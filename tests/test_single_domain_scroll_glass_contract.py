from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "gui" / "scroll_local_glass.py").read_text(encoding="utf-8")
PAGE_SOURCE = (ROOT / "gui" / "page_scroll_layout.py").read_text(encoding="utf-8")
RUN_SOURCE = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_final_viewport_glass_sources_compile() -> None:
    compile(SOURCE, str(ROOT / "gui" / "scroll_local_glass.py"), "exec")
    compile(PAGE_SOURCE, str(ROOT / "gui" / "page_scroll_layout.py"), "exec")


def test_one_viewport_compositor_owns_all_registered_scrolling_cards() -> None:
    assert "class _ViewportGlassLayer(QWidget)" in SOURCE
    assert 'setObjectName("singlePageViewportGlass")' in SOURCE
    assert "layer.stackUnder(self.page)" in SOURCE
    assert "self.page.raise_()" in SOURCE
    assert "for frame in list(cards):" in SOURCE
    assert "self._is_descendant_of(frame, page)" in SOURCE
    assert "surface_for(frame)" in SOURCE
    assert "self._targets = targets" in SOURCE
    assert "for frame, proxy in self._targets:" in SOURCE

    # The final implementation must not be keyed to one prototype card.
    assert '"heroCard"' not in SOURCE
    assert '"statusRowHost"' not in SOURCE
    assert "_ancestor_card" not in SOURCE


def test_all_migrated_scroll_cards_leave_the_quick_model() -> None:
    assert "removal_rows = sorted(" in SOURCE
    assert "{int(rows[frame]) for frame in frames if frame in rows}" in SOURCE
    assert "model.beginRemoveRows(QModelIndex(), row, row)" in SOURCE
    assert "del cards[row]" in SOURCE
    assert "del states[row]" in SOURCE
    assert "model._rows = {frame: row for row, frame in enumerate(cards)}" in SOURCE


def test_scroll_hot_path_is_widget_only_and_single_pass() -> None:
    assert "frameSwapped" not in SOURCE
    assert ".repaint(" not in SOURCE
    assert "valueChanged.connect(self._sync_scroll_position)" in SOURCE

    block = SOURCE.split("def _sync_scroll_position", 1)[1].split(
        "def _invalidate_frame", 1
    )[0]
    assert "layer.sync_all_geometry()" in block
    for forbidden in (
        "schedule_mask_update",
        "card_model.sync_geometry",
        "sync_geometry()",
        "frameSwapped",
        ".repaint(",
    ):
        assert forbidden not in block

    # The page layout must no longer maintain a parallel Quick scroll-sync chain.
    assert "sync_scroll_glass" not in PAGE_SOURCE
    assert "card_model" not in PAGE_SOURCE
    assert "schedule_mask_update" not in PAGE_SOURCE
    assert "QTimer.singleShot(0, sync_scroll_glass)" not in PAGE_SOURCE


def test_scroll_geometry_is_cached_and_reused_by_paint_and_parallax() -> None:
    assert "self._last_rects: dict[QFrame, QRectF]" in SOURCE
    assert "def card_rect(self, frame: QFrame) -> QRectF:" in SOURCE
    assert "self._proxy_by_frame = {frame: proxy for frame, proxy in targets}" in SOURCE
    assert "proxy = self._proxy_by_frame.get(frame)" in SOURCE

    paint = SOURCE.split("def paint_glass", 1)[1].split(
        "def _sync_initial_state", 1
    )[0]
    assert "for frame, proxy in self._targets:" in paint
    assert "card_rect = layer.card_rect(frame)" in paint
    assert "card_rect_in_viewport(frame)" not in paint

    refresh = SOURCE.split("def refresh_visible_cards", 1)[1].split(
        "def resize_to_viewport", 1
    )[0]
    assert "for rect in self._last_rects.values():" in refresh
    assert "card_rect_in_viewport" not in refresh


def test_glass_background_remains_live_in_viewport_coordinates() -> None:
    paint = SOURCE.split("def paint_glass", 1)[1].split(
        "def _sync_initial_state", 1
    )[0]
    assert "self.viewport.mapTo(self.window, QPoint(0, 0))" in paint
    assert "self._quick_offset()" in paint
    assert "painter.drawPixmap(card_rect, item, source)" in paint

    assert "animationRunningChanged" in SOURCE
    assert "_PARALLAX_REPAINT_MS = 16" in SOURCE
    assert "_PARALLAX_OFFSET_EPSILON = 0.02" in SOURCE
    assert "self._parallax_timer.timeout.connect(self._parallax_tick)" in SOURCE
    assert "layer.refresh_visible_cards()" in SOURCE


def test_every_migrated_card_keeps_existing_hover_press_content_effect() -> None:
    assert "from .native_visual_style import _CardScaleEffect" in SOURCE
    assert "class _ViewportContentScaleEffect(_CardScaleEffect)" in SOURCE
    assert "for frame, proxy in targets:" in SOURCE
    assert "frame.setGraphicsEffect(effect)" in SOURCE
    assert "proxy._scale_effect = effect" in SOURCE
    assert "self._invalidate_glass(self._frame)" in SOURCE


def test_launcher_installs_final_viewport_glass_after_all_single_cards_exist() -> None:
    assert "visual.refresh_glass_frames()" in RUN_SOURCE
    assert "install_scroll_local_glass(window, visual)" in RUN_SOURCE
    assert RUN_SOURCE.index("visual.refresh_glass_frames()") < RUN_SOURCE.index(
        "install_scroll_local_glass(window, visual)"
    )
