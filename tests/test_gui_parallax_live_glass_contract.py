from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "gui" / "visual_style.py").read_text(encoding="utf-8")


def test_parallax_is_fractional_and_frame_swap_driven() -> None:
    assert "class BackgroundLayer(QOpenGLWidget):" in SOURCE
    assert "self.frameSwapped.connect(self._on_frame_swapped)" in SOURCE
    assert "def parallax_source_rect(self) -> QRectF:" in SOURCE
    advance = SOURCE.split("def _advance_motion(self)", 1)[1].split("def _on_frame_swapped", 1)[0]
    assert "round(" not in advance
    assert "self.scroll(" not in advance
    assert "math.exp" in advance


def test_motion_hot_path_is_one_raw_gpu_pass() -> None:
    paint = SOURCE.split("def paintGL(self)", 1)[1].split("def cleanup_gl", 1)[0]
    assert "glDrawArrays" in paint
    assert "QPainter" not in paint
    assert ".scaled(" not in paint
    assert "_blur_pixmap" not in paint
    assert ".copy(" not in paint
    assert "mapToGlobal" not in paint
    assert "for frame" not in paint


def test_sharp_blur_and_glass_mask_are_mixed_in_shader() -> None:
    assert "uniform sampler2D u_sharp;" in SOURCE
    assert "uniform sampler2D u_blur;" in SOURCE
    assert "uniform sampler2D u_mask;" in SOURCE
    assert "gl_FragColor = mix(sharp, blurred, glass);" in SOURCE
    assert "QOpenGLTexture.MipMapGeneration.DontGenerateMipMaps" in SOURCE
    assert "QOpenGLTexture.Filter.Linear" in SOURCE


def test_card_backdrops_only_paint_tint_during_motion() -> None:
    glass = SOURCE.split("class GlassBackdrop", 1)[1].split("class VisualStyleController", 1)[0]
    assert "blurred_scene" not in glass
    assert "parallax_source_rect" not in glass
    assert "QPainter" in glass
    assert "painter.fillPath" in glass
    assert "_overlay_alpha" in glass


def test_glass_mask_rebuild_is_geometry_driven_not_motion_driven() -> None:
    controller = SOURCE.split("class VisualStyleController", 1)[1]
    assert "bar.valueChanged.connect(self._schedule_mask_rebuild)" in controller
    assert "QEvent.Resize, QEvent.Move, QEvent.Show, QEvent.Hide" in controller
    assert "self.background.set_glass_mask(image)" in controller
    background = SOURCE.split("class BackgroundLayer", 1)[1].split("class GlassBackdrop", 1)[0]
    assert "_schedule_mask_rebuild" not in background


def test_gpu_glass_mask_preserves_widget_ancestor_clipping() -> None:
    controller = SOURCE.split("class VisualStyleController", 1)[1]
    rebuild = controller.split("def _rebuild_mask(self)", 1)[1].split("def eventFilter", 1)[0]
    assert "ancestor = frame.parentWidget()" in rebuild
    assert "ancestor.mapTo(central" in rebuild
    assert "visible_clip = visible_clip.intersected(ancestor_rect)" in rebuild
    assert "painter.setClipRect(visible_clip)" in rebuild
