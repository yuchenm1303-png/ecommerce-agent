from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QUrl
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QLinearGradient,
    QPainter,
    QRadialGradient,
)
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
    color: #fff7fb;
    font-family: "Segoe UI", "Microsoft YaHei UI", sans-serif;
    font-size: 13px;
    background: transparent;
}
QWidget#workspaceHost,
QWidget#sideHost {
    background: transparent;
}
QFrame#glassCard {
    background-color: rgba(86, 53, 78, 142);
    border: 1px solid rgba(255, 238, 248, 40);
    border-radius: 18px;
}
QFrame#heroCard {
    background-color: rgba(96, 58, 88, 120);
    border: 1px solid rgba(255, 239, 249, 42);
    border-radius: 22px;
}
QFrame#statusCard {
    background-color: rgba(104, 66, 94, 128);
    border: 1px solid rgba(255, 241, 249, 38);
    border-radius: 17px;
}
QFrame#microCard {
    background-color: rgba(54, 40, 61, 112);
    border: 1px solid rgba(255, 242, 249, 28);
    border-radius: 14px;
}
QLabel#brandMark {
    color: rgba(255,255,255,188);
    font-size: 12px;
    font-weight: 650;
    letter-spacing: 2px;
}
QLabel#appTitle {
    font-size: 33px;
    font-weight: 760;
    color: #fffdfd;
}
QLabel#subtle, QLabel#cardHint {
    color: rgba(255, 237, 247, 178);
}
QLabel#cardTitle {
    font-size: 14px;
    font-weight: 680;
    color: #fffafb;
}
QLabel#sectionEyebrow {
    color: rgba(255, 226, 241, 166);
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1px;
}
QLabel#phaseBadge {
    padding: 9px 14px;
    border-radius: 13px;
    background-color: rgba(116, 76, 105, 132);
    border: 1px solid rgba(255, 231, 244, 58);
    color: #fff1f8;
    font-weight: 650;
}
QLineEdit, QSpinBox {
    min-height: 39px;
    padding: 0 12px;
    color: #fffdfd;
    background-color: rgba(39, 28, 44, 90);
    border: 1px solid rgba(255, 239, 248, 38);
    border-radius: 11px;
    selection-background-color: #b9799f;
}
QLineEdit:hover, QSpinBox:hover {
    background-color: rgba(48, 33, 51, 105);
    border-color: rgba(255, 240, 248, 54);
}
QLineEdit:focus, QSpinBox:focus {
    border: 1px solid rgba(255, 211, 235, 142);
    background-color: rgba(45, 30, 49, 125);
}
QPushButton {
    min-height: 38px;
    padding: 0 16px;
    border-radius: 11px;
    border: 1px solid rgba(255, 239, 248, 40);
    color: #fff9fc;
    background-color: rgba(74, 52, 75, 112);
}
QPushButton:hover {
    background-color: rgba(119, 76, 103, 145);
    border-color: rgba(255, 237, 247, 72);
}
QPushButton:pressed {
    background-color: rgba(68, 44, 66, 154);
}
QPushButton#primaryButton {
    min-width: 140px;
    font-weight: 720;
    background-color: rgba(190, 113, 157, 190);
    border: 1px solid rgba(255, 220, 239, 105);
}
QPushButton#primaryButton:hover {
    background-color: rgba(211, 132, 178, 220);
}
QPushButton#dangerButton {
    background-color: rgba(131, 64, 79, 125);
}
QPushButton#quietButton {
    background-color: rgba(61, 45, 66, 92);
}
QPushButton:disabled {
    color: rgba(255, 246, 250, 78);
    background-color: rgba(63, 48, 65, 68);
    border-color: rgba(255,255,255,18);
}
QCheckBox {
    spacing: 8px;
    color: rgba(255, 244, 249, 210);
}
QCheckBox::indicator {
    width: 17px;
    height: 17px;
    border-radius: 5px;
    border: 1px solid rgba(255, 243, 249, 65);
    background-color: rgba(47, 34, 49, 92);
}
QCheckBox::indicator:checked {
    background-color: #c479a7;
    border-color: #f5cce3;
}
QTableWidget {
    color: #fff8fc;
    background-color: rgba(35, 28, 42, 96);
    alternate-background-color: rgba(255, 233, 246, 9);
    border: 1px solid rgba(255, 238, 248, 24);
    border-radius: 13px;
    gridline-color: rgba(255, 238, 248, 14);
    selection-background-color: rgba(178, 107, 149, 115);
    selection-color: #ffffff;
}
QTableWidget::item {
    padding: 8px 9px;
    border-bottom: 1px solid rgba(255, 241, 249, 12);
}
QHeaderView::section {
    padding: 9px 9px;
    color: rgba(255, 246, 251, 218);
    background-color: rgba(101, 68, 94, 105);
    border: 0;
    border-bottom: 1px solid rgba(255, 238, 248, 26);
    font-weight: 650;
}
QPlainTextEdit {
    color: #f3eaf0;
    background-color: rgba(29, 24, 36, 106);
    border: 1px solid rgba(255, 238, 248, 22);
    border-radius: 13px;
    padding: 10px;
    selection-background-color: #a86990;
    font-family: "Cascadia Mono", "Consolas", monospace;
    font-size: 12px;
}
QScrollArea {
    background: transparent;
    border: 0;
}
QScrollBar:vertical {
    width: 9px;
    background: transparent;
    margin: 4px 1px 4px 1px;
}
QScrollBar::handle:vertical {
    min-height: 30px;
    border-radius: 4px;
    background: rgba(255, 220, 240, 82);
}
QScrollBar::handle:vertical:hover {
    background: rgba(255, 224, 241, 126);
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    height: 0;
    background: transparent;
}
QSplitter::handle {
    background: transparent;
    width: 12px;
    height: 12px;
}
"""


STATUS_COLORS = {
    "READY": QColor("#8fe1b9"),
    "MISSING": QColor("#f4cb7a"),
    "CONFLICT": QColor("#f18da0"),
    "BLOCKED": QColor("#e796ae"),
    "SAME_PRODUCT": QColor("#8fe1b9"),
    "DIFFERENT_PRODUCT": QColor("#f18da0"),
    "UNCERTAIN": QColor("#f4cb7a"),
}


class _SmoothWheelMixin:
    """Mixin: turn wheel notches into continuous per-pixel scrolls.

    PySide6 does not expose QAbstractScrollArea.setVerticalScrollMode() on
    QScrollArea / QPlainTextEdit, so continuous scrolling needs a manual
    wheelEvent mapping instead. Ctrl+wheel still falls through to the base
    widget (e.g. text zoom).
    """

    _PIXELS_PER_NOTCH = 96

    def wheelEvent(self, event) -> None:  # type: ignore[override]
        delta = event.angleDelta().y()
        if delta and not (event.modifiers() & Qt.ControlModifier):
            bar = self.verticalScrollBar()
            bar.setValue(bar.value() - round(delta / 120 * self._PIXELS_PER_NOTCH))
            event.accept()
        else:
            super().wheelEvent(event)


class SmoothScrollArea(_SmoothWheelMixin, QScrollArea):
    """QScrollArea variant with continuous wheel scrolling."""


class SmoothLogView(_SmoothWheelMixin, QPlainTextEdit):
    """Log pane variant with continuous wheel scrolling."""

    _PIXELS_PER_NOTCH = 48


class AtmosphereWidget(QWidget):
    """Cheap procedural backdrop inspired by the earlier dreamy glass homepage.

    It deliberately avoids network/background-image dependencies so the Windows
    development GUI remains fast and deterministic.
    """

    _PETALS = (
        (.04, .18, -20, 9), (.10, .73, 28, 7), (.16, .31, 16, 8),
        (.23, .09, -12, 6), (.29, .58, 32, 9), (.36, .21, -28, 7),
        (.43, .83, 14, 8), (.50, .13, 24, 7), (.58, .48, -16, 9),
        (.63, .76, 30, 6), (.69, .24, -10, 8), (.75, .55, 22, 7),
        (.81, .12, -30, 8), (.86, .68, 16, 9), (.92, .34, 29, 6),
        (.96, .82, -14, 8),
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("root")
        self.setAutoFillBackground(False)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = self.rect()
        width = max(1, rect.width())
        height = max(1, rect.height())

        base = QLinearGradient(0, 0, width, height)
        base.setColorAt(0.00, QColor(148, 91, 132))
        base.setColorAt(0.28, QColor(102, 77, 116))
        base.setColorAt(0.62, QColor(50, 52, 78))
        base.setColorAt(1.00, QColor(24, 29, 46))
        painter.fillRect(rect, base)

        # Wide soft blooms mimic out-of-focus sakura/tree light without a bitmap.
        blooms = (
            (.10, .02, .42, QColor(255, 181, 213, 95)),
            (.34, .08, .34, QColor(248, 200, 226, 74)),
            (.73, .12, .43, QColor(191, 156, 211, 63)),
            (.95, .45, .35, QColor(243, 163, 204, 52)),
            (.26, .90, .44, QColor(166, 116, 164, 48)),
        )
        for x, y, radius_ratio, color in blooms:
            center = QPointF(width * x, height * y)
            radius = max(width, height) * radius_ratio
            glow = QRadialGradient(center, radius)
            glow.setColorAt(0.0, color)
            fade = QColor(color)
            fade.setAlpha(0)
            glow.setColorAt(1.0, fade)
            painter.fillRect(rect, glow)

        # Gentle bright clearing through the middle, like the reference homepage.
        clearing = QRadialGradient(
            QPointF(width * .48, height * .48),
            max(width, height) * .52,
        )
        clearing.setColorAt(0.0, QColor(255, 218, 234, 42))
        clearing.setColorAt(.58, QColor(248, 205, 226, 18))
        clearing.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.fillRect(rect, clearing)

        painter.setPen(Qt.NoPen)
        for x, y, angle, size in self._PETALS:
            painter.save()
            painter.translate(width * x, height * y)
            painter.rotate(angle)
            painter.setBrush(QColor(255, 222, 237, 150))
            painter.drawEllipse(QRectF(-size * .60, -size * .23, size * 1.2, size * .46))
            painter.restore()

        # Bottom vignette improves readability of the log panel.
        vignette = QLinearGradient(0, height * .55, 0, height)
        vignette.setColorAt(0, QColor(17, 19, 31, 0))
        vignette.setColorAt(1, QColor(13, 17, 29, 80))
        painter.fillRect(rect, vignette)
        painter.end()

        super().paintEvent(event)


class StatusCard(QFrame):
    def __init__(self, title: str, caption: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("statusCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(17, 13, 17, 13)
        layout.setSpacing(3)
        self.value = QLabel("—")
        self.value.setStyleSheet("font-size: 26px; font-weight: 760; color: white;")
        title_label = QLabel(title)
        title_label.setStyleSheet("font-size: 11px; font-weight: 720; color: rgba(255,255,255,220);")
        caption_label = QLabel(caption)
        caption_label.setObjectName("cardHint")
        caption_label.setStyleSheet("font-size: 10px;")
        layout.addWidget(self.value)
        layout.addWidget(title_label)
        layout.addWidget(caption_label)

    def set_value(self, value: int | str, color: str | None = None) -> None:
        self.value.setText(str(value))
        if color:
            self.value.setStyleSheet(f"font-size: 26px; font-weight: 760; color: {color};")


class MainWindow(QMainWindow):
    def __init__(self, project_root: Path) -> None:
        super().__init__()
        self.project_root = project_root.resolve()
        self.runner = ReadOnlyRunner(self.project_root, self)
        self.current_result: RunResult | None = None
        self.setWindowTitle("ecommerce-agent · Read-only Test Lab")
        self.resize(1540, 940)
        self.setMinimumSize(1180, 760)

        root = AtmosphereWidget()
        self.setCentralWidget(root)
        self.setStyleSheet(APP_STYLE)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(30, 24, 30, 24)
        outer.setSpacing(14)

        outer.addLayout(self._build_header())
        outer.addWidget(self._build_input_card())
        outer.addLayout(self._build_status_row())

        workspace = QWidget()
        workspace.setObjectName("workspaceHost")
        workspace_layout = QHBoxLayout(workspace)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.setSpacing(0)
        center = QSplitter(Qt.Horizontal)
        center.setChildrenCollapsible(False)
        center.addWidget(self._build_fields_card())
        center.addWidget(self._build_side_panel())
        center.setStretchFactor(0, 7)
        center.setStretchFactor(1, 3)
        center.setSizes([1020, 440])
        workspace_layout.addWidget(center)
        outer.addWidget(workspace, 1)

        outer.addWidget(self._build_log_card())

        self.runner.log.connect(self._append_log)
        self.runner.phase_changed.connect(self.phase_badge.setText)
        self.runner.running_changed.connect(self._set_running)
        self.runner.result_updated.connect(self._apply_result)
        self.runner.completed.connect(self._run_completed)
        self.runner.failed.connect(self._run_failed)

    def _build_header(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setSpacing(14)
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        mark = QLabel("LOCAL DEVELOPMENT  ·  ZERO-WRITE ACCEPTANCE")
        mark.setObjectName("brandMark")
        title = QLabel("ecommerce-agent")
        title.setObjectName("appTitle")
        subtitle = QLabel("Read-only Lab  /  供应商 URL → fresh schema → cold/hot Resolver → Fill Plan")
        subtitle.setObjectName("subtle")
        title_box.addWidget(mark)
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        layout.addLayout(title_box)
        layout.addStretch(1)

        self.phase_badge = QLabel("Idle · No Makro writes")
        self.phase_badge.setObjectName("phaseBadge")
        layout.addWidget(self.phase_badge, 0, Qt.AlignBottom)
        self.open_run_button = QPushButton("打开结果目录")
        self.open_run_button.setObjectName("quietButton")
        self.open_run_button.setEnabled(False)
        self.open_run_button.clicked.connect(self._open_run_dir)
        layout.addWidget(self.open_run_button, 0, Qt.AlignBottom)
        return layout

    def _build_input_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("heroCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 15, 18, 15)
        layout.setSpacing(10)

        top = QHBoxLayout()
        text_box = QVBoxLayout()
        text_box.setSpacing(1)
        eyebrow = QLabel("PRODUCT SOURCE")
        eyebrow.setObjectName("sectionEyebrow")
        title = QLabel("商品来源")
        title.setObjectName("cardTitle")
        hint = QLabel("只输入一个 1688 / supplier 商品 URL；GUI 不接收人工 SKU，也不会写 Makro。")
        hint.setObjectName("cardHint")
        text_box.addWidget(eyebrow)
        text_box.addWidget(title)
        top.addLayout(text_box)
        top.addSpacing(12)
        top.addWidget(hint, 1, Qt.AlignBottom)
        layout.addLayout(top)

        row = QHBoxLayout()
        row.setSpacing(9)
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
        settings.setSpacing(10)
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
        layout.setSpacing(11)
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
        layout.setContentsMargins(15, 13, 15, 14)
        layout.setSpacing(9)
        title_row = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        eyebrow = QLabel("FIELD RESOLUTION")
        eyebrow.setObjectName("sectionEyebrow")
        title = QLabel("字段表")
        title.setObjectName("cardTitle")
        title_box.addWidget(eyebrow)
        title_box.addWidget(title)
        self.fields_hint = QLabel("等待只读测试结果")
        self.fields_hint.setObjectName("cardHint")
        title_row.addLayout(title_box)
        title_row.addStretch(1)
        title_row.addWidget(self.fields_hint, 0, Qt.AlignBottom)
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
        self.field_table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.field_table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        layout.addWidget(self.field_table, 1)
        return card

    def _build_side_panel(self) -> QWidget:
        scroll = SmoothScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        host = QWidget()
        host.setObjectName("sideHost")
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(11)
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
        layout.setContentsMargins(15, 13, 15, 14)
        layout.setSpacing(7)
        eyebrow = QLabel("RUN DIAGNOSTICS")
        eyebrow.setObjectName("sectionEyebrow")
        title = QLabel("Local / Cache")
        title.setObjectName("cardTitle")
        layout.addWidget(eyebrow)
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
        layout.setContentsMargins(15, 13, 15, 14)
        layout.setSpacing(8)
        row = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        eyebrow = QLabel("ENTITY MATCH")
        eyebrow.setObjectName("sectionEyebrow")
        title = QLabel("Web candidates")
        title.setObjectName("cardTitle")
        title_box.addWidget(eyebrow)
        title_box.addWidget(title)
        self.web_hint = QLabel("same / different / uncertain")
        self.web_hint.setObjectName("cardHint")
        row.addLayout(title_box)
        row.addStretch(1)
        row.addWidget(self.web_hint, 0, Qt.AlignBottom)
        layout.addLayout(row)
        self.web_table = QTableWidget(0, 3)
        self.web_table.setHorizontalHeaderLabels(["判定", "来源", "原因"])
        self.web_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.web_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.web_table.verticalHeader().setVisible(False)
        self.web_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.web_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.web_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.web_table.setMinimumHeight(176)
        self.web_table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.web_table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        layout.addWidget(self.web_table)
        return card

    def _build_safety_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("glassCard")
        layout = QGridLayout(card)
        layout.setContentsMargins(15, 13, 15, 14)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(7)
        eyebrow = QLabel("ZERO-WRITE CONTRACT")
        eyebrow.setObjectName("sectionEyebrow")
        title = QLabel("Makro write safety")
        title.setObjectName("cardTitle")
        layout.addWidget(eyebrow, 0, 0, 1, 2)
        layout.addWidget(title, 1, 0, 1, 2)
        self.write_value = self._safety_value("NO / 0")
        self.save_value = self._safety_value("NO")
        self.qc_value = self._safety_value("NO")
        layout.addWidget(QLabel("Makro Write"), 2, 0)
        layout.addWidget(self.write_value, 2, 1)
        layout.addWidget(QLabel("Save"), 3, 0)
        layout.addWidget(self.save_value, 3, 1)
        layout.addWidget(QLabel("Send to QC"), 4, 0)
        layout.addWidget(self.qc_value, 4, 1)
        return card

    def _safety_value(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        label.setStyleSheet("color: #8fe1b9; font-weight: 760;")
        return label

    def _build_log_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("heroCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(15, 11, 15, 13)
        layout.setSpacing(7)
        row = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        eyebrow = QLabel("LIVE CONSOLE")
        eyebrow.setObjectName("sectionEyebrow")
        title = QLabel("实时运行日志")
        title.setObjectName("cardTitle")
        title_box.addWidget(eyebrow)
        title_box.addWidget(title)
        clear_button = QPushButton("清空显示")
        clear_button.setObjectName("quietButton")
        clear_button.clicked.connect(lambda: self.log_view.clear())
        row.addLayout(title_box)
        row.addStretch(1)
        row.addWidget(clear_button, 0, Qt.AlignBottom)
        layout.addLayout(row)
        self.log_view = SmoothLogView()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(8000)
        self.log_view.setMinimumHeight(165)
        self.log_view.setMaximumHeight(220)
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
        self.ready_card.set_value(result.ready, "#8fe1b9")
        self.missing_card.set_value(result.missing, "#f4cb7a")
        self.conflict_card.set_value(result.conflict, "#f18da0")
        self.blocked_card.set_value(result.blocked, "#e796ae")
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
        color = "#f18da0" if bad else "#8fe1b9"
        for label in (self.write_value, self.save_value, self.qc_value):
            label.setStyleSheet(f"color: {color}; font-weight: 760;")

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
