from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "gui" / "visual_style.py").read_text(encoding="utf-8")


def test_parallax_uses_fractional_source_coordinates() -> None:
    assert "def parallax_source_rect(self) -> QRectF:" in SOURCE
    tick = SOURCE.split("def _parallax_tick(self)", 1)[1].split("def _detach_parallax", 1)[0]
    assert "round(" not in tick
    assert "self.scroll(" not in tick
    assert "self.transform_changed.emit()" in tick


def test_glass_samples_live_blurred_scene_instead_of_frozen_cache() -> None:
    glass = SOURCE.split("class GlassBackdrop", 1)[1].split("class VisualStyleController", 1)[0]
    assert "_surface_cache" not in glass
    assert "self.background.transform_changed.connect(self.update)" in glass
    assert "scene = self.background.blurred_scene()" in glass
    assert "source_rect = self._live_source_rect(scene)" in glass
    assert "painter.drawPixmap(target_rect, scene, source_rect)" in glass
