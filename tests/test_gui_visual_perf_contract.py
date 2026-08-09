from __future__ import annotations

from pathlib import Path


VISUAL_STYLE = Path(__file__).resolve().parents[1] / "gui" / "visual_style.py"


def test_wallpaper_motion_has_no_per_frame_rebuild_or_pixel_scroll() -> None:
    source = VISUAL_STYLE.read_text(encoding="utf-8")
    assert "self.scroll(" not in source
    assert "_surface_cache" not in source
    assert "self._motion_timer.setTimerType(Qt.PreciseTimer)" in source
    assert "self._blurred = _blur_pixmap(self._render, 10.0)" in source


def test_wallpaper_and_glass_share_one_paint_layer() -> None:
    source = VISUAL_STYLE.read_text(encoding="utf-8")
    assert "class VisualSceneLayer(QWidget):" in source
    assert "class GlassLayer(QWidget):" not in source
    assert "transform_changed" not in source
    assert "painter.drawPixmap(self.rect(), self._render, self._source_rect)" in source
    assert "src.x() + rect.x()" in source
    assert "src.y() + rect.y()" in source


def test_card_geometry_is_cached_outside_wallpaper_motion_path() -> None:
    source = VISUAL_STYLE.read_text(encoding="utf-8")
    assert "self._geometry_cache" in source
    assert "self._geometry_timer.setInterval(16)" in source
    motion = source.split("def _motion_tick(self) -> None:", 1)[1].split("def update_frame", 1)[0]
    assert "_visible_frame_rect" not in motion
    assert "mapToGlobal" not in motion
    assert "QPainterPath" not in motion
