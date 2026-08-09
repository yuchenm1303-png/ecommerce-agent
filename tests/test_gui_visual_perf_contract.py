from __future__ import annotations

from pathlib import Path


VISUAL_STYLE = Path(__file__).resolve().parents[1] / "gui" / "visual_style.py"


def test_visual_scene_stays_one_qwidget_compositor_without_opengl_mixing() -> None:
    source = VISUAL_STYLE.read_text(encoding="utf-8")
    assert "class VisualSceneLayer(QWidget):" in source
    assert "QOpenGLWidget" not in source
    assert "def paintEvent(self, event)" in source
    assert "def paintGL" not in source
    assert "_surface_cache" not in source


def test_motion_reuses_backing_store_instead_of_full_scene_repaint() -> None:
    source = VISUAL_STYLE.read_text(encoding="utf-8")
    apply_rect = source.split("def _apply_source_rect(self, next_rect: QRect) -> None:", 1)[1].split(
        "def _motion_tick", 1
    )[0]
    motion = source.split("def _motion_tick(self) -> None:", 1)[1].split("def update_frame", 1)[0]

    assert "self.scroll(screen_dx, screen_dy)" in apply_rect
    assert "_edge_exposure_region(screen_dx, screen_dy)" in apply_rect
    assert "_scroll_repair_region(screen_dx, screen_dy)" in apply_rect
    assert "self._apply_source_rect(self._rect_for_offset(self._offset))" in motion
    assert "self.update()" not in motion


def test_glass_boundary_repair_uses_xor_not_per_card_blur_sampling() -> None:
    source = VISUAL_STYLE.read_text(encoding="utf-8")
    repair = source.split("def _scroll_repair_region(self, dx: int, dy: int) -> QRegion:", 1)[1].split(
        "def _apply_source_rect", 1
    )[0]
    paint = source.split("def paintEvent(self, event) -> None:", 1)[1].split("def _detach", 1)[0]

    assert "self._glass_mask.xored(self._glass_mask.translated(dx, dy))" in repair
    assert "self._repair_cache" in source
    assert "painter.setClipRegion(self._glass_mask)" in paint
    assert paint.count("painter.drawPixmap(self.rect(), self._render, self._source_rect)") == 1
    assert paint.count("painter.drawPixmap(self.rect(), self._blurred, self._source_rect)") == 1
    assert "painter.drawPixmap(target, self._blurred" not in paint
    assert "src.x() + rect.x()" not in paint
    assert "src.y() + rect.y()" not in paint


def test_motion_has_no_per_frame_scale_blur_copy_or_geometry_mapping() -> None:
    source = VISUAL_STYLE.read_text(encoding="utf-8")
    motion = source.split("def _motion_tick(self) -> None:", 1)[1].split("def update_frame", 1)[0]
    apply_rect = source.split("def _apply_source_rect(self, next_rect: QRect) -> None:", 1)[1].split(
        "def _motion_tick", 1
    )[0]

    for hot_path in (motion, apply_rect):
        assert ".scaled(" not in hot_path
        assert "_blur_pixmap" not in hot_path
        assert ".copy(" not in hot_path
        assert "mapToGlobal" not in hot_path
        assert "_visible_frame_rect" not in hot_path

    assert "self._blurred = _blur_pixmap(self._render, 10.0)" in source


def test_motion_cadence_is_refresh_aware_and_never_queues_multiple_paints() -> None:
    source = VISUAL_STYLE.read_text(encoding="utf-8")
    motion = source.split("def _motion_tick(self) -> None:", 1)[1].split("def update_frame", 1)[0]

    assert "screen.refreshRate()" in source
    assert "_MAX_REFRESH_HZ = 165.0" in source
    assert "self._motion_timer.setTimerType(Qt.PreciseTimer)" in source
    assert "self._configure_motion_timer()" in source
    assert "self._paint_pending" in source
    assert "if self._paint_pending:" in motion
    assert "self._paint_pending = False" in source.split("def paintEvent", 1)[1]


def test_parallax_remains_small_and_geometry_is_cached() -> None:
    source = VISUAL_STYLE.read_text(encoding="utf-8")
    assert "_MAX_TRAVEL_PX = 12.0" in source
    assert "_OVERSCAN = 1.03" in source
    assert "self._geometry_cache" in source
    assert "self._geometry_timer.setInterval(16)" in source
