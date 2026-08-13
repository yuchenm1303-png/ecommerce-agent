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
    QLabel,
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
_INPUT_CARD_OFFER_MIN_HEIGHT = 278
_INPUT_CARD_OFFER_MAX_HEIGHT = 296
_STATUS_CARD_MIN_HEIGHT = 64
_STATUS_CARD_MAX_HEIGHT = 68
_WORKSPACE_MIN_HEIGHT = 220
_FIELD_TABLE_MIN_HEIGHT = 136
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


def _set_width_band(widget: object, minimum: int, maximum: int) -> None:
    if not isinstance(widget, QWidget):
        return
    widget.setMinimumWidth(minimum)
    widget.setMaximumWidth(maximum)


def _compact_input_rows(window: QMainWindow, input_card: QFrame) -> None:
    """Give every Product Source interaction band its own visual rhythm."""

    layout = input_card.layout()
    if not isinstance(layout, QVBoxLayout):
        return

    url_anchor = getattr(window, "url_input", None)
    stage_anchor = getattr(window, "step1_button", None)
    settings_anchor = getattr(window, "source_port", None)

    url_row = _direct_box_layout_containing(
        layout, url_anchor if isinstance(url_anchor, QWidget) else None
    )
    stage_row = _direct_box_layout_containing(
        layout, stage_anchor if isinstance(stage_anchor, QWidget) else None
    )
    settings_row = _direct_box_layout_containing(
        layout, settings_anchor if isinstance(settings_anchor, QWidget) else None
    )

    if url_row is not None:
        url_row.setSpacing(12)
        url_row.setContentsMargins(0, 1, 0, 2)
        _set_width_band(getattr(window, "start_button", None), 176, 216)
        _set_width_band(getattr(window, "stop_button", None), 62, 76)

    if stage_row is not None:
        _remove_spacers(stage_row)
        for name in ("step1_button", "step2_button", "step3_button"):
            _set_width_band(getattr(window, name, None), 148, 176)
        stage_row.addStretch(1)
        stage_row.setSpacing(12)
        stage_row.setContentsMargins(0, 2, 0, 2)

    if settings_row is not None:
        settings_row.setSpacing(14)
        settings_row.setContentsMargins(0, 3, 0, 2)
        _set_width_band(getattr(window, "source_port", None), 188, 212)
        _set_width_band(getattr(window, "vertical_input", None), 210, 250)

    setattr(window, "_single_fixed_input_rows", True)


def refresh_single_source_layout(window: QMainWindow) -> None:
    """Reflow Product Source after optional feature rows have been installed."""

    url_input = getattr(window, "url_input", None)
    input_card = _ancestor_card(
        url_input if isinstance(url_input, QWidget) else None,
        "heroCard",
    )
    if not isinstance(input_card, QFrame):
        return

    _compact_input_rows(window, input_card)
    layout = input_card.layout()
    if not isinstance(layout, QVBoxLayout):
        return

    offer_input = getattr(window, "listing_intent_input", None)
    has_offer_row = isinstance(offer_input, QWidget)
    if has_offer_row:
        offer_row = _direct_box_layout_containing(layout, offer_input)
        if offer_row is not None:
            offer_row.setSpacing(12)
            offer_row.setContentsMargins(0, 2, 0, 2)
            offer_input.setMinimumWidth(280)

    settings_toggle = getattr(window, "real_settings_toggle", None)
    if isinstance(settings_toggle, QWidget):
        summary_row = _direct_box_layout_containing(layout, settings_toggle)
        if summary_row is not None:
            summary_row.setSpacing(14)
            summary_row.setContentsMargins(0, 5, 0, 0)
        _set_width_band(settings_toggle, 104, 128)

    layout.setContentsMargins(16, 11, 38, 12)
    layout.setSpacing(9 if has_offer_row else 8)
    input_card.setMinimumHeight(
        _INPUT_CARD_OFFER_MIN_HEIGHT if has_offer_row else _INPUT_CARD_MIN_HEIGHT
    )
    input_card.setMaximumHeight(
        _INPUT_CARD_OFFER_MAX_HEIGHT if has_offer_row else _INPUT_CARD_MAX_HEIGHT
    )
    input_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)


def _compact_input_card(window: QMainWindow, input_card: QFrame) -> None:
    _compact_input_rows(window, input_card)
    layout = input_card.layout()
    if isinstance(layout, QVBoxLayout):
        layout.setContentsMargins(14, 9, 14, 10)
        layout.setSpacing(7)
    input_card.setMinimumHeight(_INPUT_CARD_MIN_HEIGHT)
    input_card.setMaximumHeight(_INPUT_CARD_MAX_HEIGHT)
    input_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)


def _apply_status_card_typography(card: QFrame) -> None:
    """Keep the three status lines legible inside the compact dashboard row."""

    layout = card.layout()
    if not isinstance(layout, QBoxLayout):
        return

    value = getattr(card, "value", None)
    if isinstance(value, QLabel):
        value.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        style = value.styleSheet()
        if "font-size: 26px" in style:
            value.setStyleSheet(style.replace("font-size: 26px", "font-size: 22px"))

    title = layout.itemAt(1).widget() if layout.count() > 1 else None
    if isinstance(title, QLabel):
        title.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        title.setStyleSheet(
            "font-size: 10px; font-weight: 720; color: rgba(255,255,255,218);"
        )

    caption = layout.itemAt(2).widget() if layout.count() > 2 else None
    if isinstance(caption, QLabel):
        caption.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        caption.setStyleSheet(
            "font-size: 9px; color: rgba(255,255,255,150);"
        )


def _compact_status_cards(window: QMainWindow) -> None:
    for name in ("ready_card", "missing_card", "conflict_card", "blocked_card"):
        card = getattr(window, name, None)
        if not isinstance(card, QFrame):
            continue
        card.setMinimumHeight(_STATUS_CARD_MIN_HEIGHT)
        card.setMaximumHeight(_STATUS_CARD_MAX_HEIGHT)
        layout = card.layout()
        if isinstance(layout, QBoxLayout):
            layout.setContentsMargins(13, 4, 13, 4)
            layout.setSpacing(1)
        _apply_status_card_typography(card)

    if not getattr(window, "_single_status_typography_refresh", False):
        runner = getattr(window, "runner", None)
        result_updated = getattr(runner, "result_updated", None)
        if result_updated is not None and hasattr(result_updated, "connect"):
            result_updated.connect(lambda *_: _compact_status_cards(window))
        setattr(window, "_single_status_typography_refresh", True)


def _strip_trailing_spacers(layout: QBoxLayout) -> None:
    for index in range(layout.count() - 1, -1, -1):
        item = layout.itemAt(index)
        if item.spacerItem() is not None:
            layout.takeAt(index)


def _unregister_nested_glass_pages(window: QMainWindow, pages: list[QFrame]) -> None:
    """Turn old nested glass cards into plain pages inside one outer glass card."""

    visual = getattr(window, "_visual_style", None)
    glass = getattr(visual, "_glass", None)
    background = getattr(visual, "background", None)
    model = getattr(background, "card_model", None)
    targets = set(pages)

    if isinstance(glass, dict):
        for frame in pages:
            surface = glass.pop(frame, None)
            if surface is not None:
                try:
                    surface.cleanup()
                except RuntimeError:
                    pass

    cards = list(getattr(model, "cards", [])) if model is not None else []
    states = list(getattr(model, "_states", [])) if model is not None else []
    if model is not None and len(cards) == len(states) and any(card in targets for card in cards):
        kept = [(card, state) for card, state in zip(cards, states) if card not in targets]
        model.beginResetModel()
        try:
            model.cards[:] = [card for card, _ in kept]
            model._states[:] = [state for _, state in kept]
            model._rows = {card: row for row, card in enumerate(model.cards)}
        finally:
            model.endResetModel()

    geometry_watch = getattr(background, "_geometry_watch", None)
    if isinstance(geometry_watch, set):
        for frame in pages:
            if frame in geometry_watch:
                try:
                    frame.removeEventFilter(background)
                except RuntimeError:
                    pass
                geometry_watch.discard(frame)

    for frame in pages:
        frame.setObjectName("sideDetailPage")
        frame.setGraphicsEffect(None)
        frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        frame.setStyleSheet(
            frame.styleSheet()
            + "\nQFrame#sideDetailPage { background: transparent; border: 0; }"
        )


def _unify_workspace_card_structure(
    window: QMainWindow,
    workspace_splitter: QSplitter,
) -> None:
    """Make Field Review and diagnostics siblings with the same outer QFrame shell."""

    existing = getattr(window, "_single_side_panel_card", None)
    if isinstance(existing, QFrame):
        return

    side_tabs = getattr(window, "side_detail_tabs", None)
    if not isinstance(side_tabs, QTabWidget) or workspace_splitter.count() < 2:
        return

    pages = [
        page
        for index in range(side_tabs.count())
        if isinstance((page := side_tabs.widget(index)), QFrame)
    ]
    if not pages:
        return

    old_side = workspace_splitter.widget(1)
    old_host = side_tabs.parentWidget()
    old_layout = old_host.layout() if old_host is not None else None
    if isinstance(old_layout, QBoxLayout):
        old_layout.removeWidget(side_tabs)

    _unregister_nested_glass_pages(window, pages)

    side_card = QFrame(workspace_splitter)
    side_card.setObjectName("glassCard")
    side_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    side_layout = QVBoxLayout(side_card)
    side_layout.setContentsMargins(0, 0, 0, 0)
    side_layout.setSpacing(0)

    side_tabs.setParent(side_card)
    side_tabs.setMinimumHeight(0)
    side_tabs.setMaximumHeight(16777215)
    side_tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    side_layout.addWidget(side_tabs, 1)

    index = workspace_splitter.indexOf(old_side)
    old_side.setParent(None)
    workspace_splitter.insertWidget(max(0, index), side_card)
    old_side.hide()
    old_side.deleteLater()
    side_card.show()

    setattr(window, "_single_side_panel_card", side_card)

    visual = getattr(window, "_visual_style", None)
    refresh = getattr(visual, "refresh_glass_frames", None)
    if callable(refresh):
        refresh()


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
        _unify_workspace_card_structure(window, workspace_splitter)

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
    """Keep Single as a fixed one-screen workspace."""

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

    outer.setSpacing(6)
    _compact_input_card(window, input_card)
    _compact_status_cards(window)
    _configure_workspace(window, body)

    QTimer.singleShot(0, lambda: refresh_single_source_layout(window))

    background = getattr(visual, "background", None)
    if background is None:
        background = getattr(getattr(window, "_visual_style", None), "background", None)
    schedule = getattr(background, "schedule_mask_update", None)
    if callable(schedule):
        QTimer.singleShot(0, schedule)

    setattr(window, "_single_fixed_body", body)
    return body
