from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

from app.listing_images import listing_images_from_resolver_outputs
from app.required_overrides import write_required_fallback_overrides
from .async_run_journal import AsyncRunJournal
from .batch_model import (
    BATCH_WORKER_DEFAULT,
    BatchJob,
    BatchRun,
    create_batch_run,
    normalize_batch_concurrency,
    save_batch_run,
)
from .readonly_runner import RunnerConfig
from .real_execution import resolver_evidence_images
from .result_loader import (
    latest_fill_plan,
    latest_live_schema,
    latest_resolver_manifest,
    load_run_result,
)


_PHASE_LINE = re.compile(
    r"^GUI_PHASE\s+(scan|cold|hot|plan)\s+(START|COMPLETE|FAILED|SKIPPED)"
    r"(?:\s+detail=(.*))?$"
)

_PHASE_UI = {
    "scan": ("UNDERSTANDING", 25, "识别商品"),
    "cold": ("SELECTING_VERTICAL", 42, "选择类目"),
    "hot": ("SELECTING_BRAND", 58, "选择品牌"),
    "plan": ("RESOLVING", 76, "解析字段"),
}

_BATCH_LOG_PREVIEW_MS = 140
_BATCH_STATE_PUBLISH_MS = 180


class BatchController(QObject):
    """Persistent batch scheduler around the canonical single-product engine.

    Source Edge navigation is intentionally serialized. As soon as a job's exact
    supplier bytes are cached, up to ``prepare_concurrency`` independent Makro
    owned-tab jobs may run in parallel. Real execution uses the same owned tab
    token and has a separate bounded concurrency.

    Child-process stdout is durably journaled per job/stage while the control-tower
    surface receives only a rate-limited preview. This keeps complete failure
    evidence available for owner telemetry without turning console bursts into GUI
    thread filesystem work.
    """

    jobs_changed = Signal(object)
    summary_changed = Signal(object)
    state_changed = Signal(str)
    running_changed = Signal(bool)
    log = Signal(str)
    failed = Signal(str)

    def __init__(self, project_root: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.project_root = project_root.resolve()
        self.batch: BatchRun | None = None
        self.config: RunnerConfig | None = None
        self._mode = "idle"
        self._source_queue: list[str] = []
        self._prepare_queue: list[str] = []
        self._execute_queue: list[str] = []
        self._processes: dict[QProcess, tuple[str, str]] = {}
        self._buffers: dict[QProcess, str] = {}
        self._journals: dict[QProcess, AsyncRunJournal] = {}
        self._stopping = False
        self._execution_images = False
        self._pending_log_preview: dict[str, str] = {}
        self._state_dirty = False

        self._log_preview_timer = QTimer(self)
        self._log_preview_timer.setSingleShot(True)
        self._log_preview_timer.setInterval(_BATCH_LOG_PREVIEW_MS)
        self._log_preview_timer.timeout.connect(self._flush_log_preview)

        self._state_publish_timer = QTimer(self)
        self._state_publish_timer.setSingleShot(True)
        self._state_publish_timer.setInterval(_BATCH_STATE_PUBLISH_MS)
        self._state_publish_timer.timeout.connect(self._flush_persist_emit)

    @property
    def is_running(self) -> bool:
        return bool(self._processes or self._source_queue or self._prepare_queue or self._execute_queue)

    def start_prepare(
        self,
        urls: list[str],
        config: RunnerConfig,
        *,
        prepare_concurrency: int = BATCH_WORKER_DEFAULT,
    ) -> BatchRun:
        if self.is_running:
            raise RuntimeError("Batch 已在运行。")
        if not os.getenv(config.api_key_env, "").strip():
            raise ValueError(f"环境变量 {config.api_key_env} 未设置。")
        self.config = config
        self.batch = create_batch_run(
            self.project_root,
            urls,
            prepare_concurrency=prepare_concurrency,
            execute_concurrency=prepare_concurrency,
        )
        self.batch.status = "PREPARING"
        self._mode = "prepare"
        self._stopping = False
        self._source_queue = [job.job_id for job in self.batch.jobs]
        self._prepare_queue = []
        self._execute_queue = []
        self._persist_emit(immediate=True)
        self.running_changed.emit(True)
        self.state_changed.emit("批量准备中")
        self._pump_prepare()
        return self.batch

    def start_execution(
        self,
        *,
        allow_save: bool,
        upload_images: bool,
        execute_concurrency: int = BATCH_WORKER_DEFAULT,
    ) -> None:
        if self.batch is None:
            raise RuntimeError("没有已准备的 Batch。")
        if self.is_running:
            raise RuntimeError("Batch 当前仍在运行。")
        if not allow_save:
            raise ValueError("批量 Full Step 3 必须显式授权 Save + reopen。")
        ready = [job for job in self.batch.jobs if job.status == "READY"]
        if not ready:
            raise ValueError("当前 Batch 没有 READY 商品。")

        self.batch.execute_concurrency = normalize_batch_concurrency(execute_concurrency)
        self.batch.save_authorized = True
        self.batch.images_authorized = bool(upload_images)
        self.batch.send_to_qc = False
        self.batch.status = "EXECUTING"
        self._mode = "execute"
        self._stopping = False
        self._execution_images = bool(upload_images)
        self._execute_queue = [job.job_id for job in ready]
        self.running_changed.emit(True)
        self.state_changed.emit("批量真实填写中")
        self._persist_emit(immediate=True)
        self._pump_execute()

    def stop(self) -> None:
        self._stopping = True
        self._source_queue.clear()
        self._prepare_queue.clear()
        self._execute_queue.clear()
        for process in list(self._processes):
            if process.state() != QProcess.NotRunning:
                process.terminate()
        for job in self._jobs():
            if job.status not in {"READY", "DONE", "REVIEW", "FAILED"}:
                job.failure_stage = job.stage_detail or "stopped"
                job.exit_code = None
                job.status = "STOPPED"
                job.stage_detail = "stopped by user"
                job.touch()
        if self.batch is not None:
            self.batch.status = "STOPPED"
        self._persist_emit(immediate=True)

    def _jobs(self) -> list[BatchJob]:
        return self.batch.jobs if self.batch is not None else []

    def _job(self, job_id: str) -> BatchJob:
        for job in self._jobs():
            if job.job_id == job_id:
                return job
        raise KeyError(job_id)

    def _job_root(self, job: BatchJob) -> Path:
        return Path(job.run_dir).parent

    def _pump_prepare(self) -> None:
        if self.batch is None or self.config is None or self._mode != "prepare":
            return
        source_active = any(stage == "source" for _, stage in self._processes.values())
        if self._source_queue and not source_active:
            self._start_source(self._source_queue.pop(0))

        active_prepare = sum(stage == "prepare" for _, stage in self._processes.values())
        while self._prepare_queue and active_prepare < self.batch.prepare_concurrency:
            self._start_prepare_job(self._prepare_queue.pop(0))
            active_prepare += 1

        if not self._source_queue and not self._prepare_queue and not self._processes:
            self.batch.status = "PREPARED"
            self._mode = "idle"
            self._persist_emit(immediate=True)
            self.running_changed.emit(False)
            self.state_changed.emit("批量准备完成")

    def _start_source(self, job_id: str) -> None:
        assert self.config is not None
        job = self._job(job_id)
        job.operation_phase = "batch_prepare"
        root = self._job_root(job)
        cache = root / "_cache" / "source"
        output = root / "source-prefetch"
        cache.mkdir(parents=True, exist_ok=True)
        output.mkdir(parents=True, exist_ok=True)
        args = [
            "makro_batch_source.py",
            "--product-url", job.product_url,
            "--source-cdp-port", str(self.config.source_cdp_port),
            "--source-cache-dir", str(cache),
            "--output-dir", str(output),
        ]
        job.status = "CAPTURING"
        job.stage_detail = "采集商品"
        job.progress = 8
        job.error = ""
        job.failure_stage = ""
        job.exit_code = None
        job.touch()
        self._persist_emit()
        self._spawn(job_id, "source", args)

    def _start_prepare_job(self, job_id: str) -> None:
        assert self.config is not None
        job = self._job(job_id)
        job.operation_phase = "batch_prepare"
        root = self._job_root(job)
        source_cache = root / "_cache" / "source"
        semantic_cache = root / "_cache" / "semantic"
        semantic_cache.mkdir(parents=True, exist_ok=True)
        Path(job.run_dir).mkdir(parents=True, exist_ok=True)
        args = [
            "makro_batch_job.py",
            "--mode", "full",
            "--product-url", job.product_url,
            "--provider", self.config.provider,
            "--base-url", self.config.base_url,
            "--model", self.config.local_model,
            "--fact-model", self.config.fact_model,
            "--web-search-model", self.config.web_model,
            "--api-key-env", self.config.api_key_env,
            "--structured-mode", "json_object",
            "--disable-thinking",
            "--cdp-port", str(self.config.makro_cdp_port),
            "--source-cdp-port", str(self.config.source_cdp_port),
            "--source-cache-dir", str(source_cache),
            "--source-cache-ttl-seconds", "3600",
            "--semantic-cache-dir", str(semantic_cache),
            "--output-dir", job.run_dir,
        ]
        job.status = "UNDERSTANDING"
        job.stage_detail = "识别商品"
        job.progress = 22
        job.failure_stage = ""
        job.exit_code = None
        job.touch()
        self._persist_emit()
        self._spawn(job_id, "prepare", args)

    def _pump_execute(self) -> None:
        if self.batch is None or self._mode != "execute":
            return
        active = sum(stage == "execute" for _, stage in self._processes.values())
        while self._execute_queue and active < self.batch.execute_concurrency:
            self._start_execute_job(self._execute_queue.pop(0))
            active += 1
        if not self._execute_queue and not self._processes:
            self.batch.status = "COMPLETE"
            self._mode = "idle"
            self._persist_emit(immediate=True)
            self.running_changed.emit(False)
            self.state_changed.emit("Batch 执行完成")

    def _start_execute_job(self, job_id: str) -> None:
        assert self.config is not None
        job = self._job(job_id)
        job.operation_phase = "batch_execute"
        args = self._execution_args(job)
        job.status = "FILLING"
        job.stage_detail = "填写全部 READY"
        job.progress = 82
        job.error = ""
        job.failure_stage = ""
        job.exit_code = None
        job.touch()
        self._persist_emit()
        self._spawn(job_id, "execute", args)

    def _execution_args(self, job: BatchJob) -> list[str]:
        run_dir = Path(job.run_dir)
        live_schema = latest_live_schema(run_dir)
        fill_plan = latest_fill_plan(run_dir)
        resolver_manifest_path = latest_resolver_manifest(run_dir, "03-hot-resolver")
        if live_schema is None or fill_plan is None or resolver_manifest_path is None:
            raise RuntimeError(f"{job.job_id} 缺少 live schema / fill plan / resolver manifest")

        fallback_summary = write_required_fallback_overrides(fill_plan, live_schema)
        if int(fallback_summary.get("count") or 0) > 0:
            self._emit_log_now(
                f"[{job.job_id}] [required-fallback] "
                f"overrides={fallback_summary.get('path') or 'none'} "
                f"automatic={fallback_summary.get('count')} ai_calls=0"
            )

        manifest = json.loads(resolver_manifest_path.read_text(encoding="utf-8"))
        outputs = manifest.get("outputs") or {}
        decision_packet = Path(str(outputs.get("final_decisions") or ""))
        snapshot = Path(str(outputs.get("primary_source_snapshot") or ""))
        evidence = resolver_evidence_images(outputs)
        product_url = str(manifest.get("primary_product_url") or job.product_url).strip()
        if not decision_packet.is_file() or not snapshot.is_file() or not evidence:
            raise RuntimeError(f"{job.job_id} strict-rebind inputs are incomplete")
        if not job.makro_target_id:
            raise RuntimeError(f"{job.job_id} missing Makro target ownership token")

        output_root = self._job_root(job) / "real-execution"
        output_root.mkdir(parents=True, exist_ok=True)
        args = [
            "makro_execute_listing.py",
            "--decision-packet", str(decision_packet),
            "--live-schema", str(live_schema),
            "--product-url", product_url,
            "--supplier-snapshot", str(snapshot),
            "--expected-vertical", job.vertical,
            "--cdp-port", str(self.config.makro_cdp_port),
            "--makro-target-id", job.makro_target_id,
            "--all-step3",
            "--allow-section-save",
            "--output-dir", str(output_root),
        ]
        for image in evidence:
            args.extend(["--image", str(image)])
        if self._execution_images:
            listing_images = listing_images_from_resolver_outputs(outputs)
            for image in listing_images[:5]:
                args.extend(["--upload-image", str(image)])
        return args

    def _spawn(self, job_id: str, stage: str, args: list[str]) -> None:
        process = QProcess(self)
        process.setWorkingDirectory(str(self.project_root))
        process.setProcessChannelMode(QProcess.MergedChannels)
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONUTF8", "1")
        environment.insert("PYTHONIOENCODING", "utf-8")
        process.setProcessEnvironment(environment)
        self._processes[process] = (job_id, stage)
        self._buffers[process] = ""

        job = self._job(job_id)
        log_path = self._job_root(job) / "diagnostics" / f"{stage}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            log_path.write_text("", encoding="utf-8")
        except OSError:
            pass
        journal = AsyncRunJournal(log_path)
        self._journals[process] = journal

        process.readyReadStandardOutput.connect(lambda p=process: self._read_output(p))
        process.finished.connect(
            lambda exit_code, exit_status, p=process: self._finished(p, int(exit_code))
        )
        process.errorOccurred.connect(lambda error, p=process: self._process_error(p, error))
        command = subprocess.list2cmdline([sys.executable, *args])
        journal.append(f"stage={stage}")
        journal.append(f"cwd={self.project_root}")
        journal.append("$ " + command)
        self._emit_log_now(f"[{job_id} · {stage}] $ {command}")
        process.start(sys.executable, args)

    def _read_output(self, process: QProcess) -> None:
        raw = bytes(process.readAllStandardOutput())
        if not raw:
            return
        text = self._buffers.get(process, "") + raw.decode("utf-8", errors="replace")
        self._buffers[process] = ""
        parts = text.splitlines(keepends=True)
        for part in parts:
            if part.endswith(("\n", "\r")):
                self._observe_line(process, part.rstrip("\r\n"))
            else:
                self._buffers[process] = part

    def _observe_line(self, process: QProcess, line: str) -> None:
        journal = self._journals.get(process)
        if journal is not None:
            journal.append(line)
        job_id, stage = self._processes.get(process, ("?", "?"))
        self._queue_log_preview(job_id, f"[{job_id}] {line}")
        if job_id == "?":
            return
        job = self._job(job_id)
        if stage == "prepare":
            match = _PHASE_LINE.match(line.strip())
            if match:
                phase, state, detail = match.groups()
                if state == "START":
                    status, progress, label = _PHASE_UI[phase]
                    job.status = status
                    job.progress = progress
                    job.stage_detail = label
                elif state == "FAILED":
                    job.error = (detail or "prepare failed").strip()
                job.touch()
                self._persist_emit()
        elif stage == "execute":
            if line.startswith("Price, Stock and Shipping Information:"):
                job.status = "SAVING"
                job.progress = 87
                job.stage_detail = "保存价格/库存"
            elif line.startswith("Product Description:"):
                job.status = "SAVING"
                job.progress = 91
                job.stage_detail = "保存商品描述"
            elif line.startswith("Additional Description:"):
                job.status = "VERIFYING"
                job.progress = 94
                job.stage_detail = "验证附加描述"
            elif line.startswith("photos:"):
                job.status = "UPLOADING_IMAGES"
                job.progress = 96
                job.stage_detail = "上传并验证图片"
            else:
                return
            job.touch()
            self._persist_emit()

    def _close_process_journal(self, process: QProcess) -> None:
        journal = self._journals.pop(process, None)
        if journal is not None:
            journal.close()

    def _finished(self, process: QProcess, exit_code: int) -> None:
        self._read_output(process)
        tail = self._buffers.pop(process, "")
        if tail:
            self._observe_line(process, tail)
        self._close_process_journal(process)
        job_id, stage = self._processes.pop(process, ("", ""))
        if not job_id:
            return
        job = self._job(job_id)

        if self._stopping:
            job.failure_stage = job.stage_detail or stage
            job.exit_code = exit_code
            job.status = "STOPPED"
            job.stage_detail = "stopped by user"
            job.touch()
            self._persist_emit()
            self._finish_if_stopped()
            return

        if stage == "source":
            job.exit_code = exit_code
            if exit_code == 0:
                job.failure_stage = ""
                job.status = "QUEUED"
                job.stage_detail = "source cached"
                job.progress = 18
                self._prepare_queue.append(job_id)
            else:
                job.failure_stage = job.stage_detail or "采集商品"
                job.status = "FAILED"
                job.error = f"Source Capture exit code={exit_code}"
                job.stage_detail = "采集失败"
            job.touch()
            self._persist_emit()
            self._pump_prepare()
            return

        if stage == "prepare":
            job.exit_code = exit_code
            if exit_code == 0:
                try:
                    result = load_run_result(Path(job.run_dir))
                    manifest = json.loads((Path(job.run_dir) / "run-manifest.json").read_text(encoding="utf-8"))
                    job.vertical = result.vertical
                    job.brand = result.brand
                    job.ready = result.ready
                    job.blocked = result.blocked
                    summary = result.plan_summary or {}
                    job.required_blocked = int(summary.get("required_blocked") or 0)
                    job.makro_target_id = str(manifest.get("makro_target_id") or "")
                    bootstrap = manifest.get("bootstrap_source") or {}
                    job.image_count = len(bootstrap.get("product_images") or []) if isinstance(bootstrap, dict) else 0
                    hints = manifest.get("listing_hints") or {}
                    identity = hints.get("product_identity") or {} if isinstance(hints, dict) else {}
                    if isinstance(identity, dict):
                        job.product_name = str(identity.get("product_type_en") or identity.get("product_name") or "")
                    job.status = "READY" if job.ready > 0 else "REVIEW"
                    job.stage_detail = "准备完成" if job.ready > 0 else "没有 READY 字段"
                    job.failure_stage = "" if job.ready > 0 else "准备验收"
                    job.progress = 100
                    job.error = ""
                except Exception as exc:
                    job.failure_stage = "读取准备结果"
                    job.status = "FAILED"
                    job.error = f"读取准备结果失败：{exc}"
                    job.stage_detail = "结果读取失败"
            else:
                job.failure_stage = job.stage_detail or "prepare"
                job.status = "FAILED"
                job.error = self._workflow_error(job) or f"Prepare exit code={exit_code}"
                job.stage_detail = "准备失败"
            job.touch()
            self._persist_emit()
            self._pump_prepare()
            return

        if stage == "execute":
            job.exit_code = exit_code
            if exit_code == 0:
                try:
                    report = self._latest_execution_report(job)
                    payload = json.loads(report.read_text(encoding="utf-8"))
                    job.execution_report = str(report.resolve())
                    completion = payload.get("completion") or {}
                    complete = bool(
                        isinstance(completion, dict)
                        and completion.get("draft_persisted_complete")
                    )
                    job.status = "DONE" if complete else "REVIEW"
                    job.stage_detail = "保存并验证完成" if complete else "已执行，需复核"
                    job.failure_stage = "" if complete else "执行验收"
                    job.progress = 100
                    if not complete:
                        job.error = self._execution_review_reason(payload)
                    else:
                        job.error = ""
                except Exception as exc:
                    job.failure_stage = "执行报告读取"
                    job.status = "FAILED"
                    job.error = f"读取真实执行报告失败：{exc}"
                    job.stage_detail = "执行结果读取失败"
            else:
                job.failure_stage = job.stage_detail or "execute"
                job.status = "FAILED"
                job.error = f"Real execution exit code={exit_code}"
                job.stage_detail = "真实填写失败"
            job.touch()
            self._persist_emit()
            self._pump_execute()

    def _process_error(self, process: QProcess, error: QProcess.ProcessError) -> None:
        if error != QProcess.FailedToStart or process not in self._processes:
            return
        job_id, stage = self._processes.pop(process)
        self._buffers.pop(process, None)
        journal = self._journals.get(process)
        if journal is not None:
            journal.append("QProcess failed to start")
        self._close_process_journal(process)
        job = self._job(job_id)
        job.failure_stage = job.stage_detail or stage
        job.exit_code = None
        job.status = "FAILED"
        job.error = f"{stage} process failed to start"
        job.stage_detail = "子进程启动失败"
        job.touch()
        self._persist_emit()
        if stage in {"source", "prepare"}:
            self._pump_prepare()
        elif stage == "execute":
            self._pump_execute()

    def _workflow_error(self, job: BatchJob) -> str:
        path = Path(job.run_dir) / "run-manifest.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return str(payload.get("error") or "").strip()
        except Exception:
            return ""

    def _latest_execution_report(self, job: BatchJob) -> Path:
        root = self._job_root(job) / "real-execution"
        reports = [path for path in root.glob("execute-*/report.json") if path.is_file()]
        if not reports:
            raise RuntimeError("report.json not found")
        return max(reports, key=lambda path: path.stat().st_mtime_ns)

    @staticmethod
    def _execution_review_reason(payload: dict[str, Any]) -> str:
        completion = payload.get("completion") or {}
        reasons: list[str] = []
        if isinstance(completion, dict):
            if not completion.get("required_field_cards_persisted"):
                reasons.append("required sections not fully persisted")
            if not completion.get("photos_persisted"):
                reasons.append("Product Photos not persisted")
            blocked = int(completion.get("required_blocked") or 0)
            if blocked:
                reasons.append(f"required_blocked={blocked}")
        return " · ".join(reasons) or "execution incomplete"

    def _finish_if_stopped(self) -> None:
        if self._processes:
            return
        self._flush_persist_emit()
        self._flush_log_preview()
        self._mode = "idle"
        self.running_changed.emit(False)
        self.state_changed.emit("Batch 已停止")

    def _emit_log_now(self, text: str) -> None:
        self.log.emit(text)

    def _queue_log_preview(self, job_id: str, text: str) -> None:
        self._pending_log_preview[str(job_id)] = text
        if not self._log_preview_timer.isActive():
            self._log_preview_timer.start()

    def _flush_log_preview(self) -> None:
        if not self._pending_log_preview:
            return
        pending = list(self._pending_log_preview.values())
        self._pending_log_preview.clear()
        for text in pending:
            self.log.emit(text)

    def _persist_emit(self, *, immediate: bool = False) -> None:
        if self.batch is None:
            return
        self._state_dirty = True
        if immediate:
            self._flush_persist_emit()
        elif not self._state_publish_timer.isActive():
            self._state_publish_timer.start()

    def _flush_persist_emit(self) -> None:
        if not self._state_dirty or self.batch is None:
            return
        self._state_publish_timer.stop()
        self._state_dirty = False
        save_batch_run(self.batch)
        self.jobs_changed.emit(list(self.batch.jobs))
        self.summary_changed.emit(self.batch.summary())


__all__ = ["BatchController"]
