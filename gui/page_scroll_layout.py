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


_INPUT_CARD_MIN_HEIGHT = 222
_INPUT_CARD_MAX_HEIGHT = 238
_STATUS_CARD_MIN_HEIGHT = 54
_STATUS_CARD_MAX_HEIGHT = 58
_WORKSPACE_MIN_HEIGHT = 220
_FIELD_TABLE_MIN_HEIGHT = 136
_FIELD_CARD_BOTTOM_INSET = 28
_SIDE_MIN_WIDTH = 360
_SIDE_MAX_WIDTH = 480
_SIDE_TARGET_RATIO = 0.29
_CONSOLE_MIN_HEIGHT = 292
_CONSOLE_MAX_HEIGHT = 336
_CONSOLE_TARGET_HEIGHT = 310


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
    """Keep URL, stage actions and source controls as visually separate rows.

    The controls are already separate business widgets. The fixed Single layout
    only gives them enough vertical budget and explicit row spacing so Qt never
    compresses the three interaction bands into one dense block.
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
    stage_row.addStretch(1)
    stage_row.setSpacing(10)
    stage_row.setContentsMargins(0, 1, 0, 1)

    settings_row.setSpacing(10)
    settings_row.setContentsMargins(0, 2, 0, 1)
    setattr(window, "_single_fixed_input_rows", True)


def _compact_input_card(window: QMainWindow, input_card: QFrame) -> None:
    _compact_input_rows(window, input_card)
    layout = input_card.layout()
    if isinstance(layout, QVBoxLayout):
        layout.setContentsMargins(14, 9, 14, 10)
        layout.setSpacing(7)
    input_card.setMinimumHeight(_INPUT_CARD_MIN_HEIGHT)
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
            layout.setContentsMargins(12, 2, 12, 2)
            layout.setSpacing(0)


def _strip_trailing_spacers(layout: QBoxLayout) -> None:
    for index in range(layout.count() - 1, -1, -1):
        item = layout.itemAt(index)
        if item.spacerItem() is not None:
            layout.takeAt(index)


def _align_field_card_to_side_panel(
    window: QMainWindow,
    workspace_splitter: QSplitter,
) -> None:
    """Trim only the left glass card so its bottom matches the right panel.

    ui_polish intentionally keeps 28 px of bottom breathing room under the
    Runtime/Reference/Safety tab group. The field card used to fill the complete
    splitter cross-axis, which made its glass shell visibly longer. A transparent
    host mirrors that same bottom inset without rebuilding the field table.
    """

    if isinstance(getattr(window, "_single_field_alignment_host", None), QWidget):
        return

    field_table = getattr(window, "field_table", None)
    field_card = field_table.parentWidget() if isinstance(field_table, QWidget) else None
    if not isinstance(field_card, QFrame):
        return

    index = workspace_splitter.indexOf(field_card)
    if index < 0:
        return

    host = QWidget()
    host.setObjectName("fieldReviewHost")
    host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    host_layout = QVBoxLayout(host)
    host_layout.setContentsMargins(0, 0, 0, _FIELD_CARD_BOTTOM_INSET)
    host_layout.setSpacing(0)

    field_card.setParent(host)
    host_layout.addWidget(field_card, 1)
    workspace_splitter.insertWidget(index, host)
    field_card.show()

    setattr(window, "_single_field_alignment_host", host)


def _compact_workspace_cards(window: QMainWindow) -> None:
    """Reduce middle-card chrome so the workflow console gets useful height."""

    field_table = getattr(window, "field_table", None)
    field_card = field_table.parentWidget() if isinstance(field_table, QWidget) else None
    if isinstance(field_card, QFrame):
        layout = field_card.layout()
        if isinstance(layout, QBoxLayout):
            layout.setContentsMargins(14, 8, 14, 10)
            layout.setSpacing(5)

    side_tabs = getattr(window, "side_detail_tabs", None)
    if isinstance(side_tabs, QTabWidget):
        for index in range(side_tabs.count()):
            card = side_tabs.widget(index)
            if not isinstance(card, QFrame):
                continue
            layout = card.layout()
            if isinstance(layout, QBoxLayout):
                layout.setContentsMargins(14, 9, 14, 10)
                layout.setSpacing(4)


def _configure_workspace(window: QMainWindow, body: QSplitter) -> None:
    workspace = body.widget(0) if body.count() > 0 else None
    console = body.widget(1) if body.count() > 1 else None
    if not isinstance(workspace, QWidget) or not isinstance(console, QWidget):
        raise RuntimeError("fixed Single layout requires workspace + console in bodySplitter")

    workspace.setMinimumHeight(_WORKSPACE_MIN_HEIGHT)
    workspace.setMaximumHeight(16777215)
    workspace.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    _compact_workspace_cards(window)

    field_table = getattr(window, "field_table", None)
    if isinstance(field_table, QWidget):
        field_table.setMinimumHeight(_FIELD_TABLE_MIN_HEIGHT)

    workspace_splitter = workspace.findChild(QSplitter, "workspaceSplitter")
    if isinstance(workspace_splitter, QSplitter) and workspace_splitter.count() > 1:
        _align_field_card_to_side_panel(window, workspace_splitter)

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
    target = min(
        _CONSOLE_MAX_HEIGHT,
        max(
            _CONSOLE_MIN_HEIGHT,
            min(_CONSOLE_TARGET_HEIGHT, max(0, available - _WORKSPACE_MIN_HEIGHT)),
        ),
    )
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

    # Keep original widget ownership: the cards stay stationary and only their
    # one-time/resize geometry changes reach the native Quick glass compositor.
    outer.setSpacing(6)
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
