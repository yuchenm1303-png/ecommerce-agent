from __future__ import annotations

from pathlib import Path


VISUAL_STYLE = Path(__file__).resolve().parents[1] / "gui" / "visual_style.py"


def test_wallpaper_motion_uses_gpu_scene_not_qwidget_raster_backing_store() -> None:
    source = VISUAL_STYLE.read_text(encoding="utf-8")
    assert "from PySide6.QtOpenGLWidgets import QOpenGLWidget" in source
    assert "class VisualSceneLayer(QOpenGLWidget):" in source
    assert "def paintGL(self) -> None:" in source
    assert "class VisualSceneLayer(QWidget):" not in source
    assert "def paintEvent(self, event)" not in source
    assert "self.scroll(" not in source
    assert "_surface_cache" not in source


def test_wallpaper_motion_is_frame_swap_driven_without_motion_timer() -> None:
    source = VISUAL_STYLE.read_text(encoding="utf-8")
    assert "self.frameSwapped.connect(self._on_frame_swapped)" in source
    assert "def _on_frame_swapped(self) -> None:" in source
    assert "self._motion_timer" not in source
    assert "self._motion_active" in source


def test_wallpaper_motion_has_no_per_frame_scale_blur_or_copy() -> None:
    source = VISUAL_STYLE.read_text(encoding="utf-8")
    motion = source.split("def _advance_motion(self) -> None:", 1)[1].split("def _on_frame_swapped", 1)[0]
    assert ".scaled(" not in motion
    assert "_blur_pixmap" not in motion
    assert ".copy(" not in motion
    assert "self._blurred = _blur_pixmap(self._render, 10.0)" in source


def test_wallpaper_and_glass_share_one_gpu_paint_pass() -> None:
    source = VISUAL_STYLE.read_text(encoding="utf-8")
    paint = source.split("def paintGL(self) -> None:", 1)[1].split("def _detach", 1)[0]
    assert "painter.drawPixmap(self.rect(), self._render, self._source_rect)" in paint
    assert "painter.drawPixmap(target, self._blurred, QRectF(sample))" in paint
    assert "src.x() + rect.x()" in paint
    assert "src.y() + rect.y()" in paint
    assert "class GlassLayer" not in source
    assert "transform_changed" not in source


def test_card_geometry_is_cached_outside_wallpaper_motion_path() -> None:
    source = VISUAL_STYLE.read_text(encoding="utf-8")
    assert "self._geometry_cache" in source
    assert "self._geometry_timer.setInterval(16)" in source
    motion = source.split("def _advance_motion(self) -> None:", 1)[1].split("def _on_frame_swapped", 1)[0]
    assert "_visible_frame_rect" not in motion
    assert "mapToGlobal" not in motion
    assert "QPainterPath" not in motion
