from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUN = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")
LOCAL = (ROOT / "gui" / "scroll_local_glass.py").read_text(encoding="utf-8")


def test_runner_enables_local_glass_only_after_all_current_glass_frames_exist() -> None:
    assert "from gui.scroll_local_glass import install_scroll_local_glass" in RUN
    assert "install_scroll_local_glass(window, visual)" in RUN
    assert RUN.index("visual.refresh_glass_frames()") < RUN.index("install_scroll_local_glass(window, visual)")
    assert RUN.index("install_scroll_local_glass(window, visual)") < RUN.index("install_ui_runtime_optimizations(window, visual)")
    assert RUN.index("install_scroll_local_glass(window, visual)") < RUN.index("install_nekro_card_fx(window, visual)")


def test_prototype_scope_is_only_product_source_and_single_status_cards() -> None:
    assert '"heroCard"' in LOCAL
    assert 'findChild(QWidget, "statusRowHost")' in LOCAL
    assert 'frame.objectName() == "statusCard"' in LOCAL
    assert "field_table" not in LOCAL
    assert "side_detail_tabs" not in LOCAL
    assert "console" not in LOCAL


def test_local_cards_are_removed_from_independent_quick_card_model() -> None:
    detach = LOCAL.split("def _detach_from_quick_model", 1)[1].split("def _ensure_scaled_item", 1)[0]
    assert "beginRemoveRows(QModelIndex(), row, row)" in detach
    assert "del cards[row]" in detach
    assert "del states[row]" in detach
    assert "model._rows =" in detach


def test_local_glass_reuses_preblurred_fuji_without_live_blur_or_window_grabs() -> None:
    assert 'getattr(self.background, "_blur_path", "")' in LOCAL
    assert "_OVERSCAN" in LOCAL
    assert "KeepAspectRatioByExpanding" in LOCAL
    assert "SmoothTransformation" in LOCAL
    assert "QGraphicsBlurEffect" not in LOCAL
    assert "grabWindow" not in LOCAL
    assert ".grab()" not in LOCAL


def test_scroll_repaints_only_local_layers_synchronously_in_widget_domain() -> None:
    assert "verticalScrollBar().valueChanged.connect(self._repaint_for_scroll)" in LOCAL
    repaint = LOCAL.split("def _repaint_for_scroll", 1)[1].split("def _invalidate_scaled_item", 1)[0]
    assert "self._repaint_visible_layers(sync=True)" in repaint
    helper = LOCAL.split("def _repaint_visible_layers", 1)[1].split("def _repaint_for_scroll", 1)[0]
    assert "layer.repaint()" in helper
    assert "layer.visibleRegion().isEmpty()" in helper


def test_local_shell_keeps_existing_hover_alpha_and_parallax_alignment() -> None:
    paint = LOCAL.split("def paintEvent", 1)[1].split("class ScrollLocalGlassController", 1)[0]
    assert 'getattr(self.proxy, "overlay_alpha", _NORMAL_GLASS_ALPHA)' in paint
    assert "frameSwapped.connect" in LOCAL
    assert 'quick.property("offsetX")' in LOCAL
    assert 'quick.property("offsetY")' in LOCAL


def test_local_layer_stays_mouse_transparent_and_below_real_controls() -> None:
    assert "WA_TransparentForMouseEvents" in LOCAL
    assert "self.lower()" in LOCAL
    assert "QPainterPath" in LOCAL
    assert "addRoundedRect" in LOCAL


def test_source_compiles_without_importing_pyside() -> None:
    compile(LOCAL, str(ROOT / "gui" / "scroll_local_glass.py"), "exec")
