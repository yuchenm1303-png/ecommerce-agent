from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = (ROOT / "gui" / "ui_runtime_optimizations.py").read_text(encoding="utf-8")
CARD_FX = (ROOT / "gui" / "nekro_card_fx.py").read_text(encoding="utf-8")
SCROLL = (ROOT / "gui" / "smooth_scroll.py").read_text(encoding="utf-8")
RUN = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_runtime_optimization_source_compiles_without_importing_pyside() -> None:
    compile(RUNTIME, "gui/ui_runtime_optimizations.py", "exec")
    compile(CARD_FX, "gui/nekro_card_fx.py", "exec")
    compile(SCROLL, "gui/smooth_scroll.py", "exec")


def test_runtime_layer_is_presentation_only_and_installed_before_event_loop() -> None:
    assert "Business runners, permission gates, values, geometry, colors and transition" in RUNTIME
    assert "install_ui_runtime_optimizations(window, visual)" in RUN
    assert RUN.index("install_ui_runtime_optimizations(window, visual)") < RUN.index("shell.show()")
    assert RUN.index("visual.refresh_glass_frames()") < RUN.index(
        "install_ui_runtime_optimizations(window, visual)"
    )


def test_glass_mask_uses_in_memory_provider_with_png_fallback() -> None:
    assert "QQuickImageProvider" in RUNTIME
    assert "engine.addImageProvider(_MASK_PROVIDER_ID, provider)" in RUNTIME
    assert "bg.card_model.render_mask(quick.width(), quick.height())" in RUNTIME
    assert 'QUrl(f"image://{_MASK_PROVIDER_ID}/{bg._mask_revision}")' in RUNTIME
    assert "self._original_mask_update()" in RUNTIME
    assert "image.save(" not in RUNTIME


def test_minimize_restore_keeps_quick_resources_and_last_complete_glass_mask() -> None:
    assert "class _MinimizeRestoreKeeper(QObject)" in RUNTIME
    assert "self.quick.setPersistentGraphics(True)" in RUNTIME
    assert "self.quick.setPersistentSceneGraph(True)" in RUNTIME
    assert "QEvent.Type.WindowStateChange" in RUNTIME
    assert "QEvent.Type.Expose" in RUNTIME
    assert "not quick.isExposed()" in RUNTIME
    assert "self.background._geometry_dirty = True" in RUNTIME
    assert "self._original_flush()" in RUNTIME
    assert "self.background._last_pointer_norm = None" in RUNTIME

    suspend = RUNTIME.split("    def _suspend(self) -> None:", 1)[1].split(
        "    def _modal_holds_underlay", 1
    )[0]
    assert "geometry_timer.stop()" in suspend
    assert "pointer_timer.stop()" in suspend
    assert 'setProperty("animationRunning", False)' in suspend
    assert "_mask_ready = False" not in suspend
    assert "card_model.sync_geometry" not in suspend

    flush = RUNTIME.split("    def _flush_geometry(self) -> None:", 1)[1].split(
        "    def eventFilter", 1
    )[0]
    assert "if self._should_suspend():" in flush
    assert "self._suspend()" in flush
    assert "return" in flush
    assert "self._original_flush()" in flush


def test_idle_background_pointer_sampling_keeps_8ms_semantics_but_skips_static_work() -> None:
    assert "def _install_idle_pointer_fast_path" in RUNTIME
    assert "timer.timeout.disconnect(original)" in RUNTIME
    assert "controller._last_pointer_global == point" in RUNTIME
    assert "controller._last_pointer_quick_geometry == geometry" in RUNTIME
    assert 'getattr(bg, "_last_pointer_norm", None) is not None' in RUNTIME
    assert "original()" in RUNTIME


def test_tables_reuse_items_cached_brushes_and_batch_row_fingerprints() -> None:
    assert "item = table.item(row, column)" in RUNTIME
    assert "if item is None:" in RUNTIME
    assert "self._ai_status_brushes" in RUNTIME
    assert "self._final_status_brushes" in RUNTIME
    assert "self._batch_status_brushes" in RUNTIME
    assert "self._batch_row_fingerprints" in RUNTIME
    assert "previous != new_fingerprints" in RUNTIME
    assert "previous[row] == new_fingerprints[row]" in RUNTIME
    assert "if old_rows != new_rows:" in RUNTIME
    assert "table.resizeRowsToContents()" in RUNTIME


def test_activity_log_filters_forward_only_existing_progress_markers() -> None:
    assert "prep.log.disconnect(prep_handler)" in RUNTIME
    assert "real.log.disconnect(real_handler)" in RUNTIME
    for marker in (
        "STEP 3 CURRENT RESOLVER · COLD",
        "STEP 3 CURRENT RESOLVER · HOT/CACHE",
        "STEP 3 CURRENT READ-ONLY FILL PLAN",
        "GUI_EXEC_FIELD\\t",
        "Price, Stock and Shipping Information:",
        "Product Description:",
        "Additional Description:",
        "photos:",
        "MAKRO STEP 3 DIRECT ACCEPTANCE",
        "ACCEPTANCE COMPLETE",
        "PREVIEW READY",
    ):
        assert marker in RUNTIME


def test_card_visual_parameters_are_current_while_hot_paths_are_local() -> None:
    assert "_NORMAL_ALPHA = 64.0" in CARD_FX
    assert "_HOVER_ALPHA = 102.0" in CARD_FX
    assert "_ACTIVE_ALPHA = 102.0" in CARD_FX
    assert "_TRANSITION_MS = 300" in CARD_FX
    assert "_POINTER_SAMPLE_MS = 8" in CARD_FX
    assert "_MIN_PRESSED_MS" not in CARD_FX
    assert "self._moving_frames" in CARD_FX
    assert "def _hover_still_owns_global" in CARD_FX
    assert "self.window.childAt(local)" in CARD_FX
    assert "widget.installEventFilter(self)" not in CARD_FX
    assert "QApplication.widgetAt" not in CARD_FX


def test_smooth_scroll_keeps_nominal_ease_but_cannot_spin_on_unreachable_target() -> None:
    assert "_STEP_MS = 16" in SCROLL
    assert "_EASE = 0.18" in SCROLL
    assert "_clamp_target" in SCROLL
    assert "if step == 0:" in SCROLL
    assert "if bar.value() == current:" in SCROLL
    assert "math.pow(1.0 - self._EASE, dt / self._REFERENCE_DT_S)" in SCROLL
