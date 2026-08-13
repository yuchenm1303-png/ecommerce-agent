"""Fixed Single-workspace layout for the formal GUI.

Presentation only: keep the existing Single widgets and business wiring, but stop
wrapping the whole page in an outer QScrollArea. The field table and diagnostic
views keep their own native scroll areas; top-level glass cards remain stationary.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QTimer
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


_INPUT_CARD_MAX_HEIGHT = 176
_STATUS_CARD_MIN_HEIGHT = 68
_STATUS_CARD_MAX_HEIGHT = 72
_WORKSPACE_MIN_HEIGHT = 292
_FIELD_TABLE_MIN_HEIGHT = 205
_SIDE_TABS_HEIGHT = 300
_CONSOLE_MIN_HEIGHT = 120
_CONSOLE_MAX_HEIGHT = 136
_CONSOLE_TARGET_HEIGHT = 128


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

    This removes one complete 33 px control row without rebuilding any business
    widgets or reconnecting signals. The real-execution summary row remains below
    it exactly as before.
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
        layout.setContentsMargins(16, 8, 16, 9)
        layout.setSpacing(4)
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
            layout.setContentsMargins(14, 5, 14, 5)
            layout.setSpacing(0)


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

    side_tabs = getattr(window, "side_detail_tabs", None)
    if isinstance(side_tabs, QTabWidget):
        side_tabs.setMinimumHeight(_SIDE_TABS_HEIGHT)
        side_tabs.setMaximumHeight(_SIDE_TABS_HEIGHT)
        side_tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

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

    # Keep the original root ownership. This is the important architectural
    # difference from the previous scroll page: no card is reparented into an
    # outer viewport, so top-level glass geometry is stationary during normal use.
    outer.setSpacing(8)
    _compact_input_card(window, input_card)
    _compact_status_cards(window)
    _configure_workspace(window, body)

    # One deferred refresh is enough after the compact geometry settles. There is
    # deliberately no scrollbar -> Quick geometry/mask synchronization path.
    background = getattr(visual, "background", None)
    if background is None:
        background = getattr(getattr(window, "_visual_style", None), "background", None)
    schedule = getattr(background, "schedule_mask_update", None)
    if callable(schedule):
        QTimer.singleShot(0, schedule)

    setattr(window, "_single_fixed_body", body)
    return body
