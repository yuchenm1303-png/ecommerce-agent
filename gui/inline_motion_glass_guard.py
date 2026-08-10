from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMainWindow


def _background(window: QMainWindow):
    visual = getattr(window, "_visual_style", None)
    return getattr(visual, "background", None)


def install_inline_motion_glass_guard(window: QMainWindow) -> None:
    """Defer only the expensive blur-mask texture while inline layout moves.

    The native background keeps its lightweight card geometry model live, but
    the full-window QImage -> PNG -> QML texture path is frozen for the short
    expansion interval.  The final mask is rebuilt exactly once after motion.
    """

    background = _background(window)
    if background is None or getattr(background, "_inline_motion_glass_guard", False):
        return

    original: Callable[[], Any] | None = getattr(background, "_update_mask_texture", None)
    if not callable(original):
        return

    def guarded_update_mask_texture() -> Any:
        if bool(getattr(window, "_inline_card_motion_active", False)):
            setattr(background, "_mask_ready", False)
            setattr(background, "_inline_motion_mask_stale", True)
            return None
        return original()

    background._update_mask_texture = guarded_update_mask_texture  # type: ignore[attr-defined]
    background._inline_motion_glass_guard = True  # type: ignore[attr-defined]
    background._inline_motion_mask_stale = False  # type: ignore[attr-defined]
    window._inline_motion_depth = 0  # type: ignore[attr-defined]


def begin_inline_motion(window: QMainWindow) -> None:
    depth = int(getattr(window, "_inline_motion_depth", 0)) + 1
    window._inline_motion_depth = depth  # type: ignore[attr-defined]
    if depth != 1:
        return

    window._inline_card_motion_active = True  # type: ignore[attr-defined]

    background = _background(window)
    if background is not None:
        pointer_timer = getattr(background, "_pointer_timer", None)
        pointer_was_active = isinstance(pointer_timer, QTimer) and pointer_timer.isActive()
        window._inline_motion_pointer_was_active = pointer_was_active  # type: ignore[attr-defined]
        if pointer_was_active:
            pointer_timer.stop()
        quick = getattr(background, "quick_window", None)
        if quick is not None:
            quick.setProperty("animationRunning", False)

    effects = getattr(window, "_nekro_effects", None)
    effects_timer = getattr(effects, "timer", None)
    effects_was_active = isinstance(effects_timer, QTimer) and effects_timer.isActive()
    window._inline_motion_effects_was_active = effects_was_active  # type: ignore[attr-defined]
    if effects_was_active:
        effects_timer.stop()


def end_inline_motion(window: QMainWindow) -> None:
    depth = max(0, int(getattr(window, "_inline_motion_depth", 0)) - 1)
    window._inline_motion_depth = depth  # type: ignore[attr-defined]
    if depth != 0:
        return

    window._inline_card_motion_active = False  # type: ignore[attr-defined]

    background = _background(window)
    if background is not None:
        if bool(getattr(window, "_inline_motion_pointer_was_active", False)):
            pointer_timer = getattr(background, "_pointer_timer", None)
            if isinstance(pointer_timer, QTimer) and not pointer_timer.isActive():
                pointer_timer.start()
        setattr(background, "_mask_ready", False)
        setattr(background, "_inline_motion_mask_stale", False)
        schedule = getattr(background, "schedule_mask_update", None)
        if callable(schedule):
            schedule()

    effects = getattr(window, "_nekro_effects", None)
    effects_timer = getattr(effects, "timer", None)
    if bool(getattr(window, "_inline_motion_effects_was_active", False)):
        if isinstance(effects_timer, QTimer) and not effects_timer.isActive():
            effects_timer.start()
