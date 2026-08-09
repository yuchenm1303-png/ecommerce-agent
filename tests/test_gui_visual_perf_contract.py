from __future__ import annotations

from pathlib import Path


VISUAL_STYLE = Path(__file__).resolve().parents[1] / "gui" / "visual_style.py"


def test_visual_scene_uses_one_qwidget_compositor_without_opengl_mixing() -> None:
    source = VISUAL_STYLE.read_text(encoding="utf-8")
    assert "class VisualSceneLayer(QWidget):" in source
    assert "QOpenGLWidget" not in source
    assert "def paintEvent(self, event)" in source
    assert "def paintGL" not in source
    assert "self.scroll(" not in source
    assert "_surface_cache" not in source


def test_wallpaper_motion_has_no_per_frame_scale_blur_or_copy() -> None:
    source = VISUAL_STYLE.read_text(encoding="utf-8")
    motion = source.split("def _motion_tick(self) -> None:", 1)[1].split("def update_frame", 1)[0]
    assert ".scaled(" not in motion
    assert "_blur_pixmap" not in motion
    assert ".copy(" not in motion
    assert "self._motion_timer.setTimerType(Qt.PreciseTimer)" in source
    assert "self._blurred = _blur_pixmap(self._render, 10.0)" in source


def test_global_glass_mask_replaces_per_card_blur_sampling() -> None:
    source = VISUAL_STYLE.read_text(encoding="utf-8")
    paint = source.split("def paintEvent(self, event) -> None:", 1)[1].split("def _detach", 1)[0]
    assert "self._glass_mask" in source
    assert "painter.setClipRegion(self._glass_mask)" in paint
    assert paint.count("painter.drawPixmap(self.rect(), self._render, self._source_rect)") == 1
    assert paint.count("painter.drawPixmap(self.rect(), self._blurred, self._source_rect)") == 1
    assert "src.x() + rect.x()" not in paint
    assert "src.y() + rect.y()" not in paint
    assert "painter.drawPixmap(target, self._blurred" not in paint
    assert "class GlassLayer" not in source
    assert "transform_changed" not in source


def test_parallax_is_small_and_card_geometry_stays_out_of_motion_path() -> None:
    source = VISUAL_STYLE.read_text(encoding="utf-8")
    assert "_MAX_TRAVEL_PX = 12.0" in source
    assert "_OVERSCAN = 1.03" in source
    assert "self._geometry_cache" in source
    assert "self._geometry_timer.setInterval(16)" in source
    motion = source.split("def _motion_tick(self) -> None:", 1)[1].split("def update_frame", 1)[0]
    assert "_visible_frame_rect" not in motion
    assert "mapToGlobal" not in motion
    assert "QRegion" not in motion
