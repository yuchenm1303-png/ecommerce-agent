from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from types import MethodType
from typing import Any
from urllib.parse import urlparse

from PySide6.QtCore import QObject, QProcess, QTimer, Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .batch_model import BatchJob, normalize_batch_concurrency
from .listing_offer_support import _clean_intent, _write_intent_sidecar
from .readonly_runner import RunnerConfig


_TERMINAL = {"DONE", "REVIEW", "FAILED", "STOPPED"}
_JOB_ID_RE = re.compile(r"^JOB-(\d+)$")


@dataclass(slots=True)
class _RowControls:
    start: QPushButton
    stop: QPushButton
    delete: QPushButton


@dataclass(slots=True)
class _CardControls:
    host: QFrame
    fill: QPushButton
    stop: QPushButton
    delete: QPushButton
    hint: QLabel


class BatchIndividualControls(QObject):
    """Independent lifecycle controls for every Batch input row and owned Job.

    The BatchController remains the sole subprocess owner. This layer only adds
    row/job-scoped scheduling around that existing controller so one product can
    start, stop, be removed, or enter real execution without changing the state
    of unrelated products. Job artifacts and Makro tabs are never deleted here.
    """

    def __init__(self, workspace: QWidget) -> None:
        super().__init__(workspace)
        self.workspace = workspace
        self.window = workspace.window()
        self.controller = workspace.controller
        self.editor = getattr(workspace, "_batch_url_editor", None)
        if self.editor is None:
            raise RuntimeError("Batch independent controls require BatchUrlEditor")

        self._row_controls: dict[int, _RowControls] = {}
        self._card_controls: dict[str, _CardControls] = {}
        self._stop_requested: set[str] = set()
        self._delete_requested: set[str] = set()
        self._batch_id = ""

        self._install_job_owned_product_files()
        self._install_row_hooks()
        self._install_controller_hooks()

        for row in list(self.editor.rows):
            self._decorate_row(row)

        self.controller.jobs_changed.connect(self._sync_jobs)
        self.controller.running_changed.connect(lambda _running: self._refresh_all_rows())
        self._sync_jobs(list(getattr(workspace, "_jobs", [])))

    # ------------------------------------------------------------ row surfaces
    def _install_row_hooks(self) -> None:
        original_add_row = self.editor.add_row

        def add_row(_editor: Any, *args: Any, **kwargs: Any):
            row = original_add_row(*args, **kwargs)
            self._decorate_row(row)
            return row

        self.editor.add_row = MethodType(add_row, self.editor)

        original_set_locked = self.editor.set_locked

        def set_locked(_editor: Any, locked: bool) -> None:
            original_set_locked(bool(locked))
            self._refresh_all_rows()

        self.editor.set_locked = MethodType(set_locked, self.editor)

    def _decorate_row(self, row: Any) -> None:
        key = id(row)
        if key in self._row_controls:
            return
        layout = row.layout()
        remove = getattr(row, "remove_button", None)
        if not isinstance(layout, QHBoxLayout) or not isinstance(remove, QPushButton):
            return

        start = QPushButton("启动", row)
        start.setObjectName("batchRowStartButton")
        start.setFixedSize(48, 28)
        start.setToolTip("只启动这一条商品：Source Capture → Step 1/2 → Resolver → Fill Plan。")

        stop = QPushButton("停止", row)
        stop.setObjectName("batchRowStopButton")
        stop.setFixedSize(48, 28)
        stop.setToolTip("只停止这一条商品，不影响其他 Batch 商品继续运行。")

        # The legacy delete button used to bypass Job ownership and was disabled
        # whenever any Batch work was active. Route it through the per-job owner.
        try:
            remove.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        remove.setText("删除")
        remove.setToolTip("删除这一条商品任务/输入；磁盘日志、报告和 Makro 页面保留。")

        index = layout.indexOf(remove)
        insert_at = index if index >= 0 else layout.count()
        layout.insertWidget(insert_at, start, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.insertWidget(insert_at + 1, stop, 0, Qt.AlignmentFlag.AlignVCenter)

        start.clicked.connect(lambda _checked=False, current=row: self.start_row(current))
        stop.clicked.connect(lambda _checked=False, current=row: self.stop_row(current))
        remove.clicked.connect(lambda _checked=False, current=row: self.delete_row(current))

        style = (
            "QPushButton#batchRowStartButton, QPushButton#batchRowStopButton {"
            " min-height:28px; max-height:28px; border-radius:8px; padding:0 7px;"
            " font-size:11px; font-weight:730; border:1px solid rgba(255,255,255,28);"
            " background:rgba(12,34,54,108); color:rgba(236,247,255,220); }"
            "QPushButton#batchRowStartButton:hover {"
            " border-color:rgba(143,231,194,105); background:rgba(44,126,94,90); }"
            "QPushButton#batchRowStopButton:hover {"
            " border-color:rgba(255,188,139,100); background:rgba(139,86,42,84); }"
            "QPushButton#batchRowStartButton:disabled, QPushButton#batchRowStopButton:disabled {"
            " color:rgba(235,245,255,62); background:rgba(24,37,49,40);"
            " border-color:rgba(255,255,255,12); }"
        )
        row.setStyleSheet((row.styleSheet() or "") + style)
        self._row_controls[key] = _RowControls(start=start, stop=stop, delete=remove)
        self._refresh_row(row)

    # --------------------------------------------------------- controller hooks
    def _install_controller_hooks(self) -> None:
        self._original_start_prepare = self.controller.start_prepare

        def start_prepare(_controller: Any, urls: list[str], config: Any, **kwargs: Any):
            rows = self._enabled_rows_snapshot()
            batch = self._original_start_prepare(urls, config, **kwargs)
            self._batch_id = str(batch.batch_id)
            self._bind_bulk_rows(rows, list(batch.jobs))
            return batch

        self.controller.start_prepare = MethodType(start_prepare, self.controller)

        self._original_finished = self.controller._finished

        def finished(_controller: Any, process: Any, exit_code: int) -> None:
            ownership = _controller._processes.get(process, ("", ""))
            job_id, stage = ownership
            self._original_finished(process, exit_code)

            if job_id in self._stop_requested:
                self._stop_requested.discard(job_id)
                job = self._job(job_id)
                if job is not None:
                    job.status = "STOPPED"
                    job.stage_detail = "已停止 · 仅当前商品"
                    job.error = ""
                    job.touch()

            if job_id in self._delete_requested:
                self._delete_requested.discard(job_id)
                self._remove_job_record(job_id)
            elif job_id:
                self.controller._persist_emit()

            self._pump_independent_lanes()
            self._settle_if_idle(stage)

        self.controller._finished = MethodType(finished, self.controller)

    # --------------------------------------------------------------- start row
    def start_row(self, row: Any) -> None:
        if row not in self.editor.rows or not bool(row.is_enabled()):
            return
        url = str(row.url() or "").strip()
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            QMessageBox.warning(self.workspace, "链接无效", "请先填写完整的 http(s) 商品链接。")
            return

        current = self._row_job(row)
        if current is not None and self._job_busy_or_ready(current):
            QMessageBox.information(self.workspace, "这一条已经启动", f"{current.job_id} 当前状态为 {current.status}。")
            return

        config = self._runtime_config(url)
        try:
            if self.controller.batch is None:
                # Use the full installed start_prepare chain for the first job so
                # AI settings, listing intent, supplemental files and future
                # controller integrations still run through their canonical path.
                batch = self._original_start_prepare(
                    [url],
                    config,
                    prepare_concurrency=int(self.workspace.worker_count.value()),
                )
                job = batch.jobs[0]
                self._batch_id = str(batch.batch_id)
            else:
                job = self._append_job(url, config)

            row._individual_job_id = str(job.job_id)
            self._bind_job_context(row, job)
            self.workspace.open_batch_button.setEnabled(True)
            self.controller.state_changed.emit(f"{job.job_id} · 单独启动 · 其他商品不受影响")
            self.controller._persist_emit(immediate=True)
            self._pump_independent_lanes()
            self._refresh_row(row)
        except Exception as exc:
            QMessageBox.critical(self.workspace, "单独启动失败", str(exc))

    def _append_job(self, url: str, config: RunnerConfig) -> BatchJob:
        batch = self.controller.batch
        if batch is None:
            raise RuntimeError("Batch session is unavailable")
        if self.controller.config is None:
            self.controller.config = config
        elif self.controller.is_running:
            # Running products own one shared browser/API runtime contract. New
            # rows join that exact runtime rather than mutating it underneath them.
            config = self.controller.config
        else:
            self.controller.config = config

        batch.prepare_concurrency = normalize_batch_concurrency(int(self.workspace.worker_count.value()))
        job_id = self._next_job_id()
        root = Path(batch.root_dir).resolve() / "jobs" / job_id
        root.mkdir(parents=True, exist_ok=False)
        job = BatchJob(
            job_id=job_id,
            product_url=url,
            run_dir=str((root / "workflow").resolve()),
        )
        batch.jobs.append(job)
        batch.status = "PREPARING"
        self.controller._stopping = False
        if job_id not in self.controller._source_queue:
            self.controller._source_queue.append(job_id)
        if self.controller._mode == "idle":
            self.controller._mode = "prepare"
            self.controller.running_changed.emit(True)
        return job

    def _next_job_id(self) -> str:
        batch = self.controller.batch
        if batch is None:
            return "JOB-001"
        numbers: set[int] = set()
        for job in batch.jobs:
            match = _JOB_ID_RE.match(str(job.job_id))
            if match:
                numbers.add(int(match.group(1)))
        jobs_root = Path(batch.root_dir).resolve() / "jobs"
        if jobs_root.is_dir():
            for path in jobs_root.iterdir():
                match = _JOB_ID_RE.match(path.name)
                if match:
                    numbers.add(int(match.group(1)))
        return f"JOB-{(max(numbers) + 1 if numbers else 1):03d}"

    def _runtime_config(self, url: str) -> RunnerConfig:
        return RunnerConfig(
            product_url=url,
            makro_cdp_port=int(self.workspace.makro_port.value()),
            source_cdp_port=int(self.workspace.source_port.value()),
            source_use_current_page=False,
        )

    # --------------------------------------------------------------- stop/delete
    def stop_row(self, row: Any) -> None:
        job = self._row_job(row)
        if job is not None:
            self.stop_job(str(job.job_id))

    def stop_job(self, job_id: str) -> None:
        job = self._job(job_id)
        if job is None or str(job.status) in _TERMINAL:
            return

        removed_from_queue = self._remove_from_all_queues(job_id)
        process = self._process_for_job(job_id)
        job.status = "STOPPED"
        job.stage_detail = "正在停止 · 仅当前商品"
        job.error = ""
        job.touch()

        if process is None:
            job.stage_detail = "已停止 · 仅当前商品"
            self.controller._persist_emit(immediate=True)
            self._pump_independent_lanes()
            self._settle_if_idle(removed_from_queue or "")
            return

        self._stop_requested.add(job_id)
        process.terminate()
        QTimer.singleShot(2500, lambda p=process: self._kill_if_still_running(p))
        self.controller._persist_emit(immediate=True)

    def delete_row(self, row: Any) -> None:
        job = self._row_job(row)
        if job is None:
            self.editor.remove_row(row)
            self._row_controls.pop(id(row), None)
            return

        active = self._job_is_scheduled(str(job.job_id))
        message = (
            "这一条商品仍在运行/排队。删除会先只停止该商品，再从当前 Batch 工作区移除。\n\n"
            if active
            else "将从当前 Batch 工作区移除这一条商品。\n\n"
        )
        answer = QMessageBox.question(
            self.workspace,
            f"删除 {job.job_id}",
            message + "Job 目录、日志、报告和已经存在的 Makro 页面都会保留。\n确认删除？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        row._delete_after_job = True
        self.delete_job(str(job.job_id))
        if self._job(str(job.job_id)) is None:
            self._remove_row_force(row)

    def delete_job(self, job_id: str) -> None:
        job = self._job(job_id)
        if job is None:
            return
        if self._job_is_scheduled(job_id):
            self._delete_requested.add(job_id)
            self.stop_job(job_id)
            return
        self._remove_from_all_queues(job_id)
        self._remove_job_record(job_id)
        self._pump_independent_lanes()
        self._settle_if_idle("")

    def _kill_if_still_running(self, process: Any) -> None:
        if process in self.controller._processes and process.state() != QProcess.NotRunning:
            process.kill()

    # ------------------------------------------------------------ real execution
    def start_job_execution(self, job_id: str) -> None:
        job = self._job(job_id)
        if job is None:
            return
        if str(job.status) != "READY":
            QMessageBox.information(
                self.workspace,
                "当前任务不可填写",
                f"{job_id} 当前状态为 {job.status}；只有 READY 商品可以单独真实填写。",
            )
            return
        if self.controller.config is None:
            QMessageBox.warning(self.workspace, "缺少 Batch 配置", "请先完成该商品准备。")
            return
        if self._job_is_scheduled(job_id):
            return

        answer = QMessageBox.question(
            self.workspace,
            f"确认单独真实填写 · {job_id}",
            "只执行这一件商品的 Full Step 3。\n\n"
            "其他商品继续运行。\n"
            "Save + reopen: ON\n"
            "Product Photos: ON\n"
            "Send to QC: LOCKED / FALSE\n\n"
            "确认开始？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        batch = self.controller.batch
        if batch is None:
            return
        was_running = self.controller.is_running
        batch.save_authorized = True
        batch.images_authorized = True
        batch.send_to_qc = False
        batch.execute_concurrency = normalize_batch_concurrency(int(self.workspace.worker_count.value()))
        batch.status = "EXECUTING"
        self.controller._execution_images = True
        self.controller._stopping = False
        if self.controller._mode == "idle":
            self.controller._mode = "execute"
        if job_id not in self.controller._execute_queue:
            self.controller._execute_queue.append(job_id)
        if not was_running:
            self.controller.running_changed.emit(True)
        self.controller.state_changed.emit(f"{job_id} · 单独真实填写 · 其他商品继续")
        self.controller._persist_emit(immediate=True)
        self._pump_independent_lanes()

    # ------------------------------------------------------------ card surfaces
    def _decorate_card(self, card: QWidget, job_id: str) -> None:
        if job_id in self._card_controls:
            return
        root = card.layout()
        details = getattr(card, "details_box", None)
        if not isinstance(root, QVBoxLayout):
            return

        host = QFrame(card)
        host.setObjectName("batchIndividualControlStrip")
        row = QHBoxLayout(host)
        row.setContentsMargins(10, 7, 10, 7)
        row.setSpacing(8)

        label = QLabel("JOB CONTROL")
        label.setObjectName("sectionEyebrow")
        hint = QLabel("独立任务控制")
        hint.setObjectName("cardHint")
        hint.setWordWrap(True)
        fill = QPushButton("单独填写")
        fill.setObjectName("primaryButton")
        stop = QPushButton("停止")
        stop.setObjectName("quietButton")
        delete = QPushButton("删除")
        delete.setObjectName("dangerButton")

        fill.clicked.connect(lambda _checked=False, jid=job_id: self.start_job_execution(jid))
        stop.clicked.connect(lambda _checked=False, jid=job_id: self.stop_job(jid))
        delete.clicked.connect(lambda _checked=False, jid=job_id: self._confirm_delete_job(jid))

        row.addWidget(label)
        row.addWidget(hint, 1)
        row.addWidget(fill)
        row.addWidget(stop)
        row.addWidget(delete)

        index = root.indexOf(details) if details is not None else root.count()
        root.insertWidget(index if index >= 0 else root.count(), host)
        self._card_controls[job_id] = _CardControls(host, fill, stop, delete, hint)

        # BatchLifecycle's legacy terminal-only remove button becomes redundant.
        lifecycle = getattr(self.workspace, "_batch_lifecycle", None)
        old = getattr(lifecycle, "_remove_buttons", {}).get(job_id) if lifecycle is not None else None
        if isinstance(old, QPushButton):
            old.hide()

    def _confirm_delete_job(self, job_id: str) -> None:
        job = self._job(job_id)
        if job is None:
            return
        active = self._job_is_scheduled(job_id)
        answer = QMessageBox.question(
            self.workspace,
            f"删除 {job_id}",
            ("任务仍在运行/排队，将先停止这一件商品。\n\n" if active else "")
            + "只从当前 Batch 工作区删除；磁盘产物和 Makro 页面保留。\n确认删除？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self.delete_job(job_id)

    # ------------------------------------------------------------ ownership bind
    def _enabled_rows_snapshot(self) -> list[Any]:
        return [
            row
            for row in list(self.editor.rows)
            if bool(row.is_enabled()) and str(row.url() or "").strip()
        ]

    def _bind_bulk_rows(self, rows: list[Any], jobs: list[Any]) -> None:
        for row, job in zip(rows, jobs):
            row._individual_job_id = str(job.job_id)
            self._bind_job_context(row, job)
        self._refresh_all_rows()

    def _bind_job_context(self, row: Any, job: Any) -> None:
        job_id = str(job.job_id)
        offer = getattr(row, "offer_input", None)
        intent = _clean_intent(offer.text() if isinstance(offer, QLineEdit) else "")
        owned_intents = getattr(self.controller, "_listing_offer_intent_by_job_id", None)
        if not isinstance(owned_intents, dict):
            owned_intents = {}
            self.controller._listing_offer_intent_by_job_id = owned_intents
        owned_intents[job_id] = intent
        _write_intent_sidecar(Path(job.run_dir).parent, intent)

        layer = getattr(self.window, "_batch_product_files_ui", None)
        files: tuple[Path, ...] = ()
        if layer is not None:
            row_files = getattr(layer, "_row_files", None)
            if callable(row_files):
                files = tuple(row_files(row))
            writer = getattr(layer, "_write_job_sidecar", None)
            if callable(writer):
                writer(job, files)
        mapping = getattr(self.controller, "_supplemental_product_files_by_job_id", None)
        if not isinstance(mapping, dict):
            mapping = {}
            self.controller._supplemental_product_files_by_job_id = mapping
        mapping[job_id] = files

    def _install_job_owned_product_files(self) -> None:
        layer = getattr(self.window, "_batch_product_files_ui", None)
        if layer is None or bool(getattr(layer, "_job_owned_files_installed", False)):
            return
        original = layer._job_files

        def job_files(_layer: Any, job: Any) -> tuple[Path, ...]:
            mapping = getattr(self.controller, "_supplemental_product_files_by_job_id", None)
            job_id = str(getattr(job, "job_id", ""))
            if isinstance(mapping, dict) and job_id in mapping:
                return tuple(Path(value).resolve() for value in mapping[job_id])
            return tuple(original(job))

        layer._job_files = MethodType(job_files, layer)
        layer._job_owned_files_installed = True

    # -------------------------------------------------------------- sync helpers
    def _sync_jobs(self, jobs: list[Any]) -> None:
        batch = self.controller.batch
        batch_id = str(batch.batch_id) if batch is not None else ""
        if batch_id != self._batch_id:
            self._batch_id = batch_id
            if not batch_id:
                for row in list(self.editor.rows):
                    row._individual_job_id = ""

        job_by_id = {str(job.job_id): job for job in jobs}
        claimed = {
            str(getattr(row, "_individual_job_id", "") or "")
            for row in list(self.editor.rows)
            if str(getattr(row, "_individual_job_id", "") or "")
        }

        # Restore row ownership when a bulk start created cards before this layer
        # observed the explicit binding. Duplicate URLs remain occurrence-ordered.
        for job in jobs:
            job_id = str(job.job_id)
            if job_id in claimed:
                continue
            for row in list(self.editor.rows):
                if str(getattr(row, "_individual_job_id", "") or ""):
                    continue
                if str(row.url() or "").strip() != str(job.product_url).strip():
                    continue
                row._individual_job_id = job_id
                self._bind_job_context(row, job)
                claimed.add(job_id)
                break

        for job_id, job in job_by_id.items():
            card = getattr(self.workspace, "_job_cards", {}).get(job_id)
            if card is not None:
                self._decorate_card(card, job_id)
                self._refresh_card(job)

        for row in list(self.editor.rows):
            job_id = str(getattr(row, "_individual_job_id", "") or "")
            if job_id and job_id not in job_by_id:
                if bool(getattr(row, "_delete_after_job", False)):
                    self._remove_row_force(row)
                    continue
                row._individual_job_id = ""
            self._refresh_row(row)

        for job_id in list(self._card_controls):
            if job_id not in job_by_id:
                self._card_controls.pop(job_id, None)

    def _refresh_all_rows(self) -> None:
        for row in list(self.editor.rows):
            self._refresh_row(row)

    def _refresh_row(self, row: Any) -> None:
        controls = self._row_controls.get(id(row))
        if controls is None:
            return
        job = self._row_job(row)
        scheduled = bool(job is not None and self._job_is_scheduled(str(job.job_id)))
        terminal = bool(job is not None and str(job.status) in _TERMINAL)
        ready = bool(job is not None and str(job.status) == "READY")
        valid_url = bool(str(row.url() or "").strip())
        enabled = bool(row.is_enabled())

        controls.start.setEnabled(enabled and valid_url and (job is None or terminal))
        controls.stop.setEnabled(bool(job is not None and (scheduled or ready or not terminal)))
        controls.delete.setEnabled(valid_url or job is not None)
        if job is None:
            controls.start.setText("启动")
            controls.stop.setEnabled(False)
        elif scheduled:
            controls.start.setText("运行中")
        elif terminal:
            controls.start.setText("重启")
        elif ready:
            controls.start.setText("已准备")
        else:
            controls.start.setText("启动")

    def _refresh_card(self, job: Any) -> None:
        controls = self._card_controls.get(str(job.job_id))
        if controls is None:
            return
        scheduled = self._job_is_scheduled(str(job.job_id))
        terminal = str(job.status) in _TERMINAL
        controls.fill.setEnabled(str(job.status) == "READY" and not scheduled)
        controls.stop.setEnabled(scheduled or (not terminal and str(job.status) == "READY"))
        controls.delete.setEnabled(True)
        if scheduled:
            controls.hint.setText("运行中 · 只控制这一件商品")
        elif str(job.status) == "READY":
            controls.hint.setText("READY · 可单独真实填写")
        elif terminal:
            controls.hint.setText(f"{job.status} · 可删除，历史产物保留")
        else:
            controls.hint.setText("独立任务控制")

        lifecycle = getattr(self.workspace, "_batch_lifecycle", None)
        old = getattr(lifecycle, "_remove_buttons", {}).get(str(job.job_id)) if lifecycle is not None else None
        if isinstance(old, QPushButton):
            old.hide()

    # ------------------------------------------------------------ queue/process
    def _pump_independent_lanes(self) -> None:
        batch = self.controller.batch
        if batch is None or self.controller.config is None:
            return

        source_active = any(stage == "source" for _, stage in self.controller._processes.values())
        if self.controller._source_queue and not source_active:
            self.controller._start_source(self.controller._source_queue.pop(0))

        active_prepare = sum(stage == "prepare" for _, stage in self.controller._processes.values())
        while self.controller._prepare_queue and active_prepare < batch.prepare_concurrency:
            self.controller._start_prepare_job(self.controller._prepare_queue.pop(0))
            active_prepare += 1

        active_execute = sum(stage == "execute" for _, stage in self.controller._processes.values())
        while self.controller._execute_queue and active_execute < batch.execute_concurrency:
            self.controller._start_execute_job(self.controller._execute_queue.pop(0))
            active_execute += 1

    def _settle_if_idle(self, _stage: str) -> None:
        if self.controller.is_running:
            return
        if self.controller._mode == "idle":
            return
        self.controller._mode = "idle"
        batch = self.controller.batch
        if batch is not None and str(batch.status) != "STOPPED":
            if any(str(job.status) == "READY" for job in batch.jobs):
                batch.status = "PREPARED"
            elif batch.jobs and all(str(job.status) in _TERMINAL for job in batch.jobs):
                batch.status = "COMPLETE"
            else:
                batch.status = "IDLE"
        self.controller._persist_emit(immediate=True)
        self.controller.running_changed.emit(False)
        self.controller.state_changed.emit("Batch 空闲 · 可继续单独启动商品")

    def _remove_from_all_queues(self, job_id: str) -> str:
        removed = ""
        for name, stage in (
            ("_source_queue", "source"),
            ("_prepare_queue", "prepare"),
            ("_execute_queue", "execute"),
        ):
            queue = getattr(self.controller, name, None)
            if not isinstance(queue, list):
                continue
            while job_id in queue:
                queue.remove(job_id)
                removed = stage
        return removed

    def _process_for_job(self, job_id: str) -> Any | None:
        for process, (owned_job_id, _stage) in self.controller._processes.items():
            if str(owned_job_id) == str(job_id):
                return process
        return None

    def _job_is_scheduled(self, job_id: str) -> bool:
        return bool(
            self._process_for_job(job_id) is not None
            or job_id in self.controller._source_queue
            or job_id in self.controller._prepare_queue
            or job_id in self.controller._execute_queue
        )

    def _job_busy_or_ready(self, job: Any) -> bool:
        return self._job_is_scheduled(str(job.job_id)) or str(job.status) not in _TERMINAL

    def _job(self, job_id: str) -> Any | None:
        batch = self.controller.batch
        if batch is None:
            return None
        return next((job for job in batch.jobs if str(job.job_id) == str(job_id)), None)

    def _row_job(self, row: Any) -> Any | None:
        job_id = str(getattr(row, "_individual_job_id", "") or "")
        return self._job(job_id) if job_id else None

    def _remove_job_record(self, job_id: str) -> None:
        batch = self.controller.batch
        if batch is None:
            return
        self._remove_from_all_queues(job_id)
        batch.jobs[:] = [job for job in batch.jobs if str(job.job_id) != str(job_id)]

        for name in (
            "_listing_offer_intent_by_job_id",
            "_supplemental_product_files_by_job_id",
        ):
            mapping = getattr(self.controller, name, None)
            if isinstance(mapping, dict):
                mapping.pop(job_id, None)

        pending = getattr(self.workspace, "_pending_logs", None)
        if isinstance(pending, dict):
            pending.pop(job_id, None)

        support = getattr(self.window, "_listing_offer_support", None)
        panels = getattr(support, "_batch_required_panels", None)
        if isinstance(panels, dict):
            panels.pop(job_id, None)

        self.controller._persist_emit(immediate=True)
        if not batch.jobs:
            self.controller.state_changed.emit("当前批次为空 · 可继续编辑或单独启动链接")

    def _remove_row_force(self, row: Any) -> None:
        if row not in self.editor.rows:
            return
        self.editor.rows.remove(row)
        self.editor.rows_layout.removeWidget(row)
        self._row_controls.pop(id(row), None)
        row.deleteLater()
        self.editor._ensure_min_rows()
        self.editor._renumber()
        self.editor._refresh_summary()


def install_batch_individual_controls(workspace: QWidget) -> BatchIndividualControls:
    existing = getattr(workspace, "_batch_individual_controls", None)
    if isinstance(existing, BatchIndividualControls):
        return existing
    manager = BatchIndividualControls(workspace)
    workspace._batch_individual_controls = manager
    return manager


__all__ = ["BatchIndividualControls", "install_batch_individual_controls"]
