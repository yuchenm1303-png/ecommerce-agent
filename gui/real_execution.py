from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

from .result_loader import latest_live_schema, latest_resolver_manifest


FULL_STEP3 = "__full_step3__"
PRODUCT_PHOTOS = "Product Photos"


@dataclass(slots=True)
class RealExecutionConfig:
    read_only_run_dir: Path
    scope: str
    expected_vertical: str
    makro_cdp_port: int = 9222
    allow_save: bool = False
    upload_images: tuple[Path, ...] = ()


class RealExecutionRunner(QObject):
    """Thin presentation-neutral bridge to the canonical Makro production executor."""

    log = Signal(str)
    progress_changed = Signal(int, str)
    running_changed = Signal(bool)
    command_started = Signal(object)
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, project_root: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.project_root = project_root.resolve()
        self.process: QProcess | None = None
        self.output_root: Path | None = None
        self.config: RealExecutionConfig | None = None
        self._stdout_tail = ""
        self._started_at = 0.0
        self._section_milestones: set[str] = set()

    @property
    def is_running(self) -> bool:
        return self.process is not None and self.process.state() != QProcess.NotRunning

    def start(self, config: RealExecutionConfig) -> None:
        if self.is_running:
            raise RuntimeError("真实执行已经在运行。")
        self._validate_config(config)
        prepared = self._prepare_inputs(config)

        self.config = config
        self.output_root = config.read_only_run_dir.resolve() / "05-real-execution"
        self.output_root.mkdir(parents=True, exist_ok=True)
        self._stdout_tail = ""
        self._section_milestones.clear()
        self._started_at = time.monotonic()

        args = [
            "makro_execute_listing.py",
            "--decision-packet",
            str(prepared["decision_packet"]),
            "--live-schema",
            str(prepared["live_schema"]),
            "--product-url",
            str(prepared["product_url"]),
            "--supplier-snapshot",
            str(prepared["snapshot"]),
            "--image",
            str(prepared["screenshot"]),
            "--expected-vertical",
            config.expected_vertical,
            "--cdp-port",
            str(config.makro_cdp_port),
            "--output-dir",
            str(self.output_root),
        ]
        for image in prepared["evidence_images"]:
            args.extend(["--image", str(image)])

        if config.scope == FULL_STEP3:
            args.append("--all-step3")
        else:
            args.extend(["--section", config.scope])
        if config.allow_save:
            args.append("--allow-section-save")

        for image in config.upload_images:
            args.extend(["--upload-image", str(image)])

        process = QProcess(self)
        self.process = process
        process.setWorkingDirectory(str(self.project_root))
        process.setProcessChannelMode(QProcess.MergedChannels)
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONUTF8", "1")
        environment.insert("PYTHONIOENCODING", "utf-8")
        process.setProcessEnvironment(environment)
        process.readyReadStandardOutput.connect(self._read_output)
        process.finished.connect(self._process_finished)
        process.errorOccurred.connect(self._process_error)

        full_argv = [sys.executable, *args]
        command = subprocess.list2cmdline(full_argv)
        self.command_started.emit(
            {
                "command": command,
                "cwd": str(self.project_root),
                "output_dir": str(self.output_root),
                "scope": config.scope,
                "allow_save": config.allow_save,
                "upload_images": [str(path) for path in config.upload_images],
                "send_to_qc": False,
            }
        )
        self.running_changed.emit(True)
        self.progress_changed.emit(5, "pre-write strict rebind / live schema verification")
        self._emit_log("===== REAL MAKRO EXECUTION =====")
        self._emit_log(f"scope={config.scope}")
        self._emit_log(f"allow_save={config.allow_save}")
        self._emit_log(f"upload_images={len(config.upload_images)}")
        self._emit_log("send_to_qc=False (repository policy lock)")
        self._emit_log("$ " + command)
        self._emit_log(f"cwd={self.project_root}")
        process.start(sys.executable, args)

    def stop(self) -> None:
        if self.process is None or self.process.state() == QProcess.NotRunning:
            return
        self._emit_log("Stop requested. Terminating real execution subprocess...")
        self.process.terminate()
        if not self.process.waitForFinished(2500):
            self.process.kill()

    def _validate_config(self, config: RealExecutionConfig) -> None:
        root = config.read_only_run_dir.resolve()
        if not root.is_dir():
            raise ValueError("没有可用的 read-only acceptance run。请先完成只读测试。")
        if not config.expected_vertical.strip():
            raise ValueError("expected vertical 不能为空。")
        if not (1 <= int(config.makro_cdp_port) <= 65535):
            raise ValueError("Makro CDP 端口无效。")
        if config.scope == FULL_STEP3 and not config.allow_save:
            raise ValueError("Full Step 3 是持久化验收，必须显式开启 Save。")
        for path in config.upload_images:
            if not path.is_file():
                raise ValueError(f"待上传图片不存在：{path}")

    def _prepare_inputs(self, config: RealExecutionConfig) -> dict[str, Any]:
        run_dir = config.read_only_run_dir.resolve()
        live_schema = latest_live_schema(run_dir)
        hot_manifest_path = latest_resolver_manifest(run_dir, "03-hot-resolver")
        plans = list((run_dir / "04-fill-plan").glob("plan-*/fill-plan.json"))
        if live_schema is None or hot_manifest_path is None or not plans:
            raise RuntimeError(
                "read-only acceptance 产物不完整；必须先完成 fresh schema → cold → hot → Fill Plan。"
            )

        manifest = json.loads(hot_manifest_path.read_text(encoding="utf-8"))
        outputs = manifest.get("outputs") or {}
        decision_packet = Path(str(outputs.get("final_decisions") or ""))
        snapshot = Path(str(outputs.get("primary_source_snapshot") or ""))
        screenshot = Path(str(outputs.get("primary_source_screenshot") or ""))
        evidence_images = [
            Path(str(value))
            for value in outputs.get("primary_source_product_images") or []
            if str(value).strip()
        ]
        product_url = str(manifest.get("primary_product_url") or "").strip()

        required = {
            "live_schema": live_schema,
            "decision_packet": decision_packet,
            "snapshot": snapshot,
            "screenshot": screenshot,
        }
        missing = [f"{name}={path}" for name, path in required.items() if not path.is_file()]
        if not product_url:
            missing.append("primary_product_url=<missing>")
        if missing:
            raise RuntimeError("真实执行 strict-rebind 输入缺失：" + " | ".join(missing))

        return {
            **required,
            "product_url": product_url,
            "evidence_images": [path for path in evidence_images if path.is_file()],
        }

    def _read_output(self) -> None:
        if self.process is None:
            return
        raw = bytes(self.process.readAllStandardOutput())
        if not raw:
            return
        text = self._stdout_tail + raw.decode("utf-8", errors="replace")
        self._stdout_tail = ""
        for part in text.splitlines(keepends=True):
            if part.endswith(("\n", "\r")):
                line = part.rstrip("\r\n")
                self._emit_log(line)
                self._observe_progress(line)
            else:
                self._stdout_tail = part

    def _observe_progress(self, line: str) -> None:
        if any(
            marker in line
            for marker in (
                "MAKRO STEP 3 DIRECT ACCEPTANCE",
                "MAKRO DIRECT SECTION PREVIEW",
                "MAKRO DIRECT SECTION PERSISTED ACCEPTANCE",
            )
        ):
            self.progress_changed.emit(15, "pre-write checks passed · browser execution started")
            return

        sections = (
            "Price, Stock and Shipping Information",
            "Product Description",
            "Additional Description",
        )
        for section in sections:
            if line.startswith(section + ":") and section not in self._section_milestones:
                self._section_milestones.add(section)
                value = 20 + len(self._section_milestones) * 20 if self.config and self.config.scope == FULL_STEP3 else 85
                self.progress_changed.emit(value, f"section complete · {section}")
                return

        if line.startswith("photos:"):
            self.progress_changed.emit(88, "Product Photos stage complete")
        elif "ACCEPTANCE COMPLETE" in line or "PREVIEW READY" in line:
            self.progress_changed.emit(95, "browser execution complete · writing report")

    def _flush_tail(self) -> None:
        if self._stdout_tail:
            line = self._stdout_tail
            self._stdout_tail = ""
            self._emit_log(line)
            self._observe_progress(line)

    def _process_finished(self, exit_code: int, _exit_status: QProcess.ExitStatus) -> None:
        self._read_output()
        self._flush_tail()
        self.process = None
        elapsed = max(0.0, time.monotonic() - self._started_at)

        if exit_code != 0:
            message = f"真实执行退出码={exit_code}，elapsed={elapsed:.2f}s。请查看 Real Execution 日志。"
            self.progress_changed.emit(0, "failed")
            self.running_changed.emit(False)
            self.failed.emit(message)
            return

        try:
            report_path = self._latest_report()
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["_report_path"] = str(report_path.resolve())
            report["_elapsed_s"] = elapsed
        except Exception as exc:
            self.running_changed.emit(False)
            self.failed.emit(f"真实执行结束，但读取 report.json 失败：{exc}")
            return

        self.progress_changed.emit(100, f"complete · {elapsed:.2f}s")
        self.running_changed.emit(False)
        self.completed.emit(report)

    def _process_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.FailedToStart:
            self.process = None
            self.running_changed.emit(False)
            self.failed.emit("真实执行 Python 子进程启动失败。")

    def _latest_report(self) -> Path:
        if self.output_root is None:
            raise RuntimeError("真实执行 output root 未初始化。")
        reports = [path for path in self.output_root.glob("execute-*/report.json") if path.is_file()]
        if not reports:
            raise RuntimeError(f"未找到执行报告：{self.output_root}")
        return max(reports, key=lambda path: path.stat().st_mtime_ns)

    def _emit_log(self, line: str) -> None:
        self.log.emit(line)
        if self.output_root is not None:
            with (self.output_root / "real-execution-gui.log").open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
