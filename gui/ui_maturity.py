from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QBoxLayout,
    QFrame,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QTabWidget,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

# Typography and control polish only. Page geometry belongs to
# gui/workspace_composer.py; this module never sizes splitters or cards at runtime.

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
QTabWidget#sideDetailTabs::pane { border: 1px solid rgba(255,255,255,12); border-radius: 9px; background-color: rgba(0,0,0,22); top: -1px; }
QTabWidget#sideDetailTabs QTabBar { background: transparent; }
QTabWidget#sideDetailTabs QTabBar::tab { min-height: 27px; margin: 0 4px 5px 0; padding: 0 12px; color: rgba(255,255,255,152); background-color: rgba(0,0,0,28); border: 1px solid rgba(255,255,255,10); border-radius: 7px; }
QTabWidget#sideDetailTabs QTabBar::tab:selected { color: #ffffff; background-color: rgba(255,255,255,30); border-color: rgba(255,255,255,20); }
QTabWidget#sideDetailTabs QTabBar::tab:hover { color: #ffffff; background-color: rgba(255,255,255,20); }
QFrame#consolePhaseUnit { background-color: rgba(0,0,0,42); border: 1px solid rgba(255,255,255,12); border-radius: 8px; }
QFrame#consolePhaseUnit QLabel#consoleHint { font-size: 9px; }
QFrame#acceptanceConsole QTabWidget::pane { background-color: rgba(0,0,0,32); border: 1px solid rgba(255,255,255,12); border-radius: 7px; }
QFrame#acceptanceConsole QTabBar::tab { min-height: 25px; padding: 0 12px; margin-right: 3px; background-color: rgba(0,0,0,26); border-radius: 6px; }
QFrame#acceptanceConsole QTabBar::tab:selected { background-color: rgba(255,255,255,28); }
QFrame#acceptanceConsole QPlainTextEdit#consoleText { background-color: rgba(0,0,0,66); }
"""


def _polish_tabs(tabs: QTabWidget | None, *, expanding: bool) -> None:
    if not isinstance(tabs, QTabWidget):
        return
    bar = tabs.tabBar()
    bar.setDrawBase(False)
    bar.setExpanding(expanding)
    bar.setUsesScrollButtons(False)
    bar.setElideMode(Qt.TextElideMode.ElideRight)
    tabs.setDocumentMode(True)


def _polish_tables(window: QMainWindow) -> None:
    field_table = getattr(window, "field_table", None)
    if isinstance(field_table, QTableWidget):
        field_table.verticalHeader().setDefaultSectionSize(38)
        field_table.verticalHeader().setMinimumSectionSize(36)
        field_table.horizontalHeader().setMinimumHeight(37)
        field_table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        field_table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    web_table = getattr(window, "web_table", None)
    if isinstance(web_table, QTableWidget):
        web_table.verticalHeader().setDefaultSectionSize(37)
        web_table.horizontalHeader().setMinimumHeight(36)
    console = getattr(window, "console", None)
    if isinstance(console, QWidget):
        for table in console.findChildren(QTableWidget):
            table.verticalHeader().setDefaultSectionSize(35)
            table.horizontalHeader().setMinimumHeight(35)


def _polish_status_cards(window: QMainWindow) -> None:
    for name in ("ready_card", "missing_card", "conflict_card", "blocked_card"):
        card = getattr(window, name, None)
        if not isinstance(card, QFrame):
            continue
        card.setMinimumHeight(72)
        card.setMaximumHeight(76)
        layout = card.layout()
        if isinstance(layout, QBoxLayout):
            layout.setSpacing(1)
            layout.setContentsMargins(15, 8, 15, 8)


def _polish_input_card(window: QMainWindow) -> None:
    url_input = getattr(window, "url_input", None)
    input_card = url_input.parentWidget() if isinstance(url_input, QWidget) else None
    while isinstance(input_card, QWidget) and not (
        isinstance(input_card, QFrame) and input_card.objectName() == "heroCard"
    ):
        input_card = input_card.parentWidget()
    if not isinstance(input_card, QFrame):
        return
    layout = input_card.layout()
    if isinstance(layout, QVBoxLayout):
        layout.setSpacing(7)
        layout.setContentsMargins(18, 11, 18, 12)
    for name in ("step1_button", "step2_button", "step3_button"):
        button = getattr(window, name, None)
        if isinstance(button, QPushButton):
            button.setMinimumWidth(132)
            button.setMaximumWidth(158)
    start = getattr(window, "start_button", None)
    stop = getattr(window, "stop_button", None)
    if isinstance(start, QPushButton):
        start.setMinimumWidth(148)
    if isinstance(stop, QPushButton):
        stop.setMinimumWidth(58)
        stop.setMaximumWidth(74)


def _polish_workspace(window: QMainWindow) -> None:
    _polish_tabs(getattr(window, "side_detail_tabs", None), expanding=True)


def _format_elapsed(value: float) -> str:
    if value <= 0:
        return ""
    if value < 10:
        return f"{value:.2f}s"
    if value < 60:
        return f"{value:.1f}s"
    minutes, seconds = divmod(value, 60.0)
    return f"{int(minutes)}m {seconds:04.1f}s"


def _install_compact_phase_states(window: QMainWindow) -> None:
    console = getattr(window, "console", None)
    if not isinstance(console, QFrame) or getattr(console, "_mature_phase_states", False):
        return

    phase_units: dict[str, Any] = dict(getattr(console, "phase_units", {}))
    for unit in phase_units.values():
        if not isinstance(unit, QFrame):
            continue
        detail = getattr(unit, "detail", None)
        if isinstance(detail, QLabel):
            detail.hide()
        unit.setMinimumHeight(44)
        unit.setMaximumHeight(48)
        unit_layout = unit.layout()
        if isinstance(unit_layout, QVBoxLayout):
            unit_layout.setSpacing(0)
            unit_layout.setContentsMargins(10, 5, 10, 5)

    runner = getattr(console, "runner", None)
    if runner is None:
        return

    def sync_phase(event: dict[str, Any]) -> None:
        phase = str(event.get("phase") or "")
        unit = phase_units.get(phase)
        state_label = getattr(unit, "state", None)
        if not isinstance(state_label, QLabel):
            return
        state = str(event.get("status") or "waiting").upper()
        elapsed = _format_elapsed(float(event.get("elapsed_s") or 0.0))
        state_label.setText(f"{state} · {elapsed}" if elapsed else state)

    def reset_phase(running: bool) -> None:
        if not running:
            return
        for unit in phase_units.values():
            state_label = getattr(unit, "state", None)
            if isinstance(state_label, QLabel):
                state_label.setText("WAITING")

    runner.phase_event.connect(sync_phase)
    runner.running_changed.connect(reset_phase)
    setattr(console, "_mature_phase_states", True)


def _polish_console(window: QMainWindow) -> None:
    console = getattr(window, "console", None)
    if not isinstance(console, QFrame):
        return

    layout = console.layout()
    if isinstance(layout, QVBoxLayout):
        layout.setSpacing(5)
        layout.setContentsMargins(16, 9, 16, 10)

    # The console-level eyebrow is redundant once the panel is expanded; hiding
    # only the direct child saves a full text line without touching phase numbers.
    for label in console.findChildren(QLabel, "consoleEyebrow"):
        if label.parentWidget() is console:
            label.hide()

    _install_compact_phase_states(window)

    tabs = getattr(console, "tabs", None)
    _polish_tabs(tabs, expanding=False)
    if isinstance(tabs, QTabWidget):
        tabs.setMinimumHeight(205)
        tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        short_names = ("Console", "Timeline", "Artifacts", "Diagnostics", "Real Run")
        for index, text in enumerate(short_names):
            if index < tabs.count():
                tabs.setTabText(index, text)

        # Make every actual tab page prefer the viewport instead of collapsing
        # around its internal helper row/table size hints.
        for index in range(tabs.count()):
            page = tabs.widget(index)
            if isinstance(page, QWidget):
                page.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)


def install_mature_ui(window: QMainWindow) -> None:
    root = window.centralWidget()
    if root is None:
        raise RuntimeError("mature UI requires a central widget")
    window.setStyleSheet(window.styleSheet() + "\n" + _MATURE_STYLE)
    _polish_input_card(window)
    _polish_status_cards(window)
    _polish_workspace(window)
    _polish_console(window)
    _polish_tables(window)
