from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QMainWindow,
    QPushButton,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .overlay_sheet_motion import ClipSheetMotion


_SHEET_MARGIN = 18
_SHEET_GAP = 8
_GEOMETRY_COALESCE_MS = 24

_SHEET_STYLE = r"""
QFrame#anchoredOverlaySheet {
    background-color: rgba(17,17,19,238);
    border: 1px solid rgba(255,255,255,30);
    border-radius: 12px;
}
QLabel#anchoredSheetEyebrow {
    color: rgba(255,255,255,126);
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 1px;
}
QLabel#anchoredSheetTitle {
    color: #ffffff;
    font-size: 15px;
    font-weight: 720;
}
QLabel#anchoredSheetHint {
    color: rgba(255,255,255,154);
    font-size: 10px;
}
QToolButton#anchoredSheetClose {
    min-width: 26px;
    max-width: 26px;
    min-height: 26px;
    max-height: 26px;
    padding: 0;
    color: rgba(255,255,255,196);
    background-color: rgba(255,255,255,14);
    border: 1px solid rgba(255,255,255,16);
    border-radius: 7px;
    font-size: 15px;
}
QToolButton#anchoredSheetClose:hover {
    color: #ffffff;
    background-color: rgba(255,255,255,28);
}
"""


def _detach_widget(layout: QLayout | None, widget: QWidget) -> bool:
    if layout is None:
        return False
    for index in range(layout.count() - 1, -1, -1):
        item = layout.itemAt(index)
        if item.widget() is widget:
            layout.takeAt(index)
            return True
        child = item.layout()
        if child is not None and _detach_widget(child, widget):
            return True
    return False


def _hero_card_for(window: QMainWindow) -> QFrame | None:
    current = getattr(window, "url_input", None)
    current = current.parentWidget() if isinstance(current, QWidget) else None
    while isinstance(current, QWidget):
        if isinstance(current, QFrame) and current.objectName() == "heroCard":
            return current
        current = current.parentWidget()
    return None


def _header(sheet: QFrame, eyebrow: str, title: str, hint: str) -> tuple[QVBoxLayout, QToolButton]:
    layout = QVBoxLayout(sheet)
    layout.setContentsMargins(16, 13, 16, 15)
    layout.setSpacing(9)

    row = QHBoxLayout()
    row.setSpacing(10)
    title_box = QVBoxLayout()
    title_box.setSpacing(1)
    eyebrow_label = QLabel(eyebrow)
    eyebrow_label.setObjectName("anchoredSheetEyebrow")
    title_label = QLabel(title)
    title_label.setObjectName("anchoredSheetTitle")
    title_box.addWidget(eyebrow_label)
    title_box.addWidget(title_label)
    row.addLayout(title_box)
    hint_label = QLabel(hint)
    hint_label.setObjectName("anchoredSheetHint")
    hint_label.setWordWrap(True)
    row.addWidget(hint_label, 1, Qt.AlignmentFlag.AlignBottom)
    close = QToolButton()
    close.setObjectName("anchoredSheetClose")
    close.setText("×")
    close.setToolTip("关闭")
    row.addWidget(close, 0, Qt.AlignmentFlag.AlignTop)
    layout.addLayout(row)
    return layout, close


class AnchoredSheetController(QObject):
    """Settings stay in an anchored sheet; Console stays expanded in-place."""

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self.window = window
        self.root = window.centralWidget()
        if self.root is None:
            raise RuntimeError("anchored sheets require a central widget")

        window._anchored_sheets_active = True  # type: ignore[attr-defined]
        window._console_summary_mode = True  # type: ignore[attr-defined]
        self._real_open = False
        self.root.setStyleSheet(self.root.styleSheet() + "\n" + _SHEET_STYLE)
        self._geometry_timer = QTimer(self)
        self._geometry_timer.setSingleShot(True)
        self._geometry_timer.setInterval(_GEOMETRY_COALESCE_MS)
        self._geometry_timer.timeout.connect(self._sync_geometry)
        self.root.installEventFilter(self)

        self.real_toggle = getattr(window, "real_settings_toggle", None)
        self.console_toggle = getattr(window, "console_detail_toggle", None)
        self.real_motion = self._build_real_sheet()
        self._prepare_console_summary()

        window.destroyed.connect(self.cleanup)
        QTimer.singleShot(0, self._sync_geometry)
        QTimer.singleShot(0, self._sync_console_summary_geometry)

    def _new_sheet(self) -> QFrame:
        sheet = QFrame(self.root)
        sheet.setObjectName("anchoredOverlaySheet")
        sheet.hide()
        return sheet

    def _real_rect(self) -> QRect:
        anchor = _hero_card_for(self.window)
        if anchor is None:
            return QRect(_SHEET_MARGIN, 120, max(320, self.root.width() - 2 * _SHEET_MARGIN), 156)
        top_left = anchor.mapTo(self.root, QPoint(0, 0))
        width = min(anchor.width(), max(320, self.root.width() - 2 * _SHEET_MARGIN))
        x = max(_SHEET_MARGIN, min(top_left.x(), self.root.width() - width - _SHEET_MARGIN))
        natural = self.real_sheet.sizeHint().height() if hasattr(self, "real_sheet") else 156
        height = max(136, min(188, natural))
        below = top_left.y() + anchor.height() + _SHEET_GAP
        if below + height + _SHEET_MARGIN <= self.root.height():
            y = below
        else:
            y = max(_SHEET_MARGIN, top_left.y() - height - _SHEET_GAP)
        return QRect(x, y, width, height)

    def _build_real_sheet(self) -> ClipSheetMotion | None:
        toggle = self.real_toggle
        if not isinstance(toggle, QPushButton):
            return None

        names = (
            "real_scope_combo",
            "real_save_check",
            "real_upload_check",
            "real_pick_images_button",
            "real_image_count",
            "real_qc_check",
            "real_policy_hint",
            "real_start_button",
            "real_stop_button",
        )
        widgets = {name: getattr(self.window, name, None) for name in names}
        if not all(isinstance(value, QWidget) for value in widgets.values()):
            return None

        anchor = _hero_card_for(self.window)
        if anchor is None or anchor.layout() is None:
            return None
        for widget in widgets.values():
            assert isinstance(widget, QWidget)
            _detach_widget(anchor.layout(), widget)

        self.real_sheet = self._new_sheet()
        layout, close = _header(
            self.real_sheet,
            "REAL EXECUTION · SETTINGS",
            "真实填写设置",
            "设置从商品来源卡片延伸展开；主工作区尺寸保持不变。",
        )

        controls = QHBoxLayout()
        controls.setSpacing(9)
        for name in (
            "real_scope_combo",
            "real_save_check",
            "real_upload_check",
            "real_pick_images_button",
            "real_image_count",
            "real_qc_check",
        ):
            widget = widgets[name]
            assert isinstance(widget, QWidget)
            widget.setParent(self.real_sheet)
            widget.show()
            controls.addWidget(widget)
        controls.addStretch(1)
        layout.addLayout(controls)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        policy = widgets["real_policy_hint"]
        start = widgets["real_start_button"]
        stop = widgets["real_stop_button"]
        assert isinstance(policy, QWidget) and isinstance(start, QWidget) and isinstance(stop, QWidget)
        for widget in (policy, start, stop):
            widget.setParent(self.real_sheet)
            widget.show()
        actions.addWidget(policy, 1)
        actions.addSpacing(10)
        actions.addWidget(start)
        actions.addWidget(stop)
        layout.addLayout(actions)

        try:
            toggle.toggled.disconnect()
        except (RuntimeError, TypeError):
            pass
        toggle.setChecked(False)
        toggle.setCheckable(False)
        toggle.setText("展开设置 ﹀")
        close.clicked.connect(lambda: self._set_real_sheet(False))

        motion = ClipSheetMotion(self.root, self.real_sheet, self._real_rect, edge="top", duration_ms=162)
        toggle.clicked.connect(lambda: self._set_real_sheet(not self._real_open))
        return motion

    def _prepare_console_summary(self) -> None:
        toggle = self.console_toggle
        console = getattr(self.window, "console", None)
        if not isinstance(toggle, QPushButton) or not isinstance(console, QFrame):
            return

        # ui_polish originally wired this button to hide/show phase cards and
        # resize the splitter. Disconnect that whole reflow path. The button is
        # kept logically checked so ui_maturity continues to allocate the old
        # expanded Console height, while clicks are free to open Focus Mode.
        try:
            toggle.toggled.disconnect()
        except (RuntimeError, TypeError):
            pass
        toggle.setCheckable(True)
        toggle.setChecked(True)
        toggle.setText("展开详情 ⌄")
        toggle.clicked.connect(self._restore_console_summary_toggle)

        for unit in getattr(console, "phase_units", {}).values():
            if isinstance(unit, QWidget):
                unit.show()
        tabs = getattr(console, "tabs", None)
        if isinstance(tabs, QWidget):
            tabs.show()

        console.setMinimumHeight(300)
        console.setMaximumHeight(460)

    def _restore_console_summary_toggle(self, *_args: object) -> None:
        toggle = self.console_toggle
        if not isinstance(toggle, QPushButton):
            return
        # QAbstractButton toggles before clicked(). Restore the internal checked
        # state immediately; no layout handler is attached to toggled anymore.
        if not toggle.isChecked():
            toggle.setChecked(True)
        toggle.setText("展开详情 ⌄")

    def _sync_console_summary_geometry(self) -> None:
        body = getattr(self.window, "_ui_polish_body_splitter", None)
        console = getattr(self.window, "console", None)
        if not isinstance(body, QSplitter) or not isinstance(console, QWidget):
            return
        available = max(1, body.height() - body.handleWidth())
        target = min(440, max(340, available - 300))
        target = min(target, max(300, available - 260))
        console.setMinimumHeight(300)
        console.setMaximumHeight(460)
        body.setSizes([max(260, available - target), target])

    def _set_real_sheet(self, expanded: bool) -> None:
        expanded = bool(expanded)
        if expanded:
            details = getattr(self.window, "_card_details", None)
            if details is not None and hasattr(details, "close"):
                details.close()
        self._real_open = expanded
        if isinstance(self.real_toggle, QPushButton):
            self.real_toggle.setText("收起设置 ︿" if expanded else "展开设置 ﹀")
        if self.real_motion is not None:
            self.real_motion.toggle(expanded)

    def close_all(self) -> None:
        self._set_real_sheet(False)

    def _schedule_geometry(self) -> None:
        if not self._geometry_timer.isActive():
            self._geometry_timer.start()

    def _sync_geometry(self) -> None:
        if self.real_motion is not None:
            self.real_motion.sync_geometry()
        self._sync_console_summary_geometry()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self.root:
            if event.type() in {QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.LayoutRequest}:
                self._schedule_geometry()
            elif event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
                if event.key() == Qt.Key.Key_Escape:
                    self.close_all()
        return False

    def cleanup(self) -> None:
        self._geometry_timer.stop()
        if self.real_motion is not None:
            self.real_motion.cleanup()
        try:
            self.root.removeEventFilter(self)
        except RuntimeError:
            pass


def install_anchored_sheets(window: QMainWindow) -> AnchoredSheetController:
    controller = AnchoredSheetController(window)
    window._anchored_sheets = controller  # type: ignore[attr-defined]
    return controller
