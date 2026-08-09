from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NATIVE = (ROOT / "gui" / "native_background.py").read_text(encoding="utf-8")
VISUAL = (ROOT / "gui" / "visual_style.py").read_text(encoding="utf-8")
RUNNER = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_wallpaper_uses_native_quick_scene_graph_frame_cadence() -> None:
    assert "QQuickWindow" in NATIVE
    assert "FrameAnimation" in NATIVE
    assert "frameTime" in NATIVE
    assert "QSG_RENDER_LOOP" in RUNNER
    assert '"threaded"' in RUNNER
    assert "QQuickWidget" not in NATIVE + VISUAL
    assert "QOpenGLWidget" not in NATIVE + VISUAL
    assert "self.scroll(" not in NATIVE
    assert "setInterval(16)" not in NATIVE


def test_real_glass_is_one_global_mask_over_preblurred_wallpaper() -> None:
    assert "def _blur_wallpaper" in NATIVE
    assert "ShaderEffect" in NATIVE
    assert "property variant blurTex: blurHost" in NATIVE
    assert "property variant maskTex: maskImg" in NATIVE
    assert "blur.a * mask.a" in NATIVE
    assert "root.imageX" in NATIVE
    assert "root.imageY" in NATIVE
    assert "wallpaper_blurred.jpg" in NATIVE
    assert "ShaderEffectSource" not in NATIVE
    assert "QGraphicsBlurEffect" not in VISUAL


def test_glass_mask_comes_from_live_widget_geometry_not_hardcoded_cards() -> None:
    assert 'frame.objectName() in _GLASS_NAMES' in NATIVE
    assert "frame.mapToGlobal(QPoint(0, 0))" in NATIVE
    assert "path.addRoundedRect(rect" in NATIVE
    assert "schedule_mask_update" in NATIVE
    assert "QEvent.Type.LayoutRequest" in NATIVE
    assert "verticalScrollBar().valueChanged.connect(self.schedule_mask_update)" in NATIVE
    assert "horizontalScrollBar().valueChanged.connect(self.schedule_mask_update)" in NATIVE
    assert "GLASS_CARDS = [" not in NATIVE


def test_card_hover_keeps_local_tint_layer_without_per_card_blur() -> None:
    glass = VISUAL.split("class GlassBackdrop", 1)[1].split("class VisualStyleController", 1)[0]
    assert "painter.drawRoundedRect" in glass
    assert "_overlay_alpha" in glass
    assert "QGraphicsBlurEffect" not in glass
    assert "drawPixmap" not in glass
    assert "background.transform_changed" not in glass


def test_legacy_qwidget_background_paint_is_suppressed_without_layout_rewrite() -> None:
    assert "watched is self.central and event.type() == QEvent.Type.Paint" in VISUAL
    assert "return True" in VISUAL.split("watched is self.central", 1)[1].split("if isinstance(watched, QFrame)", 1)[0]


def test_native_background_has_explicit_shutdown_contract() -> None:
    shutdown = NATIVE.split("def shutdown(self)", 1)[1]
    assert 'setProperty("animationRunning", False)' in shutdown
    assert "quick.hide()" in shutdown
    assert "quick.releaseResources()" in shutdown
    assert "quick.close()" in shutdown
    assert "quick.deleteLater()" in shutdown
    assert "self.engine.clearComponentCache()" in shutdown
    assert "self.engine.deleteLater()" in shutdown
    assert "self._temp.cleanup()" in shutdown
