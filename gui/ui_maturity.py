from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import QAbstractItemView, QBoxLayout, QFrame, QMainWindow, QPushButton, QSplitter, QTabWidget, QTableWidget, QToolButton, QVBoxLayout, QWidget

_EXPANDABLE_NAMES = {"glassCard", "heroCard", "statusCard", "microCard", "consolePhaseUnit"}
_EXPAND_SAFE_RIGHT = 38

_MATURE_STYLE = r"""
QWidget#root { font-size: 12px; }
QLabel#brandMark { font-size: 9px; color: rgba(255,255,255,148); }
QLabel#appTitle { font-size: 25px; font-weight: 720; }
QLabel#subtle { font-size: 10px; color: rgba(255,255,255,154); }
QLabel#sectionEyebrow, QLabel#consoleEyebrow { font-size: 9px; color: rgba(255,255,255,126); }
QLabel#cardTitle { font-size: 14px; font-weight: 700; }
QLabel#cardHint, QLabel#consoleHint { font-size: 10px; color: rgba(255,255,255,156); }
QPushButton { min-height: 33px; padding-left: 13px; padding-right: 13px; }
QLineEdit, QSpinBox, QComboBox { min-height: 33px; }
QTableWidget { background-color: rgba(0,0,0,54); }
QTableWidget::item { padding: 7px 9px; }
QHeaderView::section { min-height: 37px; padding-left: 9px; padding-right: 9px; background-color: rgba(255,255,255,25); }
QToolButton#cardExpandButton { min-width: 20px; max-width: 20px; min-height: 20px; max-height: 20px; padding: 0; margin: 0; color: rgba(255,255,255,160); background-color: rgba(0,0,0,30); border: 1px solid rgba(255,255,255,14); border-radius: 6px; font-size: 11px; font-weight: 650; }
QToolButton#cardExpandButton:hover { color: #ffffff; background-color: rgba(255,255,255,30); border-color: rgba(255,255,255,30); }
QToolButton#cardExpandButton:pressed { background-color: rgba(255,255,255,20); }
QTabWidget#sideDetailTabs::pane { border: 1px solid rgba(255,255,255,12); border-radius: 9px; background-color: rgba(0,0,0,22); top: -1px; }
QTabWidget#sideDetailTabs QTabBar { background: transparent; }
QTabWidget#sideDetailTabs QTabBar::tab { min-height: 27px; margin: 0 4px 5px 0; padding: 0 12px; color: rgba(255,255,255,152); background-color: rgba(0,0,0,28); border: 1px solid rgba(255,255,255,10); border-radius: 7px; }
QTabWidget#sideDetailTabs QTabBar::tab:selected { color: #ffffff; background-color: rgba(255,255,255,30); border-color: rgba(255,255,255,20); }
QTabWidget#sideDetailTabs QTabBar::tab:hover { color: #ffffff; background-color: rgba(255,255,255,20); }
QFrame#consolePhaseUnit { background-color: rgba(0,0,0,42); border: 1px solid rgba(255,255,255,12); border-radius: 8px; }
QFrame#consolePhaseUnit QLabel#consoleHint { font-size: 9px; }
QFrame#acceptanceConsole QTabWidget::pane { background-color: rgba(0,0,0,32); border: 1px solid rgba(255,255,255,12); border-radius: 7px; }
QFrame#acceptanceConsole QTabBar::tab { min-height: 25px; padding: 0 11px; margin-right: 3px; background-color: rgba(0,0,0,26); border-radius: 6px; }
QFrame#acceptanceConsole QTabBar::tab:selected { background-color: rgba(255,255,255,28); }
QFrame#acceptanceConsole QPlainTextEdit#consoleText { background-color: rgba(0,0,0,66); }
"""

def _reserve_expand_lane(window: QMainWindow) -> None:
    for frame in window.findChildren(QFrame):
        if frame.objectName() not in _EXPANDABLE_NAMES:
            continue
        layout = frame.layout()
        if layout is None:
            continue
        margins = layout.contentsMargins()
        right = max(margins.right(), _EXPAND_SAFE_RIGHT)
        if right != margins.right():
            layout.setContentsMargins(margins.left(), margins.top(), right, margins.bottom())
    for button in window.findChildren(QToolButton, "cardExpandButton"):
        button.setText("⤢")
        button.setToolTip("展开详情")
        if button.size().width() != 20 or button.size().height() != 20:
            button.setFixedSize(20, 20)
        parent = button.parentWidget()
        if parent is not None:
            target_x = max(5, parent.width() - 27)
            if button.x() != target_x or button.y() != 7:
                button.move(target_x, 7)
            button.raise_()

def _polish_tabs(tabs: QTabWidget | None, *, expanding: bool) -> None:
    if not isinstance(tabs, QTabWidget): return
    bar = tabs.tabBar(); bar.setDrawBase(False); bar.setExpanding(expanding); bar.setUsesScrollButtons(False); bar.setElideMode(Qt.TextElideMode.ElideRight); tabs.setDocumentMode(True)

def _polish_tables(window: QMainWindow) -> None:
    field_table = getattr(window, "field_table", None)
    if isinstance(field_table, QTableWidget):
        field_table.verticalHeader().setDefaultSectionSize(38); field_table.verticalHeader().setMinimumSectionSize(36); field_table.horizontalHeader().setMinimumHeight(37); field_table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel); field_table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    web_table = getattr(window, "web_table", None)
    if isinstance(web_table, QTableWidget): web_table.verticalHeader().setDefaultSectionSize(37); web_table.horizontalHeader().setMinimumHeight(36)
    console = getattr(window, "console", None)
    if isinstance(console, QWidget):
        for table in console.findChildren(QTableWidget): table.verticalHeader().setDefaultSectionSize(35); table.horizontalHeader().setMinimumHeight(35)

def _polish_status_cards(window: QMainWindow) -> None:
    for name in ("ready_card", "missing_card", "conflict_card", "blocked_card"):
        card = getattr(window, name, None)
        if isinstance(card, QFrame):
            card.setMinimumHeight(72); card.setMaximumHeight(76)
            layout = card.layout()
            if isinstance(layout, QBoxLayout): layout.setSpacing(1); layout.setContentsMargins(15, 8, _EXPAND_SAFE_RIGHT, 8)

def _polish_input_card(window: QMainWindow) -> None:
    url_input = getattr(window, "url_input", None); input_card = url_input.parentWidget() if isinstance(url_input, QWidget) else None
    while isinstance(input_card, QWidget) and not (isinstance(input_card, QFrame) and input_card.objectName() == "heroCard"): input_card = input_card.parentWidget()
    if not isinstance(input_card, QFrame): return
    layout = input_card.layout()
    if isinstance(layout, QVBoxLayout): layout.setSpacing(7); layout.setContentsMargins(18, 11, _EXPAND_SAFE_RIGHT, 12)
    for name in ("step1_button", "step2_button", "step3_button"):
        button = getattr(window, name, None)
        if isinstance(button, QPushButton): button.setMinimumWidth(132); button.setMaximumWidth(158)
    start = getattr(window, "start_button", None); stop = getattr(window, "stop_button", None)
    if isinstance(start, QPushButton): start.setMinimumWidth(148)
    if isinstance(stop, QPushButton): stop.setMinimumWidth(58); stop.setMaximumWidth(74)

def _polish_workspace(window: QMainWindow) -> None:
    root = window.centralWidget(); splitter = root.findChild(QSplitter, "workspaceSplitter") if root is not None else None
    if not isinstance(splitter, QSplitter): return
    splitter.setHandleWidth(8); splitter.setStretchFactor(0, 1); splitter.setStretchFactor(1, 0)
    if splitter.count() > 1:
        side = splitter.widget(1); side.setMinimumWidth(300); side.setMaximumWidth(370)
    _polish_tabs(getattr(window, "side_detail_tabs", None), expanding=True)

def _polish_console(window: QMainWindow) -> None:
    console = getattr(window, "console", None)
    if not isinstance(console, QFrame): return
    layout = console.layout()
    if isinstance(layout, QVBoxLayout): layout.setSpacing(6); layout.setContentsMargins(16, 10, _EXPAND_SAFE_RIGHT, 11)
    phase_units = list(getattr(console, "phase_units", {}).values())
    for unit in phase_units:
        if isinstance(unit, QFrame):
            unit.setMinimumHeight(56); unit.setMaximumHeight(62); unit_layout = unit.layout()
            if isinstance(unit_layout, QVBoxLayout): unit_layout.setSpacing(1); unit_layout.setContentsMargins(10, 6, _EXPAND_SAFE_RIGHT, 6)
    tabs = getattr(console, "tabs", None); _polish_tabs(tabs, expanding=False)
    if isinstance(tabs, QTabWidget): tabs.setMinimumHeight(118)

class MatureResponsiveController(QObject):
    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window); self.window = window; self.root = window.centralWidget()
        if self.root is None: raise RuntimeError("mature UI requires a central widget")
        self._timer = QTimer(self); self._timer.setSingleShot(True); self._timer.setInterval(32); self._timer.timeout.connect(self.apply); self.root.installEventFilter(self)
        for name in ("console_detail_toggle", "real_settings_toggle"):
            toggle = getattr(window, name, None)
            if isinstance(toggle, QPushButton): toggle.toggled.connect(lambda *_: self.schedule())
        self.schedule()
    def schedule(self) -> None:
        if not self._timer.isActive(): self._timer.start()
    def _body_splitter(self) -> QSplitter | None:
        value = getattr(self.window, "_ui_polish_body_splitter", None); return value if isinstance(value, QSplitter) else None
    def _workspace_splitter(self) -> QSplitter | None: return self.root.findChild(QSplitter, "workspaceSplitter")
    @staticmethod
    def _set_splitter_sizes_if_needed(splitter: QSplitter, target: list[int]) -> None:
        current = splitter.sizes()
        if len(current) != len(target) or any(abs(a - b) > 3 for a, b in zip(current, target)): splitter.setSizes(target)
    def apply(self) -> None:
        _reserve_expand_lane(self.window); width = max(1, self.root.width()); height = max(1, self.root.height())
        workspace_splitter = self._workspace_splitter()
        if isinstance(workspace_splitter, QSplitter) and workspace_splitter.count() > 1:
            total = max(1, workspace_splitter.width() - workspace_splitter.handleWidth()); side_target = 330 if width >= 1500 else 310; side_target = min(side_target, max(280, int(total * 0.28))); self._set_splitter_sizes_if_needed(workspace_splitter, [max(560, total - side_target), side_target])
        body = self._body_splitter(); console = getattr(self.window, "console", None)
        if isinstance(body, QSplitter) and isinstance(console, QWidget):
            available = max(1, body.height() - body.handleWidth()); toggle = getattr(self.window, "console_detail_toggle", None); expanded = isinstance(toggle, QPushButton) and toggle.isChecked()
            if expanded:
                target = int(available * (0.34 if height >= 980 else 0.31)); target = min(350, max(260, target)); console.setMinimumHeight(230); console.setMaximumHeight(390)
            else:
                target = 116; console.setMinimumHeight(108); console.setMaximumHeight(124)
            self._set_splitter_sizes_if_needed(body, [max(300, available - target), target])
        for button in self.window.findChildren(QToolButton, "cardExpandButton"):
            parent = button.parentWidget()
            if parent is not None:
                target_x = max(5, parent.width() - 27)
                if button.x() != target_x or button.y() != 7: button.move(target_x, 7)
                button.raise_()
        visual = getattr(self.window, "_visual_style", None); background = getattr(visual, "background", None)
        if background is not None and hasattr(background, "schedule_mask_update"): background.schedule_mask_update()
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self.root and event.type() in {QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.LayoutRequest}: self.schedule()
        return False
    def cleanup(self) -> None:
        self._timer.stop()
        try: self.root.removeEventFilter(self)
        except RuntimeError: pass

def install_mature_ui(window: QMainWindow) -> MatureResponsiveController:
    root = window.centralWidget()
    if root is None: raise RuntimeError("mature UI requires a central widget")
    window.setStyleSheet(window.styleSheet() + "\n" + _MATURE_STYLE)
    outer = root.layout()
    if isinstance(outer, QVBoxLayout): outer.setContentsMargins(18, 16, 18, 16); outer.setSpacing(9)
    _polish_input_card(window); _polish_status_cards(window); _polish_workspace(window); _polish_console(window); _polish_tables(window); _reserve_expand_lane(window)
    controller = MatureResponsiveController(window); window._mature_ui = controller  # type: ignore[attr-defined]
    window.destroyed.connect(controller.cleanup); return controller
