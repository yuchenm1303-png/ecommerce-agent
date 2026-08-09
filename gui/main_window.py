from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .readonly_runner import ReadOnlyRunner, RunnerConfig
from .result_loader import PhaseStats, RunResult


APP_STYLE = r"""
QWidget#root {
    color: #f7f2fa;
    font-family: "Segoe UI", "Microsoft YaHei UI", sans-serif;
    font-size: 13px;
    background: qradialgradient(
        cx:0.18, cy:0.10, radius:1.05,
        fx:0.18, fy:0.10,
        stop:0 rgba(163, 90, 139, 255),
        stop:0.30 rgba(80, 68, 104, 255),
        stop:0.66 rgba(30, 34, 49, 255),
        stop:1 rgba(14, 18, 27, 255)
    );
}
QFrame#glassCard {
    background-color: rgba(18, 22, 31, 208);
    border: 1px solid rgba(255, 255, 255, 36);
    border-radius: 16px;
}
QFrame#statusCard {
    background-color: rgba(22, 26, 36, 218);
    border: 1px solid rgba(255, 255, 255, 30);
    border-radius: 14px;
}
QLabel#appTitle {
    font-size: 25px;
    font-weight: 700;
    color: #ffffff;
}
QLabel#subtle, QLabel#cardHint {
    color: rgba(242, 231, 246, 165);
}
QLabel#cardTitle {
    font-size: 14px;
    font-weight: 650;
    color: #ffffff;
}
QLabel#phaseBadge {
    padding: 7px 12px;
    border-radius: 12px;
    background-color: rgba(224, 166, 205, 30);
    border: 1px solid rgba(238, 195, 224, 70);
    color: #f4dce9;
}
QLineEdit, QSpinBox {
    min-height: 38px;
    padding: 0 11px;
    color: #ffffff;
    background-color: rgba(255, 255, 255, 15);
    border: 1px solid rgba(255, 255, 255, 40);
    border-radius: 10px;
    selection-background-color: #9d6388;
}
QLineEdit:focus, QSpinBox:focus {
    border: 1px solid rgba(241, 185, 220, 135);
    background-color: rgba(255, 255, 255, 22);
}
QPushButton {
    min-height: 37px;
    padding: 0 16px;
    border-radius: 10px;
    border: 1px solid rgba(255, 255, 255, 38);
    color: #f9f5fa;
    background-color: rgba(255, 255, 255, 16);
}
QPushButton:hover {
    background-color: rgba(255, 255, 255, 26);
    border-color: rgba(255, 255, 255, 58);
}
QPushButton:pressed {
    background-color: rgba(255, 255, 255, 12);
}
QPushButton#primaryButton {
    min-width: 132px;
    font-weight: 700;
    background-color: rgba(193, 112, 163, 175);
    border: 1px solid rgba(255, 209, 237, 100);
}
QPushButton#primaryButton:hover {
    background-color: rgba(210, 127, 180, 205);
}
QPushButton#dangerButton {
    background-color: rgba(150, 65, 80, 115);
}
QPushButton:disabled {
    color: rgba(255, 255, 255, 75);
    background-color: rgba(255, 255, 255, 7);
}
QCheckBox {
    spacing: 7px;
    color: rgba(245, 237, 248, 205);
}
QCheckBox::indicator {
    width: 17px;
    height: 17px;
    border-radius: 5px;
    border: 1px solid rgba(255, 255, 255, 60);
    background-color: rgba(255, 255, 255, 12);
}
QCheckBox::indicator:checked {
    background-color: #b56f9c;
    border-color: #e3b6d2;
}
QTableWidget {
    color: #f4eef6;
    background-color: rgba(8, 11, 17, 90);
    alternate-background-color: rgba(255, 255, 255, 8);
    border: 0;
    border-radius: 10px;
    gridline-color: rgba(255, 255, 255, 16);
    selection-background-color: rgba(161, 96, 137, 115);
    selection-color: #ffffff;
}
QTableWidget::item {
    padding: 7px 8px;
    border-bottom: 1px solid rgba(255, 255, 255, 12);
}
QHeaderView::section {
    padding: 8px 8px;
    color: rgba(249, 241, 251, 205);
    background-color: rgba(255, 255, 255, 11);
    border: 0;
    border-bottom: 1px solid rgba(255, 255, 255, 22);
    font-weight: 600;
}
QPlainTextEdit {
    color: #d8d9df;
    background-color: rgba(5, 8, 13, 155);
    border: 0;
    border-radius: 10px;
    padding: 8px;
    selection-background-color: #8f5f7f;
    font-family: "Cascadia Mono", "Consolas", monospace;
    font-size: 12px;
}
QScrollBar:vertical {
    width: 10px;
    background: transparent;
    margin: 3px 2px 3px 2px;
}
QScrollBar::handle:vertical {
    min-height: 28px;
    border-radius: 5px;
    background: rgba(231, 197, 220, 80);
}
QScrollBar::handle:vertical:hover {
    background: rgba(231, 197, 220, 120);
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    height: 0;
    background: transparent;
}
QSplitter::handle {
    background: transparent;
    width: 8px;
    height: 8px;
}
"""


STATUS_COLORS = {
    "READY": QColor("#66d19e"),
    "MISSING": QColor("#e9bd69"),
    "CONFLICT": QColor("#e67e91"),
    "BLOCKED": QColor("#d9879c"),
    "SAME_PRODUCT": QColor("#66d19e"),
    "DIFFERENT_PRODUCT": QColor("#e67e91"),
    "UNCERTAIN": QColor("#e9bd69"),
}


class StatusCard(QFrame):
    def __init__(self, title: str, caption: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("statusCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 11, 14, 11)
        layout.setSpacing(2)
        self.value = QLabel("—")
        self.value.setStyleSheet("font-size: 24px; font-weight: 750; color: white;")
        title_label = QLabel(title)
        title_label.setStyleSheet("font-size: 12px; font-weight: 650; color: rgba(255,255,255,210);")
        caption_label = QLabel(caption)
        caption_label.setObjectName("cardHint")
        caption_label.setStyleSheet("font-size: 10px;")
        layout.addWidget(self.value)
        layout.addWidget(title_label)
        layout.addWidget(caption_label)

    def set_value(self, value: int | str, color: str | None = None) -> None:
        self.value.setText(str(value))
        if color:
            self.value.setStyleSheet(f"font-size: 24px; font-weight: 750; color: {color};")


class MainWindow(QMainWindow):
    def __init__(self, project_root: Path) -> None:
        super().__init__()
        self.project_root = project_root.resolve()
        self.runner = ReadOnlyRunner(self.project_root, self)
        self.current_result: RunResult | None = None
        self.setWindowTitle("ecommerce-agent · Read-only Test Lab")
        self.resize(1520, 930)
        self.setMinimumSize(1180, 760)

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        self.setStyleSheet(APP_STYLE)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(22, 18, 22, 18)
        outer.setSpacing(12)

        outer.addLayout(self._build_header())
        outer.addWidget(self._build_input_card())
        outer.addLayout(self._build_status_row())

        center = QSplitter(Qt.Horizontal)
        center.setChildrenCollapsible(False)
        center.addWidget(self._build_fields_card())
        center.addWidget(self._build_side_panel())
        center.setStretchFactor(0, 7)
        center.setStretchFactor(1, 3)
        center.setSizes([1040, 430])
        outer.addWidget(center, 1)

        outer.addWidget(self._build_log_card())

        self.runner.log.connect(self._append_log)
        self.runner.phase_changed.connect(self.phase_badge.setText)
        self.runner.running_changed.connect(self._set_running)
        self.runner.result_updated.connect(self._apply_result)
        self.runner.completed.connect(self._run_completed)
        self.runner.failed.connect(self._run_failed)

    def _build_header(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("ecommerce-agent  /  Read-only Lab")
        title.setObjectName("appTitle")
        subtitle = QLabel("供应商 URL → fresh schema → cold/hot Resolver → read-only Fill Plan")
        subtitle.setObjectName("subtle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        layout.addLayout(title_box)
        layout.addStretch(1)
        self.phase_badge = QLabel("Idle · No Makro writes")
        self.phase_badge.setObjectName("phaseBadge")
        layout.addWidget(self.phase_badge)
        self.open_run_button = QPushButton("打开本次结果目录")
        self.open_run_button.setEnabled(False)
        self.open_run_button.clicked.connect(self._open_run_dir)
        layout.addWidget(self.open_run_button)
        return layout

    def _build_input_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("glassCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(15, 13, 15, 13)
        layout.setSpacing(9)

        top = QHBoxLayout()
        title = QLabel("商品来源")
        title.setObjectName("cardTitle")
        hint = QLabel("只输入 1688 / supplier 商品 URL；GUI 不接受人工 SKU。")
        hint.setObjectName("cardHint")
        top.addWidget(title)
        top.addSpacing(10)
        top.addWidget(hint)
        top.addStretch(1)
        layout.addLayout(top)

        row = QHBoxLayout()
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://detail.1688.com/offer/...")
        self.url_input.returnPressed.connect(self._start_run)
        row.addWidget(self.url_input, 1)
        self.start_button = QPushButton("只读测试")
        self.start_button.setObjectName("primaryButton")
        self.start_button.clicked.connect(self._start_run)
        row.addWidget(self.start_button)
        self.stop_button = QPushButton("停止")
        self.stop_button.setObjectName("dangerButton")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.runner.stop)
        row.addWidget(self.stop_button)
        layout.addLayout(row)

        settings = QHBoxLayout()
        settings.setSpacing(12)
        self.makro_port = QSpinBox()
        self.makro_port.setRange(1, 65535)
        self.makro_port.setValue(9222)
        self.makro_port.setPrefix("Makro CDP  ")
        self.makro_port.setMaximumWidth(170)
        settings.addWidget(self.makro_port)

        self.source_port = QSpinBox()
        self.source_port.setRange(1, 65535)
        self.source_port.setValue(9333)
        self.source_port.setPrefix("Source CDP  ")
        self.source_port.setMaximumWidth(175)
        settings.addWidget(self.source_port)

        self.vertical_input = QLineEdit("vehicle_camera_system")
        self.vertical_input.setPlaceholderText("expected vertical")
        self.vertical_input.setMaximumWidth(245)
        settings.addWidget(self.vertical_input)

        self.current_page_check = QCheckBox("Source Edge 已人工验证：采集当前页")
        settings.addWidget(self.current_page_check)
        settings.addStretch(1)
        layout.addLayout(settings)
        return card

    def _build_status_row(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setSpacing(10)
        self.ready_card = StatusCard("READY", "Final Fill Plan")
        self.missing_card = StatusCard("MISSING", "AI final packet")
        self.conflict_card = StatusCard("CONFLICT", "AI final packet")
        self.blocked_card = StatusCard("BLOCKED", "Final hard/business gate")
        for card in (self.ready_card, self.missing_card, self.conflict_card, self.blocked_card):
            layout.addWidget(card, 1)
        return layout

    def _build_fields_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("glassCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(13, 12, 13, 12)
        layout.setSpacing(8)
        title_row = QHBoxLayout()
        title = QLabel("字段表")
        title.setObjectName("cardTitle")
        self.fields_hint = QLabel("等待只读测试结果")
        self.fields_hint.setObjectName("cardHint")
        title_row.addWidget(title)
        title_row.addStretch(1)
        title_row.addWidget(self.fields_hint)
        layout.addLayout(title_row)

        self.field_table = QTableWidget(0, 5)
        self.field_table.setHorizontalHeaderLabels(["字段名", "AI 结果", "最终状态", "blocked 原因", "来源"])
        self.field_table.setAlternatingRowColors(True)
        self.field_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.field_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.field_table.setSortingEnabled(False)
        self.field_table.verticalHeader().setVisible(False)
        header = self.field_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        layout.addWidget(self.field_table, 1)
        return card

    def _build_side_panel(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: 0; }")
        host = QWidget()
        host.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(self._build_runtime_card())
        layout.addWidget(self._build_web_card(), 1)
        layout.addWidget(self._build_safety_card())
        layout.addStretch(1)
        scroll.setWidget(host)
        return scroll

    def _build_runtime_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("glassCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(13, 12, 13, 12)
        layout.setSpacing(8)
        title = QLabel("Local / Cache")
        title.setObjectName("cardTitle")
        layout.addWidget(title)
        self.cold_label = QLabel("Cold  · waiting")
        self.hot_label = QLabel("Hot   · waiting")
        self.source_cache_label = QLabel("Source cache  · waiting")
        self.web_cache_label = QLabel("Web cache     · waiting")
        for label in (self.cold_label, self.hot_label, self.source_cache_label, self.web_cache_label):
            label.setWordWrap(True)
            label.setObjectName("cardHint")
            layout.addWidget(label)
        return card

    def _build_web_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("glassCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(13, 12, 13, 12)
        layout.setSpacing(8)
        row = QHBoxLayout()
        title = QLabel("Web candidates")
        title.setObjectName("cardTitle")
        self.web_hint = QLabel("same / different / uncertain")
        self.web_hint.setObjectName("cardHint")
        row.addWidget(title)
        row.addStretch(1)
        row.addWidget(self.web_hint)
        layout.addLayout(row)
        self.web_table = QTableWidget(0, 3)
        self.web_table.setHorizontalHeaderLabels(["判定", "来源", "原因"])
        self.web_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.web_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.web_table.verticalHeader().setVisible(False)
        self.web_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.web_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.web_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.web_table.setMinimumHeight(180)
        layout.addWidget(self.web_table)
        return card

    def _build_safety_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("glassCard")
        layout = QGridLayout(card)
        layout.setContentsMargins(13, 12, 13, 12)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(7)
        title = QLabel("Makro write safety")
        title.setObjectName("cardTitle")
        layout.addWidget(title, 0, 0, 1, 2)
        self.write_value = self._safety_value("NO / 0")
        self.save_value = self._safety_value("NO")
        self.qc_value = self._safety_value("NO")
        layout.addWidget(QLabel("Makro Write"), 1, 0)
        layout.addWidget(self.write_value, 1, 1)
        layout.addWidget(QLabel("Save"), 2, 0)
        layout.addWidget(self.save_value, 2, 1)
        layout.addWidget(QLabel("Send to QC"), 3, 0)
        layout.addWidget(self.qc_value, 3, 1)
        return card

    def _safety_value(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        label.setStyleSheet("color: #66d19e; font-weight: 750;")
        return label

    def _build_log_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("glassCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(13, 10, 13, 12)
        layout.setSpacing(7)
        row = QHBoxLayout()
        title = QLabel("实时运行日志")
        title.setObjectName("cardTitle")
        clear_button = QPushButton("清空显示")
        clear_button.clicked.connect(lambda: self.log_view.clear())
        row.addWidget(title)
        row.addStretch(1)
        row.addWidget(clear_button)
        layout.addLayout(row)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(8000)
        self.log_view.setMinimumHeight(190)
        self.log_view.setMaximumHeight(245)
        layout.addWidget(self.log_view)
        return card

    def _start_run(self) -> None:
        config = RunnerConfig(
            product_url=self.url_input.text().strip(),
            expected_vertical=self.vertical_input.text().strip(),
            makro_cdp_port=int(self.makro_port.value()),
            source_cdp_port=int(self.source_port.value()),
            source_use_current_page=self.current_page_check.isChecked(),
        )
        try:
            self._reset_result_views()
            self.runner.start(config)
            self.open_run_button.setEnabled(True)
        except Exception as exc:
            QMessageBox.critical(self, "无法开始只读测试", str(exc))

    def _set_running(self, running: bool) -> None:
        self.start_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        self.url_input.setEnabled(not running)
        self.makro_port.setEnabled(not running)
        self.source_port.setEnabled(not running)
        self.vertical_input.setEnabled(not running)
        self.current_page_check.setEnabled(not running)

    def _append_log(self, line: str) -> None:
        self.log_view.appendPlainText(line)
        bar = self.log_view.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _reset_result_views(self) -> None:
        self.current_result = None
        self.ready_card.set_value("—")
        self.missing_card.set_value("—")
        self.conflict_card.set_value("—")
        self.blocked_card.set_value("—")
        self.field_table.setRowCount(0)
        self.web_table.setRowCount(0)
        self.fields_hint.setText("运行中")
        self.web_hint.setText("等待 Web research")
        self.cold_label.setText("Cold  · waiting")
        self.hot_label.setText("Hot   · waiting")
        self.source_cache_label.setText("Source cache  · waiting")
        self.web_cache_label.setText("Web cache     · waiting")
        self._set_safety(0, False, False)

    def _apply_result(self, result: RunResult) -> None:
        self.current_result = result
        self.ready_card.set_value(result.ready, "#66d19e")
        self.missing_card.set_value(result.missing, "#e9bd69")
        self.conflict_card.set_value(result.conflict, "#e67e91")
        self.blocked_card.set_value(result.blocked, "#d9879c")
        self._populate_fields(result)
        self._populate_web(result)
        self._populate_runtime(result.cold, result.hot)
        self._set_safety(
            result.safety.writes_performed,
            result.safety.save_clicked,
            result.safety.send_to_qc_clicked,
        )
        suffix = f"{result.live_field_count} fields" if result.live_field_count else "partial result"
        self.fields_hint.setText(suffix)

    def _populate_fields(self, result: RunResult) -> None:
        self.field_table.setRowCount(len(result.fields))
        for row_index, row in enumerate(result.fields):
            values = [row.field_name, row.ai_result, row.final_status, row.blocked_reason, row.source]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if column == 2:
                    color = STATUS_COLORS.get(row.final_status)
                    if color:
                        item.setForeground(color)
                self.field_table.setItem(row_index, column, item)
        self.field_table.resizeRowsToContents()

    def _populate_web(self, result: RunResult) -> None:
        candidates = result.web_candidates
        self.web_table.setRowCount(len(candidates))
        self.web_hint.setText(f"{len(candidates)} candidates")
        for row_index, candidate in enumerate(candidates):
            match_text = candidate.match.upper()
            source_text = candidate.title or candidate.url
            values = [match_text, source_text, candidate.reason]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                tooltip = value
                if column == 1:
                    tooltip = candidate.url
                elif column == 2 and candidate.identity_evidence:
                    tooltip += "\n\nIdentity evidence:\n- " + "\n- ".join(candidate.identity_evidence)
                item.setToolTip(tooltip)
                if column == 0:
                    color = STATUS_COLORS.get(match_text)
                    if color:
                        item.setForeground(color)
                self.web_table.setItem(row_index, column, item)
        self.web_table.resizeRowsToContents()

    def _populate_runtime(self, cold: PhaseStats, hot: PhaseStats) -> None:
        self.cold_label.setText(self._phase_text("Cold", cold))
        self.hot_label.setText(self._phase_text("Hot", hot))
        self.source_cache_label.setText(
            "Source cache  · Cold={}  Hot={}".format(
                "HIT" if cold.source_cache_hit else "MISS",
                "HIT" if hot.source_cache_hit else "MISS",
            )
        )
        self.web_cache_label.setText(
            "Web cache     · Cold {}/{} hits · Hot {}/{} hits".format(
                cold.web_cache_hits,
                cold.web_batch_count,
                hot.web_cache_hits,
                hot.web_batch_count,
            )
        )

    def _phase_text(self, name: str, stats: PhaseStats) -> str:
        return (
            f"{name} · Local batches {stats.batch_count} · calls {stats.model_calls} · "
            f"cache {stats.cache_hits}/{stats.batch_count} · failed {stats.failed_batches}"
        )

    def _set_safety(self, writes: int, save: bool, qc: bool) -> None:
        self.write_value.setText(f"{'YES' if writes else 'NO'} / {writes}")
        self.save_value.setText("YES" if save else "NO")
        self.qc_value.setText("YES" if qc else "NO")
        bad = writes > 0 or save or qc
        color = "#ef7285" if bad else "#66d19e"
        for label in (self.write_value, self.save_value, self.qc_value):
            label.setStyleSheet(f"color: {color}; font-weight: 750;")

    def _run_completed(self, result: RunResult) -> None:
        if result.safety.safe:
            self.phase_badge.setText("完成 · 0 Write / 0 Save / 0 QC")
        else:
            self.phase_badge.setText("警告 · Safety contract violated")
            QMessageBox.critical(
                self,
                "Makro write safety warning",
                "检测到本次 manifest 记录了写入/Save/QC。请立即检查日志。",
            )

    def _run_failed(self, message: str) -> None:
        QMessageBox.warning(self, "只读测试未完成", message)

    def _open_run_dir(self) -> None:
        run_dir = self.runner.run_dir
        if run_dir is None or not run_dir.exists():
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(run_dir)))
