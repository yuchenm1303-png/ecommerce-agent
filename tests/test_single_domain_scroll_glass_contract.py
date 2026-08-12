from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "gui" / "scroll_local_glass.py").read_text(encoding="utf-8")


def test_viewport_glass_source_compiles() -> None:
    compile(SOURCE, str(ROOT / "gui" / "scroll_local_glass.py"), "exec")


def test_product_source_uses_viewport_owned_widget_glass() -> None:
    assert "class _ViewportGlassLayer(QWidget)" in SOURCE
    assert "class _ViewportContentScaleEffect(_CardScaleEffect)" in SOURCE
    assert 'setObjectName("singlePageViewportGlass")' in SOURCE
    assert "layer.stackUnder(page)" in SOURCE
    assert "page.raise_()" in SOURCE
    assert '"heroCard"' in SOURCE
    assert '"statusRowHost"' not in SOURCE


def test_product_source_leaves_the_quick_card_model() -> None:
    assert "model.beginRemoveRows(QModelIndex(), row, row)" in SOURCE
    assert "del cards[row]" in SOURCE
    assert "del states[row]" in SOURCE


def test_scroll_hot_path_updates_only_widget_glass_geometry() -> None:
    assert "frameSwapped" not in SOURCE
    assert ".repaint(" not in SOURCE
    assert "_SCROLL_SETTLE_MS" not in SOURCE
    assert "valueChanged.connect(self._sync_scroll_position)" in SOURCE

    block = SOURCE.split("def _sync_scroll_position", 1)[1].split(
        "def _invalidate_card_region", 1
    )[0]
    assert "layer.sync_card_geometry()" in block
    for forbidden in (
        "schedule_mask_update",
        "card_model.sync_geometry",
        "_refresh_backdrop",
        "frameSwapped",
        ".repaint(",
    ):
        assert forbidden not in block


def test_glass_background_stays_live_in_viewport_coordinates() -> None:
    paint = SOURCE.split("def paint_glass", 1)[1].split(
        "def _sync_initial_state", 1
    )[0]
    assert "card_rect_in_viewport()" in paint
    assert "self.viewport.mapTo(self.window, QPoint(0, 0))" in paint
    assert "self._quick_offset()" in paint
    assert "painter.drawPixmap(card_rect, item, source)" in paint

    assert "animationRunningChanged" in SOURCE
    assert "_PARALLAX_REPAINT_MS = 16" in SOURCE
    assert "self._parallax_timer.timeout.connect(self._parallax_tick)" in SOURCE


def test_content_hover_scale_reuses_existing_native_effect_contract() -> None:
    assert "from .native_visual_style import _CardScaleEffect" in SOURCE
    assert "proxy._scale_effect = effect" in SOURCE
    assert "frame.setGraphicsEffect(effect)" in SOURCE
