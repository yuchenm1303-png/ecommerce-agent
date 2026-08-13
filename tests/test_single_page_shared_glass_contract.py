from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")
PAGE = (ROOT / "gui" / "page_scroll_layout.py").read_text(encoding="utf-8")
GLASS = (ROOT / "gui" / "scroll_local_glass.py").read_text(encoding="utf-8")


def _body(source: str, start: str, end: str) -> str:
    return source.split(start, 1)[1].split(end, 1)[0]


def test_single_uses_one_page_owned_parent_glass_layer_not_per_card_backdrops() -> None:
    assert "widget_card_rendering" not in RUNNER
    assert "install_widget_card_rendering" not in RUNNER
    assert "from .scroll_local_glass import install_scroll_local_glass" in PAGE
    assert "install_scroll_local_glass(window, resolved_visual, scroll, page)" in PAGE
    assert "class _SinglePageGlassLayer(QWidget)" in GLASS
    assert "super().__init__(controller.page)" in GLASS
    assert 'self.setObjectName("singlePageSharedGlass")' in GLASS
    assert "class _LocalGlassBackdrop" not in GLASS


def test_outer_scroll_hot_path_has_no_per_card_geometry_or_quick_mask_work() -> None:
    hot = _body(GLASS, "def _on_scroll", "def _invalidate_scene_cache")
    assert "layer.update_visible_region()" in hot
    for forbidden in (
        "for record",
        "for frame",
        "mapTo(",
        "card_model",
        "sync_geometry",
        "render_mask",
        "schedule_mask_update",
        "setProperty(",
        "quick.update",
    ):
        assert forbidden not in hot

    assert "valueChanged.connect(schedule_mask)" not in PAGE
    assert "QTimer.singleShot(0, schedule_mask)" not in PAGE
    assert "sync_scroll_glass" not in PAGE


def test_single_card_geometry_is_cached_in_page_coordinates_not_remeasured_on_scroll() -> None:
    page_rect = _body(GLASS, "def _page_rect", "def _record_for")
    rebuild = _body(GLASS, "def _rebuild_records", "def _quick_offset")
    events = _body(GLASS, "def eventFilter", "def install_scroll_local_glass")

    assert "widget.mapTo(self.page" in page_rect
    assert "for frame in self._page_glass_frames():" in rebuild
    assert "QEvent.Type.Move" not in events.split("if watched is self.page:", 1)[1].split(
        "elif watched is self.viewport", 1
    )[0]
    assert "QScrollArea scrolling moves the page" in events


def test_single_rows_leave_quick_but_batch_refresh_still_uses_original_native_model() -> None:
    migrate = _body(GLASS, "def _detach_quick_rows", "def _hook_proxy")
    refresh = _body(GLASS, "def _wrap_refresh_glass_frames", "def _queue_layout_sync")

    assert "model.beginRemoveRows" in migrate
    assert "del cards[row]" in migrate
    assert "del states[row]" in migrate
    assert "model._rows = {frame: row for row, frame in enumerate(cards)}" in migrate
    assert "added = int(original())" in refresh
    assert "controller._detach_quick_rows(frames)" in refresh
    assert "window.install_mode_workspace()" in RUNNER
    assert "visual.refresh_glass_frames()" in RUNNER


def test_shared_layer_reuses_preblur_and_tracks_presented_parallax_without_stale_pixels() -> None:
    assert 'self._source = QPixmap(str(getattr(self.background, "_blur_path", "")))' in GLASS
    assert "self._cover_key" in GLASS
    assert "self.quick.frameSwapped.connect(self._on_quick_frame)" in GLASS
    assert "CompositionMode_Source" in GLASS
    assert "painter.fillRect(event.rect(), Qt.GlobalColor.transparent)" in GLASS
    assert "self.controller.scaled_rect(record.rect, 1.04)" in GLASS
    assert 'window._scroll_local_glass = controller' in GLASS


def test_shared_glass_sources_compile_without_importing_pyside() -> None:
    for path, source in (
        (ROOT / "run_local_gui.py", RUNNER),
        (ROOT / "gui" / "page_scroll_layout.py", PAGE),
        (ROOT / "gui" / "scroll_local_glass.py", GLASS),
    ):
        compile(source, str(path), "exec")
