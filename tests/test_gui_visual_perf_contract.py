from __future__ import annotations

from pathlib import Path


VISUAL_STYLE = Path(__file__).resolve().parents[1] / "gui" / "visual_style.py"


def test_wallpaper_motion_does_not_rebuild_or_scroll_pixels_per_frame() -> None:
    source = VISUAL_STYLE.read_text(encoding="utf-8")
    assert "self.scroll(" not in source
    assert "_surface_cache" not in source
    assert "self._motion_timer.setTimerType(Qt.PreciseTimer)" in source
    assert "self._blurred = _blur_pixmap(self._render, 10.0)" in source


def test_glass_uses_shared_live_background_transform() -> None:
    source = VISUAL_STYLE.read_text(encoding="utf-8")
    assert "class GlassLayer(QWidget):" in source
    assert "background.transform_changed.connect(self.update_transform)" in source
    assert "src = self.background.source_rect()" in source
    assert "src.x() + rect.x()" in source
    assert "src.y() + rect.y()" in source
