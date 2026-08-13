"""Fixed Single-workspace layout for the formal GUI.

Presentation only: keep the existing Single widgets and business wiring, but stop
wrapping the whole page in an outer QScrollArea. The field table and diagnostic
views keep their own native scroll areas; top-level glass cards remain stationary.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QBoxLayout,
    QFrame,
    QLayout,
    QMainWindow,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)


_INPUT_CARD_MAX_HEIGHT = 160
_STATUS_CARD_MIN_HEIGHT = 60
_STATUS_CARD_MAX_HEIGHT = 64
_WORKSPACE_MIN_HEIGHT = 260
_FIELD_TABLE_MIN_HEIGHT = 170
_SIDE_MIN_WIDTH = 360
_SIDE_MAX_WIDTH = 480
_SIDE_TARGET_RATIO = 0.29
_CONSOLE_MIN_HEIGHT = 218
_CONSOLE_MAX_HEIGHT = 242
_CONSOLE_TARGET_HEIGHT = 230


def _ancestor_card(widget: QWidget | None, object_name: str) -> QFrame | None:
    current = widget
    while current is not None:
        if isinstance(current, QFrame) and current.objectName() == object_name:
            return current
        current = current.parentWidget()
    return None


def _contains_widget(layout: QLayout | None, target: QWidget) -> bool:
    if layout is None:
        return False
    for index in range(layout.count()):
        item = layout.itemAt(index)
        if item.widget() is target:
            return True
        child = item.layout()
        if child is not None and _contains_widget(child, target):
            return True
    return False


def _direct_box_layout_containing(
    parent: QVBoxLayout,
    target: QWidget | None,
) -> QBoxLayout | None:
    if target is None:
        return None
    for index in range(parent.count()):
        candidate = parent.itemAt(index).layout()
        if isinstance(candidate, QBoxLayout) and _contains_widget(candidate, target):
            return candidate
    return None


def _remove_spacers(layout: QBoxLayout) -> None:
    for index in range(layout.count() - 1, -1, -1):
        item = layout.itemAt(index)
        if item.spacerItem() is not None:
            layout.takeAt(index)


def _compact_input_rows(window: QMainWindow, input_card: QFrame) -> None:
    """Put stage buttons and connection controls on one row.

    This removes one complete control row without rebuilding any business widget
    or reconnecting signals. The real-execution summary row remains below it.
    """

    if getattr(window, "_single_fixed_input_rows", False):
        return
    layout = input_card.layout()
    if not isinstance(layout, QVBoxLayout):
        return

    stage_anchor = getattr(window, "step1_button", None)
    settings_anchor = getattr(window, "source_port", None)
    if not isinstance(stage_anchor, QWidget) or not isinstance(settings_anchor, QWidget):
        return

    stage_row = _direct_box_layout_containing(layout, stage_anchor)
    settings_row = _direct_box_layout_containing(layout, settings_anchor)
    if stage_row is None or settings_row is None or stage_row is settings_row:
        return

    _remove_spacers(stage_row)
    _remove_spacers(settings_row)

    moved = False
    for name in ("makro_port", "source_port", "vertical_input", "current_page_check"):
        widget = getattr(window, name, None)
        if not isinstance(widget, QWidget) or not _contains_widget(settings_row, widget):
            continue
        settings_row.removeWidget(widget)
        if not moved:
            stage_row.addSpacing(8)
            moved = True
        stage_row.addWidget(widget)

    if moved:
        stage_row.addStretch(1)
        stage_row.setSpacing(7)
        settings_row.setSpacing(0)
        settings_row.setContentsMargins(0, 0, 0, 0)
        setattr(window, "_single_fixed_input_rows", True)


def _compact_input_card(window: QMainWindow, input_card: QFrame) -> None:
    _compact_input_rows(window, input_card)
    layout = input_card.layout()
    if isinstance(layout, QVBoxLayout):
        layout.setContentsMargins(14, 7, 14, 8)
        layout.setSpacing(3)
    input_card.setMinimumHeight(0)
    input_card.setMaximumHeight(_INPUT_CARD_MAX_HEIGHT)
    input_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)


def _compact_status_cards(window: QMainWindow) -> None:
    for name in ("ready_card", "missing_card", "conflict_card", "blocked_card"):
        card = getattr(window, name, None)
        if not isinstance(card, QFrame):
            continue
        card.setMinimumHeight(_STATUS_CARD_MIN_HEIGHT)
        card.setMaximumHeight(_STATUS_CARD_MAX_HEIGHT)
        layout = card.layout()
        if isinstance(layout, QBoxLayout):
            layout.setContentsMargins(12, 4, 12, 4)
            layout.setSpacing(0)


def _strip_trailing_spacers(layout: QBoxLayout) -> None:
    for index in range(layout.count() - 1, -1, -1):
        item = layout.itemAt(index)
        if item.spacerItem() is not None:
            layout.takeAt(index)


def _configure_workspace(window: QMainWindow, body: QSplitter) -> None:
    workspace = body.widget(0) if body.count() > 0 else None
    console = body.widget(1) if body.count() > 1 else None
    if not isinstance(workspace, QWidget) or not isinstance(console, QWidget):
        raise RuntimeError("fixed Single layout requires workspace + console in bodySplitter")

    workspace.setMinimumHeight(_WORKSPACE_MIN_HEIGHT)
    workspace.setMaximumHeight(16777215)
    workspace.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    field_table = getattr(window, "field_table", None)
    if isinstance(field_table, QWidget):
        field_table.setMinimumHeight(_FIELD_TABLE_MIN_HEIGHT)

    workspace_splitter = workspace.findChild(QSplitter, "workspaceSplitter")
    if isinstance(workspace_splitter, QSplitter) and workspace_splitter.count() > 1:
        side = workspace_splitter.widget(1)
        side.setMinimumWidth(_SIDE_MIN_WIDTH)
        side.setMaximumWidth(_SIDE_MAX_WIDTH)
        total = max(1, workspace_splitter.width() - workspace_splitter.handleWidth())
        side_target = min(
            _SIDE_MAX_WIDTH,
            max(_SIDE_MIN_WIDTH, round(total * _SIDE_TARGET_RATIO)),
        )
        workspace_splitter.setSizes([max(620, total - side_target), side_target])

    side_tabs = getattr(window, "side_detail_tabs", None)
    if isinstance(side_tabs, QTabWidget):
        # The old scroll-page launcher forced this panel to 300 px and inserted a
        # stretch below it. In the fixed page it should fill the workspace height
        # exactly like the field card beside it.
        side_tabs.setMinimumHeight(0)
        side_tabs.setMaximumHeight(16777215)
        side_tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        side_host = side_tabs.parentWidget()
        side_layout = side_host.layout() if side_host is not None else None
        if isinstance(side_layout, QVBoxLayout):
            _strip_trailing_spacers(side_layout)
            side_layout.setStretchFactor(side_tabs, 1)
            side_layout.setAlignment(side_tabs, Qt.AlignmentFlag(0))

    console.setMinimumHeight(_CONSOLE_MIN_HEIGHT)
    console.setMaximumHeight(_CONSOLE_MAX_HEIGHT)
    console.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    body.setHandleWidth(7)
    body.setChildrenCollapsible(False)
    body.setStretchFactor(0, 1)
    body.setStretchFactor(1, 0)
    available = max(1, body.height() - body.handleWidth())
    target = min(_CONSOLE_MAX_HEIGHT, max(_CONSOLE_MIN_HEIGHT, _CONSOLE_TARGET_HEIGHT))
    body.setSizes([max(_WORKSPACE_MIN_HEIGHT, available - target), target])


def install_page_scroll_layout(
    window: QMainWindow,
    visual: Any | None = None,
) -> QSplitter:
    """Keep Single as a fixed one-screen workspace.

    The historical function name is retained so ``run_local_gui.py`` and existing
    callers do not need a migration. No outer Single QScrollArea is created.
    """

    existing = getattr(window, "_single_fixed_body", None)
    if isinstance(existing, QSplitter):
        return existing

    root = window.centralWidget()
    outer = root.layout() if root is not None else None
    if root is None or not isinstance(outer, QVBoxLayout):
        raise RuntimeError("fixed Single layout requires the preserved root QVBoxLayout")

    url_input = getattr(window, "url_input", None)
    input_card = _ancestor_card(
        url_input if isinstance(url_input, QWidget) else None,
        "heroCard",
    )
    body = getattr(window, "_ui_polish_body_splitter", None)
    if not isinstance(input_card, QFrame):
        raise RuntimeError("fixed Single layout could not resolve the Product Source card")
    if not isinstance(body, QSplitter) or body.count() < 2:
        raise RuntimeError("fixed Single layout requires the polished bodySplitter")

    # Keep the original root ownership. No card is reparented into an outer
    # viewport, so top-level glass geometry remains stationary during normal use.
    outer.setSpacing(7)
    _compact_input_card(window, input_card)
    _compact_status_cards(window)
    _configure_workspace(window, body)

    background = getattr(visual, "background", None)
    if background is None:
        background = getattr(getattr(window, "_visual_style", None), "background", None)
    schedule = getattr(background, "schedule_mask_update", None)
    if callable(schedule):
        QTimer.singleShot(0, schedule)

    setattr(window, "_single_fixed_body", body)
    return body
