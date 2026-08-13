from __future__ import annotations

from typing import Any

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QBoxLayout, QFrame, QVBoxLayout, QWidget


_TOP_CARD_MIN = 238
_TOP_CARD_MAX = 248
_CONTROL_HEIGHT = 30
_SINGLE_PAGE_SPACING = 6


def _contains_widget(layout: Any, target: QWidget) -> bool:
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


def _row_for(parent: QVBoxLayout, target: QWidget | None) -> QBoxLayout | None:
    if target is None:
        return None
    for index in range(parent.count()):
        child = parent.itemAt(index).layout()
        if isinstance(child, QBoxLayout) and _contains_widget(child, target):
            return child
    return None


def _ancestor_card(widget: QWidget | None) -> QFrame | None:
    current = widget
    while current is not None:
        if isinstance(current, QFrame) and current.objectName() == "heroCard":
            return current
        current = current.parentWidget()
    return None


def _set_compact_height(widget: object) -> None:
    if isinstance(widget, QWidget):
        widget.setMinimumHeight(_CONTROL_HEIGHT)
        widget.setMaximumHeight(_CONTROL_HEIGHT)


def _apply(window: Any) -> None:
    url_input = getattr(window, "url_input", None)
    card = _ancestor_card(url_input if isinstance(url_input, QWidget) else None)
    layout = card.layout() if isinstance(card, QFrame) else None
    if not isinstance(card, QFrame) or not isinstance(layout, QVBoxLayout):
        return

    # Win the final startup geometry pass after listing_offer_support and the
    # historical page_scroll_layout zero-time refresh have both installed.
    layout.setContentsMargins(14, 8, 38, 9)
    layout.setSpacing(4)
    card.setMinimumHeight(_TOP_CARD_MIN)
    card.setMaximumHeight(_TOP_CARD_MAX)

    header = layout.itemAt(0).layout() if layout.count() else None
    if isinstance(header, QBoxLayout):
        header.setSpacing(8)
        header.setContentsMargins(0, 0, 0, 1)

    url_row = _row_for(layout, url_input if isinstance(url_input, QWidget) else None)
    offer_input = getattr(window, "listing_intent_input", None)
    offer_row = _row_for(layout, offer_input if isinstance(offer_input, QWidget) else None)
    stage_button = getattr(window, "step1_button", None)
    stage_row = _row_for(layout, stage_button if isinstance(stage_button, QWidget) else None)
    source_port = getattr(window, "source_port", None)
    source_row = _row_for(layout, source_port if isinstance(source_port, QWidget) else None)
    settings_toggle = getattr(window, "real_settings_toggle", None)
    settings_row = _row_for(layout, settings_toggle if isinstance(settings_toggle, QWidget) else None)

    for row, spacing in (
        (url_row, 10),
        (offer_row, 9),
        (stage_row, 10),
        (source_row, 10),
        (settings_row, 10),
    ):
        if isinstance(row, QBoxLayout):
            row.setSpacing(spacing)
            row.setContentsMargins(0, 0, 0, 0)

    for name in (
        "url_input",
        "start_button",
        "stop_button",
        "listing_intent_input",
        "step1_button",
        "step2_button",
        "step3_button",
        "source_port",
        "vertical_input",
        "real_settings_toggle",
    ):
        _set_compact_height(getattr(window, name, None))

    stack = getattr(window, "mode_stack", None)
    if stack is not None and stack.count() > 0:
        single_page = stack.widget(0)
        single_layout = single_page.layout() if isinstance(single_page, QWidget) else None
        if isinstance(single_layout, QVBoxLayout):
            single_layout.setSpacing(_SINGLE_PAGE_SPACING)

    visual = getattr(window, "_visual_style", None)
    refresh = getattr(visual, "refresh_glass_frames", None)
    if callable(refresh):
        refresh()


def install_single_top_compact(window: Any) -> None:
    """One-shot final Single geometry pass; presentation only, no business wiring."""

    if getattr(window, "_single_top_compact_installed", False):
        return
    setattr(window, "_single_top_compact_installed", True)
    QTimer.singleShot(0, lambda: _apply(window))
