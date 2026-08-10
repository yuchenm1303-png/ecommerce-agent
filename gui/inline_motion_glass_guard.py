from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMainWindow


def _background(window: QMainWindow):
    visual = getattr(window, "_visual_style", None)
    return getattr(visual, "background", None)


def _pause_timer(owner: object | None, name: str, window: QMainWindow, key: str) -> None:
    timer = getattr(owner, name, None)
    was_active = isinstance(timer, QTimer) and timer.isActive()
    setattr(window, key, was_active)
    if was_active:
        timer.stop()


def _resume_timer(owner: object | None, name: str, window: QMainWindow, key: str) -> None:
    if not bool(getattr(window, key, False)):
        return
    timer = getattr(owner, name, None)
    if isinstance(timer, QTimer) and not timer.isActive():
        timer.start()


def install_inline_motion_glass_guard(window: QMainWindow) -> None:
    """Freeze expensive glass texture work during short inline layout motion."""

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
        _pause_timer(background, "_pointer_timer", window, "_inline_motion_pointer_was_active")
        quick = getattr(background, "quick_window", None)
        if quick is not None:
            quick.setProperty("animationRunning", False)

    effects = getattr(window, "_nekro_effects", None)
    _pause_timer(effects, "timer", window, "_inline_motion_effects_was_active")

    card_fx = getattr(window, "_nekro_card_fx", None)
    _pause_timer(card_fx, "_sample_timer", window, "_inline_motion_card_sample_was_active")
    _pause_timer(card_fx, "_animation_timer", window, "_inline_motion_card_anim_was_active")

    smooth = getattr(window, "_smooth_wheel_filter", None)
    scroller = getattr(smooth, "_scroller", None)
    _pause_timer(scroller, "_timer", window, "_inline_motion_scroll_was_active")


def end_inline_motion(window: QMainWindow) -> None:
    depth = max(0, int(getattr(window, "_inline_motion_depth", 0)) - 1)
    window._inline_motion_depth = depth  # type: ignore[attr-defined]
    if depth != 0:
        return

    window._inline_card_motion_active = False  # type: ignore[attr-defined]

    background = _background(window)
    if background is not None:
        # Force the next pointer sample to re-arm QML FrameAnimation even when
        # the cursor did not move while layout motion was active.
        setattr(background, "_last_pointer_norm", None)
        _resume_timer(background, "_pointer_timer", window, "_inline_motion_pointer_was_active")
        setattr(background, "_mask_ready", False)
        setattr(background, "_inline_motion_mask_stale", False)
        schedule = getattr(background, "schedule_mask_update", None)
        if callable(schedule):
            schedule()

    effects = getattr(window, "_nekro_effects", None)
    _resume_timer(effects, "timer", window, "_inline_motion_effects_was_active")

    card_fx = getattr(window, "_nekro_card_fx", None)
    _resume_timer(card_fx, "_sample_timer", window, "_inline_motion_card_sample_was_active")
    _resume_timer(card_fx, "_animation_timer", window, "_inline_motion_card_anim_was_active")

    # A scroll animation interrupted by card expansion should not continue from
    # a stale target after the layout has moved.  It remains stopped by design.
