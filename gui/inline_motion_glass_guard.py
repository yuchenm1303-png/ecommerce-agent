from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtWidgets import QMainWindow


def install_inline_motion_glass_guard(window: QMainWindow) -> None:
    """Keep card geometry/tint live while deferring the expensive blur-mask PNG.

    NativeQuickBackground already coalesces QWidget geometry changes and keeps
    its lightweight GlassCardModel in sync. The expensive part is the final
    blur mask path: full-window QImage -> PNG -> QML texture. Rebuilding that
    texture repeatedly while a large QWidget layout is expanding is unnecessary
    and is the dominant source of visible stutter.

    During inline-card motion we therefore let geometry synchronization continue
    but suppress only ``_update_mask_texture``. ``_mask_ready`` is marked false
    while suppressed so the first coalesced pass after motion ends is guaranteed
    to rebuild one exact final mask.
    """

    visual = getattr(window, "_visual_style", None)
    background = getattr(visual, "background", None)
    if background is None or getattr(background, "_inline_motion_glass_guard", False):
        return

    original: Callable[[], Any] | None = getattr(background, "_update_mask_texture", None)
    if not callable(original):
        return

    def guarded_update_mask_texture() -> Any:
        if bool(getattr(window, "_inline_card_motion_active", False)):
            # Ensure the first normal geometry flush after the motion cannot
            # incorrectly conclude that the old static mask is still valid.
            setattr(background, "_mask_ready", False)
            return None
        return original()

    background._update_mask_texture = guarded_update_mask_texture  # type: ignore[attr-defined]
    background._inline_motion_glass_guard = True  # type: ignore[attr-defined]
