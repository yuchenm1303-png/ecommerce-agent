from __future__ import annotations

from typing import Any

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QBoxLayout, QFrame, QLabel, QVBoxLayout, QWidget

from .numeric_field_chrome import install_numeric_field_chrome


_TOP_CARD_MIN = 272
_TOP_CARD_MAX = 282
_CONTROL_HEIGHT = 30
_SINGLE_PAGE_SPACING = 6
_INTENT_DETAIL_EXTRA = 112
_LEFT_LABEL_WIDTH = 112
_MIDDLE_LABEL_WIDTH = 132
_STAGE_BUTTON_WIDTH = 176
_DETAIL_BUTTON_WIDTH = 70


def _contains_widget(layout: Any, target: QWidget) -> bool:
    if layout is None:
        return False
    for index in range(layout.count()):
        item = layout.itemAt(index)
        widget = item.widget()
        if widget is target:
            return True
        child = item.layout()
        if child is not None and _contains_widget(child, target):
            return True
        if isinstance(widget, QWidget) and _contains_widget(widget.layout(), target):
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


def _label(row: QBoxLayout | None, text: str) -> QLabel | None:
    if not isinstance(row, QBoxLayout):
        return None
    for index in range(row.count()):
        widget = row.itemAt(index).widget()
        if isinstance(widget, QLabel) and widget.text() == text:
            return widget
    return None


def _set_label_column(label: QLabel | None, width: int) -> None:
    if not isinstance(label, QLabel):
        return
    label.setMinimumWidth(int(width))
    label.setMaximumWidth(int(width))
    label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)


def _align_row_controls(row: QBoxLayout | None) -> None:
    if not isinstance(row, QBoxLayout):
        return
    for index in range(row.count()):
        widget = row.itemAt(index).widget()
        if isinstance(widget, QWidget):
            row.setAlignment(widget, Qt.AlignmentFlag.AlignVCenter)


def set_single_top_detail_expanded(window: Any, expanded: bool) -> None:
    """Resize the Single source card only while the explicit detail editor is open."""

    url_input = getattr(window, "url_input", None)
    card = _ancestor_card(url_input if isinstance(url_input, QWidget) else None)
    if not isinstance(card, QFrame):
        return
    extra = _INTENT_DETAIL_EXTRA if bool(expanded) else 0
    card.setMinimumHeight(_TOP_CARD_MIN + extra)
    card.setMaximumHeight(_TOP_CARD_MAX + extra)
    card.updateGeometry()

    visual = getattr(window, "_visual_style", None)
    refresh = getattr(visual, "refresh_glass_frames", None)
    if callable(refresh):
        refresh()


def _apply(window: Any) -> None:
    url_input = getattr(window, "url_input", None)
    card = _ancestor_card(url_input if isinstance(url_input, QWidget) else None)
    layout = card.layout() if isinstance(card, QFrame) else None
    if not isinstance(card, QFrame) or not isinstance(layout, QVBoxLayout):
        return

    # This is the final geometry owner for the Single source card. Keep one
    # symmetric content grid so labels, fields and buttons share the same visual
    # baselines before StaticQmlBridge snapshots them.
    layout.setContentsMargins(16, 8, 16, 9)
    layout.setSpacing(4)

    header = layout.itemAt(0).layout() if layout.count() else None
    if isinstance(header, QBoxLayout):
        header.setSpacing(8)
        header.setContentsMargins(0, 0, 0, 1)

    url_row = _row_for(layout, url_input if isinstance(url_input, QWidget) else None)
    offer_input = getattr(window, "listing_intent_input", None)
    offer_row = _row_for(layout, offer_input if isinstance(offer_input, QWidget) else None)
    ai_guidance_input = getattr(window, "ai_guidance_input", None)
    guidance_row = _row_for(layout, ai_guidance_input if isinstance(ai_guidance_input, QWidget) else None)
    stage_button = getattr(window, "step1_button", None)
    stage_row = _row_for(layout, stage_button if isinstance(stage_button, QWidget) else None)
    source_port = getattr(window, "source_port", None)
    source_row = _row_for(layout, source_port if isinstance(source_port, QWidget) else None)
    settings_toggle = getattr(window, "real_settings_toggle", None)
    settings_row = _row_for(layout, settings_toggle if isinstance(settings_toggle, QWidget) else None)

    for row, spacing in (
        (url_row, 10),
        (offer_row, 8),
        (guidance_row, 8),
        (stage_row, 10),
        (source_row, 8),
        (settings_row, 10),
    ):
        if isinstance(row, QBoxLayout):
            row.setSpacing(spacing)
            row.setContentsMargins(0, 0, 0, 0)
            _align_row_controls(row)

    # Product-offer and AI-guidance rows now share an actual label column rather
    # than unrelated minimum widths. The mid-row Model Name label has its own
    # stable column so both guidance editors sit on predictable baselines.
    _set_label_column(_label(offer_row, "销售规格 / 套装"), _LEFT_LABEL_WIDTH)
    _set_label_column(_label(guidance_row, "AI 引导"), _LEFT_LABEL_WIDTH)
    _set_label_column(_label(guidance_row, "Model Name 流量词"), _MIDDLE_LABEL_WIDTH)

    detail_button = getattr(window, "listing_intent_detail_button", None)
    if isinstance(detail_button, QWidget):
        detail_button.setMinimumWidth(_DETAIL_BUTTON_WIDTH)
        detail_button.setMaximumWidth(_DETAIL_BUTTON_WIDTH)

    for name in ("step1_button", "step2_button", "step3_button"):
        button = getattr(window, name, None)
        if isinstance(button, QWidget):
            button.setMinimumWidth(_STAGE_BUTTON_WIDTH)
            button.setMaximumWidth(_STAGE_BUTTON_WIDTH)

    vertical_input = getattr(window, "vertical_input", None)
    if isinstance(vertical_input, QWidget):
        vertical_input.setMinimumWidth(260)
        vertical_input.setMaximumWidth(340)

    # Restore the prompt and +/- controls that native QSpinBox chrome loses in
    # the Quick mirror. The underlying source_port QSpinBox remains the business
    # value owner; the renderer-neutral chrome is only presentation.
    install_numeric_field_chrome(
        source_port,
        prompt_width=_LEFT_LABEL_WIDTH,
        value_width=78,
    )

    for name in (
        "url_input",
        "start_button",
        "stop_button",
        "listing_intent_input",
        "listing_intent_detail_button",
        "ai_guidance_input",
        "model_name_keywords_input",
        "step1_button",
        "step2_button",
        "step3_button",
        "source_port",
        "vertical_input",
        "real_settings_toggle",
    ):
        _set_compact_height(getattr(window, name, None))

    detail_host = getattr(window, "listing_intent_detail_host", None)
    set_single_top_detail_expanded(
        window,
        bool(isinstance(detail_host, QWidget) and detail_host.isVisible()),
    )

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
    """Final Single top presentation, including the on-demand intent detail editor."""

    if getattr(window, "_single_top_compact_installed", False):
        return
    setattr(window, "_single_top_compact_installed", True)

    # ListingOfferSupport already owns the canonical one-line business value.
    # Add only an alternate editing surface here, then let this geometry owner
    # resize the hero card while that surface is explicitly expanded.
    from .listing_intent_detail import install_listing_intent_detail

    install_listing_intent_detail(
        window,
        on_expanded=lambda expanded: set_single_top_detail_expanded(window, expanded),
    )
    QTimer.singleShot(0, lambda: _apply(window))


__all__ = ["install_single_top_compact", "set_single_top_detail_expanded"]
