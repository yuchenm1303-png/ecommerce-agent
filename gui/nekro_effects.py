from __future__ import annotations

from .visual_assets import load_sakura_sprite


def _load_sprite():
    """Compatibility name for the exact existing sakura sprite asset."""
    return load_sakura_sprite()
