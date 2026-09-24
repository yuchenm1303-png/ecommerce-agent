from __future__ import annotations

from typing import Any

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QBoxLayout,
    QFrame,
    QLabel,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


_TOP_CARD_MIN = 272
_TOP_CARD_MAX = 282
_CONTROL_HEIGHT = 30
_SINGLE_PAGE_SPACING = 6
_INTENT_DETAIL_EXTRA = 112
_LEFT_LABEL_WIDTH = 112
_MIDDLE_LABEL_WIDTH = 132
_FORM_ROW_SPACING = 8
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
    if not isinstance(widget, QWidget):
        return
    widget.setFixedHeight(_CONTROL_HEIGHT)
    policy = widget.sizePolicy()
    if policy.verticalPolicy() != QSizePolicy.Policy.Fixed:
        widget.setSizePolicy(policy.horizontalPolicy(), QSizePolicy.Policy.Fixed)


def _widget_index(row: QBoxLayout | None, target: QWidget | None) -> int:
    if not isinstance(row, QBoxLayout) or not isinstance(target, QWidget):
        return -1
    for index in range(row.count()):
        if row.itemAt(index).widget() is target:
            return index
    return -1


def _label_before(row: QBoxLayout | None, target: QWidget | None) -> QLabel | None:
    target_index = _widget_index(row, target)
    if target_index <= 0 or not isinstance(row, QBoxLayout):
        return None
    for index in range(target_index - 1, -1, -1):
        widget = row.itemAt(index).widget()
        if isinstance(widget, QLabel):
            return widget
    return None


def _set_label_column(label: QLabel | None, width: int) -> None:
    if not isinstance(label, QLabel):
        return
    label.setContentsMargins(0, 0, 0, 0)
    label.setFixedSize(int(width), _CONTROL_HEIGHT)
    label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)


def _normalize_form_row(row: QBoxLayout | None) -> None:
    if not isinstance(row, QBoxLayout):
        return
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(_FORM_ROW_SPACING)
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

    # This is the final geometry owner for the Single source card. Every form row
    # uses the same left edge, label column, control height and vertical baseline
    # before StaticQmlBridge snapshots it for the visible Quick scene.
    layout.setContentsMargins(16, 8, 16, 9)
    layout.setSpacing(4)

    header = layout.itemAt(0).layout() if layout.count() else None
    if isinstance(header, QBoxLayout):
        header.setSpacing(8)
        header.setContentsMargins(0, 0, 0, 1)

    offer_input = getattr(window, "listing_intent_input", None)
    ai_guidance_input = getattr(window, "ai_guidance_input", None)
    model_keywords_input = getattr(window, "model_name_keywords_input", None)
    stage_button = getattr(window, "step1_button", None)
    source_port = getattr(window, "source_port", None)
    settings_toggle = getattr(window, "real_settings_toggle", None)

    url_row = _row_for(layout, url_input if isinstance(url_input, QWidget) else None)
    offer_row = _row_for(layout, offer_input if isinstance(offer_input, QWidget) else None)
    guidance_row = _row_for(layout, ai_guidance_input if isinstance(ai_guidance_input, QWidget) else None)
    stage_row = _row_for(layout, stage_button if isinstance(stage_button, QWidget) else None)
    source_row = _row_for(layout, source_port if isinstance(source_port, QWidget) else None)
    settings_row = _row_for(layout, settings_toggle if isinstance(settings_toggle, QWidget) else None)

    for row in (offer_row, guidance_row, source_row):
        _normalize_form_row(row)
    for row, spacing in (
        (url_row, 10),
        (stage_row, 10),
        (settings_row, 10),
    ):
        if isinstance(row, QBoxLayout):
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(spacing)
            for index in range(row.count()):
                widget = row.itemAt(index).widget()
                if isinstance(widget, QWidget):
                    row.setAlignment(widget, Qt.AlignmentFlag.AlignVCenter)

    # Resolve labels by their structural relationship to each editor, not by
    # display text. Copy changes therefore cannot silently break the geometry.
    _set_label_column(_label_before(offer_row, offer_input), _LEFT_LABEL_WIDTH)
    _set_label_column(_label_before(guidance_row, ai_guidance_input), _LEFT_LABEL_WIDTH)
    _set_label_column(_label_before(guidance_row, model_keywords_input), _MIDDLE_LABEL_WIDTH)

    for widget in (offer_input, ai_guidance_input, model_keywords_input):
        if isinstance(widget, QWidget):
            _set_compact_height(widget)
            widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

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

    # Keep the original QSpinBox intact. Quick owns only its presentation and
    # uses this semantic name to render Source CDP + value + stacked stepper as
    # one control; QWidget fallback retains the native prefix and arrows.
    if isinstance(source_port, QSpinBox):
        source_port.setObjectName("sourceCdpSpin")
        source_port.setMinimumWidth(176)
        source_port.setMaximumWidth(176)
        source_port.setFixedHeight(_CONTROL_HEIGHT)

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

    # Commit the final QWidget geometry synchronously. Quick must never snapshot
    # an intermediate size-hint state and then present slightly different column
    # origins for adjacent rows.
    layout.invalidate()
    layout.activate()
    card.updateGeometry()

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
            single_layout.invalidate()
            single_layout.activate()

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
