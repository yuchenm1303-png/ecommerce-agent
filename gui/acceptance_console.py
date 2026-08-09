from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


_PHASES = (
    ("scan", "01", "Fresh Schema"),
    ("cold", "02", "Cold Resolver"),
    ("hot", "03", "Hot / Cache"),
    ("plan", "04", "Fill Plan"),
)

_STATUS_COLOR = {
    "waiting": "rgba(255,255,255,115)",
    "running": "#ffffff",
    "completed": "#8fe1b9",
    "failed": "#f18da0",
    "cancelled": "#f4cb7a",
}

_CONSOLE_STYLE = r"""
QFrame#acceptanceConsole {
    background: transparent;
    border: 0;
}
QFrame#consolePhaseUnit {
    background-color: rgba(0,0,0,42);
    border: 1px solid rgba(255,255,255,18);
    border-radius: 6px;
}
QLabel#consoleEyebrow {
    color: rgba(255,255,255,135);
    font-size: 9px;
    font-weight: 650;
    letter-spacing: 1px;
}
QLabel#consoleTitle {
    color: white;
    font-size: 14px;
    font-weight: 700;
}
QLabel#consoleHint {
    color: rgba(255,255,255,165);
    font-size: 10px;
}
QProgressBar {
    min-height: 18px;
    max-height: 18px;
    border: 0;
    border-radius: 6px;
    background-color: rgba(0,0,0,58);
    color: rgba(255,255,255,220);
    text-align: center;
    font-size: 9px;
    font-weight: 700;
}
QProgressBar::chunk {
    border-radius: 6px;
    background-color: rgba(255,255,255,128);
}
QTabWidget::pane {
    border: 1px solid rgba(255,255,255,16);
    border-radius: 6px;
    background-color: rgba(0,0,0,24);
    top: -1px;
}
QTabBar::tab {
    color: rgba(255,255,255,165);
    background: rgba(0,0,0,34);
    border: 0;
    padding: 7px 13px;
    margin-right: 2px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
}
QTabBar::tab:selected {
    color: white;
    background: rgba(0,0,0,78);
}
QTabBar::tab:hover {
    color: white;
    background: rgba(0,0,0,58);
}
QTableWidget#consoleTable {
    background-color: rgba(0,0,0,38);
    alternate-background-color: rgba(255,255,255,7);
    border: 0;
    border-radius: 4px;
    gridline-color: rgba(255,255,255,12);
}
QPlainTextEdit#consoleText {
    background-color: rgba(0,0,0,54);
    border: 0;
    border-radius: 4px;
    padding: 8px;
    font-family: "Cascadia Mono", "Consolas", monospace;
    font-size: 11px;
    color: rgba(255,255,255,220);
}
"""


def _seconds(value: float) -> str:
    if value < 10:
        return f"{value:.2f}s"
    if value < 60:
        return f"{value:.1f}s"
    minutes, seconds = divmod(value, 60.0)
    return f"{int(minutes)}m {seconds:04.1f}s"


def _file_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / 1024 / 1024:.1f} MB"


class _PhaseUnit(QFrame):
    def __init__(self, number: str, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("consolePhaseUnit")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 7, 10, 7)
        layout.setSpacing(1)

        top = QHBoxLayout()
        top.setSpacing(6)
        number_label = QLabel(number)
        number_label.setObjectName("consoleEyebrow")
        self.state = QLabel("WAITING")
        self.state.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.state.setStyleSheet(f"color: {_STATUS_COLOR['waiting']}; font-size: 9px; font-weight: 750;")
        top.addWidget(number_label)
        top.addStretch(1)
        top.addWidget(self.state)

        self.title = QLabel(title)
        self.title.setStyleSheet("color: white; font-size: 11px; font-weight: 680;")
        self.detail = QLabel("—")
        self.detail.setObjectName("consoleHint")
        layout.addLayout(top)
        layout.addWidget(self.title)
        layout.addWidget(self.detail)

    def set_state(self, state: str, detail: str = "") -> None:
        state = state.casefold()
        self.state.setText(state.upper())
        self.state.setStyleSheet(
            f"color: {_STATUS_COLOR.get(state, _STATUS_COLOR['waiting'])}; "
            "font-size: 9px; font-weight: 750;"
        )
        if detail:
            self.detail.setText(detail)


class AcceptanceConsole(QFrame):
    """Dense, structured view of the real read-only acceptance execution."""

    def __init__(self, runner: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.runner = runner
        self.setObjectName("heroCard")
        self.setStyleSheet(_CONSOLE_STYLE)
        self.setMinimumHeight(260)
        self.setMaximumHeight(370)

        self._run_started: float | None = None
        self._phase_started: dict[str, float] = {}
        self._log_lines = 0
        self._last_result: Any = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 11, 15, 13)
        layout.setSpacing(8)

        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        eyebrow = QLabel("ACCEPTANCE CONTROL CONSOLE")
        eyebrow.setObjectName("consoleEyebrow")
        title = QLabel("运行控制台 · 真实阶段 / 命令 / 产物 / 日志")
        title.setObjectName("consoleTitle")
        title_box.addWidget(eyebrow)
        title_box.addWidget(title)
        header.addLayout(title_box)
        header.addStretch(1)
        self.total_time_label = QLabel("Total 00.0s")
        self.total_time_label.setObjectName("consoleHint")
        self.log_count_label = QLabel("0 log lines")
        self.log_count_label.setObjectName("consoleHint")
        header.addWidget(self.total_time_label)
        header.addSpacing(8)
        header.addWidget(self.log_count_label)
        layout.addLayout(header)

        progress_row = QHBoxLayout()
        progress_row.setSpacing(10)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("0%")
        self.progress_detail = QLabel("0/4 · idle")
        self.progress_detail.setObjectName("consoleHint")
        progress_row.addWidget(self.progress, 1)
        progress_row.addWidget(self.progress_detail)
        layout.addLayout(progress_row)

        phases = QHBoxLayout()
        phases.setSpacing(7)
        self.phase_units: dict[str, _PhaseUnit] = {}
        for key, number, title_text in _PHASES:
            unit = _PhaseUnit(number, title_text)
            self.phase_units[key] = unit
            phases.addWidget(unit, 1)
        layout.addLayout(phases)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self._build_log_tab(), "Live Console")
        self.tabs.addTab(self._build_timeline_tab(), "Timeline")
        self.tabs.addTab(self._build_command_artifact_tab(), "Commands / Artifacts")
        self.tabs.addTab(self._build_diagnostics_tab(), "Diagnostics")
        layout.addWidget(self.tabs, 1)

        runner.progress_changed.connect(self._on_progress)
        runner.phase_event.connect(self._on_phase_event)
        runner.command_started.connect(self._on_command_started)
        runner.result_updated.connect(self.apply_result)
        runner.running_changed.connect(self._on_running_changed)
        runner.log.connect(self._count_log_line)

        self.clock = QTimer(self)
        self.clock.setInterval(250)
        self.clock.timeout.connect(self._tick_clock)

    def _build_log_tab(self) -> QWidget:
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(5)
        row = QHBoxLayout()
        hint = QLabel("完整 subprocess stdout/stderr；批量刷新，避免高频日志拖慢 UI")
        hint.setObjectName("consoleHint")
        clear = QPushButton("清空显示")
        clear.setObjectName("quietButton")
        clear.clicked.connect(self._clear_log)
        row.addWidget(hint)
        row.addStretch(1)
        row.addWidget(clear)
        layout.addLayout(row)
        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("consoleText")
        self.log_view.setReadOnly(True)
        self.log_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.log_view.document().setMaximumBlockCount(12000)
        layout.addWidget(self.log_view, 1)
        return host

    def _build_timeline_tab(self) -> QWidget:
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(6, 6, 6, 6)
        self.timeline = QTableWidget(len(_PHASES), 6)
        self.timeline.setObjectName("consoleTable")
        self.timeline.setHorizontalHeaderLabels(
            ["Stage", "状态", "耗时", "Exit", "开始时间", "Output"]
        )
        self.timeline.setAlternatingRowColors(True)
        self.timeline.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.timeline.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.timeline.verticalHeader().setVisible(False)
        header = self.timeline.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.Stretch)
        self.phase_rows: dict[str, int] = {}
        for row, (key, number, title) in enumerate(_PHASES):
            self.phase_rows[key] = row
            self.timeline.setItem(row, 0, QTableWidgetItem(f"{number} · {title}"))
            self.timeline.setItem(row, 1, QTableWidgetItem("WAITING"))
            self.timeline.setItem(row, 2, QTableWidgetItem("—"))
            self.timeline.setItem(row, 3, QTableWidgetItem("—"))
            self.timeline.setItem(row, 4, QTableWidgetItem("—"))
            self.timeline.setItem(row, 5, QTableWidgetItem("—"))
        layout.addWidget(self.timeline)
        return host

    def _build_command_artifact_tab(self) -> QWidget:
        host = QWidget()
        layout = QHBoxLayout(host)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(7)

        self.command_view = QPlainTextEdit()
        self.command_view.setObjectName("consoleText")
        self.command_view.setReadOnly(True)
        self.command_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.command_view.setPlaceholderText("实际启动命令会按阶段记录在这里")
        self.command_view.document().setMaximumBlockCount(1000)

        self.artifact_table = QTableWidget(0, 3)
        self.artifact_table.setObjectName("consoleTable")
        self.artifact_table.setHorizontalHeaderLabels(["Type", "Artifact", "Size"])
        self.artifact_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.artifact_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.artifact_table.verticalHeader().setVisible(False)
        self.artifact_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.artifact_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.artifact_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        layout.addWidget(self.command_view, 5)
        layout.addWidget(self.artifact_table, 4)
        return host

    def _build_diagnostics_tab(self) -> QWidget:
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(6, 6, 6, 6)
        self.diagnostics_view = QPlainTextEdit()
        self.diagnostics_view.setObjectName("consoleText")
        self.diagnostics_view.setReadOnly(True)
        self.diagnostics_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.diagnostics_view.setPlainText("等待 acceptance 数据…")
        layout.addWidget(self.diagnostics_view)
        return host

    def _clear_log(self) -> None:
        self.log_view.clear()
        self._log_lines = 0
        self.log_count_label.setText("0 log lines")

    def _count_log_line(self, _line: str) -> None:
        self._log_lines += 1

    def _on_running_changed(self, running: bool) -> None:
        if running:
            self._run_started = time.monotonic()
            self._phase_started.clear()
            self._log_lines = 0
            self.command_view.clear()
            self.artifact_table.setRowCount(0)
            self.diagnostics_view.setPlainText("运行中，等待阶段产物…")
            for key, _, _ in _PHASES:
                self.phase_units[key].set_state("waiting", "—")
                row = self.phase_rows[key]
                for column, value in enumerate((None, "WAITING", "—", "—", "—", "—")):
                    if column == 0:
                        continue
                    self.timeline.setItem(row, column, QTableWidgetItem(value))
            self.clock.start()
        else:
            self._tick_clock()
            self.clock.stop()

    def _on_progress(self, percent: int, text: str) -> None:
        value = max(0, min(100, int(percent)))
        self.progress.setValue(value)
        self.progress.setFormat(f"{value}%")
        self.progress_detail.setText(text)

    def _on_phase_event(self, event: dict[str, Any]) -> None:
        phase = str(event.get("phase") or "")
        if phase not in self.phase_units:
            return
        status = str(event.get("status") or "waiting").casefold()
        row = self.phase_rows[phase]
        unit = self.phase_units[phase]

        if status == "running":
            self._phase_started[phase] = time.monotonic()
            unit.set_state(status, "running…")
        else:
            elapsed = float(event.get("elapsed_s") or 0.0)
            detail = _seconds(elapsed) if elapsed else status
            unit.set_state(status, detail)

        status_item = QTableWidgetItem(status.upper())
        status_item.setForeground(QColor(_STATUS_COLOR.get(status, _STATUS_COLOR["waiting"])))
        self.timeline.setItem(row, 1, status_item)
        elapsed = float(event.get("elapsed_s") or 0.0)
        if elapsed:
            self.timeline.setItem(row, 2, QTableWidgetItem(_seconds(elapsed)))
        exit_code = event.get("exit_code")
        if exit_code is not None:
            self.timeline.setItem(row, 3, QTableWidgetItem(str(exit_code)))
        started_at = str(event.get("started_at") or "")
        if started_at:
            self.timeline.setItem(row, 4, QTableWidgetItem(started_at.split("T")[-1]))
        output_dir = str(event.get("output_dir") or "")
        if output_dir:
            item = QTableWidgetItem(output_dir)
            item.setToolTip(output_dir)
            self.timeline.setItem(row, 5, item)

        if status in {"completed", "failed", "cancelled"}:
            self._refresh_artifacts()

    def _on_command_started(self, event: dict[str, Any]) -> None:
        phase = str(event.get("phase") or "?").upper()
        started_at = str(event.get("started_at") or "").split("T")[-1]
        command = str(event.get("command") or "")
        cwd = str(event.get("cwd") or "")
        output_dir = str(event.get("output_dir") or "")
        block = [f"[{started_at}] {phase}", f"$ {command}", f"cwd: {cwd}"]
        if output_dir:
            block.append(f"output: {output_dir}")
        if not self.command_view.document().isEmpty():
            self.command_view.appendPlainText("")
        self.command_view.appendPlainText("\n".join(block))

    def _tick_clock(self) -> None:
        if self._run_started is not None:
            self.total_time_label.setText(f"Total {_seconds(time.monotonic() - self._run_started)}")
        self.log_count_label.setText(f"{self._log_lines} log lines")
        for phase, started in self._phase_started.items():
            row = self.phase_rows.get(phase)
            if row is None:
                continue
            status = self.timeline.item(row, 1)
            if status is not None and status.text() == "RUNNING":
                elapsed = time.monotonic() - started
                self.timeline.setItem(row, 2, QTableWidgetItem(_seconds(elapsed)))
                self.phase_units[phase].detail.setText(_seconds(elapsed))

    def apply_result(self, result: Any) -> None:
        self._last_result = result
        counts: dict[str, int] = {}
        for row in getattr(result, "fields", []):
            key = str(getattr(row, "ai_status", "") or "UNKNOWN").upper()
            counts[key] = counts.get(key, 0) + 1

        cold = result.cold
        hot = result.hot
        lines = [
            f"run_dir          : {result.run_dir}",
            f"product_url      : {result.product_url or '—'}",
            f"live_fields      : {result.live_field_count}",
            "",
            "[AI decision packet]",
            "  " + "  ".join(f"{key}={value}" for key, value in sorted(counts.items())) if counts else "  waiting",
            "",
            "[Final Fill Plan]",
            f"  READY={result.ready}  BLOCKED={result.blocked}  MISSING={result.missing}  CONFLICT={result.conflict}",
            f"  summary={json.dumps(result.plan_summary, ensure_ascii=False, sort_keys=True)}",
            "",
            "[Cold Resolver]",
            f"  Local batches={cold.batch_count} calls={cold.model_calls} cache_hits={cold.cache_hits} failed={cold.failed_batches}",
            f"  Web   batches={cold.web_batch_count} calls={cold.web_model_calls} cache_hits={cold.web_cache_hits} failed={cold.web_failed_batches}",
            f"  Source cache={'HIT' if cold.source_cache_hit else 'MISS'}",
            "",
            "[Hot Resolver]",
            f"  Local batches={hot.batch_count} calls={hot.model_calls} cache_hits={hot.cache_hits} failed={hot.failed_batches}",
            f"  Web   batches={hot.web_batch_count} calls={hot.web_model_calls} cache_hits={hot.web_cache_hits} failed={hot.web_failed_batches}",
            f"  Source cache={'HIT' if hot.source_cache_hit else 'MISS'}",
            "",
            "[Web entity match]",
            f"  candidates={len(result.web_candidates)}",
            "",
            "[Zero-write safety]",
            f"  writes={result.safety.writes_performed}  save={result.safety.save_clicked}  send_to_qc={result.safety.send_to_qc_clicked}",
            f"  contract={'SAFE' if result.safety.safe else 'VIOLATED'}",
        ]
        self.diagnostics_view.setPlainText("\n".join(lines))
        self._refresh_artifacts()

    def _refresh_artifacts(self) -> None:
        run_dir = getattr(self.runner, "run_dir", None)
        if run_dir is None:
            return
        root = Path(run_dir)
        if not root.exists():
            return
        try:
            files = [path for path in root.rglob("*") if path.is_file()]
        except OSError:
            return
        files.sort(key=lambda path: path.stat().st_mtime_ns, reverse=True)
        files = files[:120]
        self.artifact_table.setRowCount(len(files))
        for row, path in enumerate(files):
            suffix = path.suffix.lower().lstrip(".") or "file"
            relative = str(path.relative_to(root))
            try:
                size = _file_size(path.stat().st_size)
            except OSError:
                size = "—"
            values = [suffix.upper(), relative, size]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(str(path))
                self.artifact_table.setItem(row, column, item)
