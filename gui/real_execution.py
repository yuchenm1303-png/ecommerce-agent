from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .result_loader import latest_live_schema, latest_resolver_manifest


FULL_STEP3 = "__full_step3__"
PRODUCT_PHOTOS = "Product Photos"


def resolver_evidence_images(outputs: dict[str, Any]) -> list[Path]:
    """Return the exact image universe used by the Resolver.

    Product images are authoritative when capture downloaded them. The page
    screenshot is only the Resolver's fallback when no product image exists;
    combining both changes the strict source-manifest digest.
    """

    product_images = [
        Path(str(value))
        for value in outputs.get("primary_source_product_images") or []
        if str(value).strip() and Path(str(value)).is_file()
    ]
    if product_images:
        return product_images
    screenshot = Path(str(outputs.get("primary_source_screenshot") or ""))
    return [screenshot] if screenshot.is_file() else []


@dataclass(slots=True)
class RealExecutionConfig:
    read_only_run_dir: Path
    scope: str
    expected_vertical: str
    makro_cdp_port: int = 9222
    allow_save: bool = False
    upload_images: tuple[Path, ...] = ()


class RealExecutionRunner(QObject):
    """Thin GUI bridge to the canonical Makro production executor."""

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
        evidence_images = resolver_evidence_images(outputs)
        product_url = str(manifest.get("primary_product_url") or "").strip()

        required = {
            "live_schema": live_schema,
            "decision_packet": decision_packet,
            "snapshot": snapshot,
        }
        missing = [f"{name}={path}" for name, path in required.items() if not path.is_file()]
        if not evidence_images:
            missing.append("evidence_images=<missing>")
        if not product_url:
            missing.append("primary_product_url=<missing>")
        if missing:
            raise RuntimeError("真实执行 strict-rebind 输入缺失：" + " | ".join(missing))

        return {
            **required,
            "product_url": product_url,
            "evidence_images": evidence_images,
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


class RealExecutionConsole(QWidget):
    """Detailed console tab for live browser execution and report inspection."""

    def __init__(self, runner: RealExecutionRunner, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.runner = runner
        self._pending_logs: list[str] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        top = QHBoxLayout()
        self.state_label = QLabel("IDLE · waiting for completed read-only acceptance")
        self.state_label.setObjectName("consoleHint")
        self.safety_label = QLabel("WRITE opt-in · SAVE opt-in · IMAGE opt-in · QC LOCKED")
        self.safety_label.setObjectName("consoleHint")
        top.addWidget(self.state_label)
        top.addStretch(1)
        top.addWidget(self.safety_label)
        layout.addLayout(top)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("0% · idle")
        layout.addWidget(self.progress)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self._build_live_tab(), "Live Output")
        self.tabs.addTab(self._build_fields_tab(), "Field Execution")
        self.tabs.addTab(self._build_report_tab(), "Report JSON")
        layout.addWidget(self.tabs, 1)

        self.flush_timer = QTimer(self)
        self.flush_timer.setInterval(70)
        self.flush_timer.timeout.connect(self._flush_logs)

        runner.log.connect(self._queue_log)
        runner.progress_changed.connect(self._on_progress)
        runner.command_started.connect(self._on_command)
        runner.running_changed.connect(self._on_running)
        runner.completed.connect(self._on_completed)
        runner.failed.connect(self._on_failed)

    def _build_live_tab(self) -> QWidget:
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        self.command_view = QPlainTextEdit()
        self.command_view.setObjectName("consoleText")
        self.command_view.setReadOnly(True)
        self.command_view.setMaximumHeight(64)
        self.command_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.command_view.setPlaceholderText("真实 executor 命令会显示在这里")
        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("consoleText")
        self.log_view.setReadOnly(True)
        self.log_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.log_view.document().setMaximumBlockCount(12000)
        layout.addWidget(self.command_view)
        layout.addWidget(self.log_view, 1)
        return host

    def _build_fields_tab(self) -> QWidget:
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        self.summary_label = QLabel("等待真实执行报告")
        self.summary_label.setObjectName("consoleHint")
        layout.addWidget(self.summary_label)
        self.field_table = QTableWidget(0, 7)
        self.field_table.setObjectName("consoleTable")
        self.field_table.setHorizontalHeaderLabels(
            ["Section", "Field", "Mode", "Execution", "Answer", "Persisted", "Detail"]
        )
        self.field_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.field_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.field_table.setAlternatingRowColors(True)
        self.field_table.verticalHeader().setVisible(False)
        header = self.field_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.Stretch)
        layout.addWidget(self.field_table, 1)
        return host

    def _build_report_tab(self) -> QWidget:
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        self.report_view = QPlainTextEdit()
        self.report_view.setObjectName("consoleText")
        self.report_view.setReadOnly(True)
        self.report_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.report_view.setPlaceholderText("report.json 会在真实执行完成后显示")
        layout.addWidget(self.report_view)
        return host

    def _queue_log(self, line: str) -> None:
        self._pending_logs.append(line)
        if not self.flush_timer.isActive():
            self.flush_timer.start()

    def _flush_logs(self) -> None:
        if not self._pending_logs:
            self.flush_timer.stop()
            return
        self.log_view.appendPlainText("\n".join(self._pending_logs))
        self._pending_logs.clear()
        bar = self.log_view.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _on_progress(self, percent: int, text: str) -> None:
        value = max(0, min(100, int(percent)))
        self.progress.setValue(value)
        self.progress.setFormat(f"{value}% · {text}")
        self.state_label.setText(text)

    def _on_command(self, event: dict[str, Any]) -> None:
        self.command_view.setPlainText(
            f"$ {event.get('command', '')}\n"
            f"cwd={event.get('cwd', '')}\n"
            f"output={event.get('output_dir', '')}"
        )
        save = "ON" if event.get("allow_save") else "OFF"
        images = len(event.get("upload_images") or [])
        self.safety_label.setText(f"SAVE {save} · IMAGE {images} file(s) · QC LOCKED")

    def _on_running(self, running: bool) -> None:
        if running:
            self.log_view.clear()
            self.report_view.clear()
            self.field_table.setRowCount(0)
            self.summary_label.setText("真实网页执行中…")
            self.tabs.setCurrentIndex(0)
        else:
            self._flush_logs()

    def _on_completed(self, report: dict[str, Any]) -> None:
        self._flush_logs()
        self._populate_report(report)
        self.state_label.setText("COMPLETED · real browser execution report loaded")

    def _on_failed(self, message: str) -> None:
        self._flush_logs()
        self.state_label.setText("FAILED · " + message)
        self.state_label.setStyleSheet("color: #f18da0;")

    def _populate_report(self, report: dict[str, Any]) -> None:
        totals = report.get("field_totals") or {}
        photo = report.get("photo_upload") or {}
        self.summary_label.setText(
            "candidate={candidate} · attempted={attempted} · validated={validated} · "
            "persisted={persisted} · validation_failed={failed} · fill_error={errors} · "
            "sections_saved={saved} · photos={photos} · QC={qc}".format(
                candidate=totals.get("candidate_count", 0),
                attempted=totals.get("writes_attempted", 0),
                validated=totals.get("validated", 0),
                persisted=totals.get("persisted_verified", 0),
                failed=int(totals.get("validation_failed", 0))
                + int(totals.get("persisted_validation_failed", 0)),
                errors=totals.get("fill_error", 0),
                saved=report.get("section_saved", 0),
                photos=photo.get("staged", 0) if isinstance(photo, dict) else 0,
                qc=report.get("send_to_qc_clicked", False),
            )
        )

        rows: list[tuple[str, dict[str, Any], str]] = []
        for section in report.get("section_reports") or []:
            if not isinstance(section, dict):
                continue
            persisted = {
                str(item.get("label") or item.get("attribute_key") or ""): str(item.get("status") or "")
                for item in section.get("persisted_verifications") or []
                if isinstance(item, dict)
            }
            for item in section.get("results") or []:
                if isinstance(item, dict):
                    key = str(item.get("label") or item.get("attribute_key") or "")
                    rows.append((str(section.get("section") or ""), item, persisted.get(key, "")))

        self.field_table.setUpdatesEnabled(False)
        try:
            self.field_table.setRowCount(len(rows))
            for row_index, (section, item, persisted) in enumerate(rows):
                answer = item.get("answer_values") or item.get("answer") or ""
                if isinstance(answer, list):
                    answer = " + ".join(str(value) for value in answer)
                values = (
                    section,
                    str(item.get("label") or item.get("attribute_key") or ""),
                    str(item.get("preview_mode") or ""),
                    str(item.get("execution_status") or ""),
                    str(answer),
                    persisted,
                    str(item.get("detail") or ""),
                )
                for column, value in enumerate(values):
                    table_item = QTableWidgetItem(value)
                    table_item.setToolTip(value)
                    if column == 3:
                        status = values[3].casefold()
                        if "error" in status or "failed" in status:
                            table_item.setForeground(QColor("#f18da0"))
                        elif "validated" in status or "filled" in status:
                            table_item.setForeground(QColor("#8fe1b9"))
                    self.field_table.setItem(row_index, column, table_item)
        finally:
            self.field_table.setUpdatesEnabled(True)

        self.report_view.setPlainText(json.dumps(report, ensure_ascii=False, indent=2))
        self.tabs.setCurrentIndex(1)
