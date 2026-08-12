from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "gui" / "scroll_local_glass.py").read_text(encoding="utf-8")


def test_single_domain_scroll_glass_source_compiles() -> None:
    compile(SOURCE, str(ROOT / "gui" / "scroll_local_glass.py"), "exec")


def test_product_source_owns_one_widget_composite_without_child_glass_layer() -> None:
    assert "class _SingleDomainGlassEffect(QGraphicsEffect)" in SOURCE
    assert "class _LocalGlassLayer" not in SOURCE
    assert '"heroCard"' in SOURCE
    assert '"statusRowHost"' not in SOURCE
    assert "proxy._scale_effect = effect" in SOURCE
    assert "frame.setGraphicsEffect(effect)" in SOURCE


def test_product_source_leaves_the_quick_card_model() -> None:
    assert "model.beginRemoveRows(QModelIndex(), row, row)" in SOURCE
    assert "del cards[row]" in SOURCE
    assert "del states[row]" in SOURCE


def test_scroll_hot_path_never_repaints_or_waits_for_quick_frames() -> None:
    assert "frameSwapped" not in SOURCE
    assert ".repaint(" not in SOURCE
    assert "valueChanged.connect(self._mark_scrolling)" in SOURCE
    assert "_SCROLL_SETTLE_MS = 84" in SOURCE

    block = SOURCE.split("def _mark_scrolling", 1)[1].split("def _finish_scroll", 1)[0]
    assert "self._scrolling = True" in block
    assert "self._settle_timer.start()" in block
    for forbidden in (
        "_refresh_backdrop",
        ".update(",
        ".repaint(",
        "sync_geometry",
        "schedule_mask_update",
        "frameSwapped",
    ):
        assert forbidden not in block


def test_backdrop_is_resampled_only_after_scroll_settles() -> None:
    finish = SOURCE.split("def _finish_scroll", 1)[1].split("def _invalidate_scene_cache", 1)[0]
    assert "self._scrolling = False" in finish
    assert "self._refresh_backdrop()" in finish

    assert "animationRunningChanged" in SOURCE
    assert "if not running:" in SOURCE
