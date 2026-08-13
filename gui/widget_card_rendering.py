"""Single-surface QWidget card rendering trial for the formal GUI.

The native Quick scene remains the wallpaper/parallax owner, while every moving
card shell is painted by the same QWidget that owns its text and controls. This
removes scroll-time QWidget -> Python geometry scan -> Quick card-position
synchronization from the visible card path.
"""

from __future__ import annotations

from types import MethodType

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QMainWindow

from .native_visual_style import NativeGlassProxy


_GLASS_NAMES = {"glassCard", "heroCard", "statusCard", "microCard"}
_LOCAL_CARD_STYLE = """
QFrame#glassCard,
QFrame#heroCard,
QFrame#statusCard,
QFrame#microCard {
    background-color: rgba(0, 0, 0, 64);
    border: 0;
    border-radius: 6px;
}
"""


def _style_local_card(frame: QFrame) -> None:
    """Give one card a local shell so background and children share coordinates."""

    frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    # A widget-local stylesheet wins over the parent NEKRO rule that intentionally
    # made these frames transparent while Quick owned the glass shell.
    frame.setStyleSheet(_LOCAL_CARD_STYLE)


def _clear_quick_card_model(background) -> None:  # noqa: ANN001
    """Remove card delegates/masks from Quick without touching its wallpaper."""

    model = getattr(background, "card_model", None)
    if model is None:
        return

    model.beginResetModel()
    try:
        model.cards.clear()
        model._rows.clear()  # noqa: SLF001
        model._states.clear()  # noqa: SLF001
    finally:
        model.endResetModel()


def install_widget_card_rendering(window: QMainWindow, visual) -> None:  # noqa: ANN001
    """Move visible card shells from Quick to their owning QWidget frames.

    Existing NativeGlassProxy objects are preserved because their QGraphicsEffect
    still provides the proven whole-card hover/press scaling. Once the Quick card
    model is empty, proxy presentation calls become cheap no-ops on the background
    side while the QWidget subtree (now including its own shell) scales together.
    """

    if bool(getattr(visual, "_widget_card_rendering", False)):
        return

    background = getattr(visual, "background", None)
    if background is None:
        raise RuntimeError("widget card rendering requires the native Quick background")

    _clear_quick_card_model(background)

    for frame in tuple(getattr(visual, "_glass", {})):
        if isinstance(frame, QFrame) and frame.objectName() in _GLASS_NAMES:
            _style_local_card(frame)

    def refresh_widget_cards(controller) -> int:  # noqa: ANN001
        new_frames = [
            frame
            for frame in controller.window.findChildren(QFrame)
            if frame.objectName() in _GLASS_NAMES and frame not in controller._glass  # noqa: SLF001
        ]
        for frame in new_frames:
            _style_local_card(frame)
            controller._glass[frame] = NativeGlassProxy(frame, controller.background)  # noqa: SLF001
        return len(new_frames)

    # Cards created later (notably Batch/mode-workspace cards) must also stay out
    # of the Quick geometry/mask model. Keep only the existing whole-card scale
    # proxy so hover interaction remains behaviorally compatible.
    visual.refresh_glass_frames = MethodType(refresh_widget_cards, visual)

    # page_scroll_layout is installed after this hook. It asks the background for
    # schedule_mask_update and connects it to the outer scrollbar. In this mode
    # there is no moving Quick card mask to update, so expose a deliberate no-op
    # before that connection is created. Existing constructor-time connections on
    # nested item views are harmless: the model is empty and cannot redraw cards.
    def ignore_card_geometry_updates(_background, *_args: object) -> None:  # noqa: ANN001
        return None

    background.schedule_mask_update = MethodType(ignore_card_geometry_updates, background)
    visual._widget_card_rendering = True  # type: ignore[attr-defined]
