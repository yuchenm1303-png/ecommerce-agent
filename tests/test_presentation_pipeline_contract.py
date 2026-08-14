from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLOCK = (ROOT / "gui" / "presentation_clock.py").read_text(encoding="utf-8")
NATIVE = (ROOT / "gui" / "native_background.py").read_text(encoding="utf-8")
VISUAL = (ROOT / "gui" / "native_visual_style.py").read_text(encoding="utf-8")
CARD = (ROOT / "gui" / "nekro_card_fx.py").read_text(encoding="utf-8")
EFFECTS = (ROOT / "gui" / "nekro_effects.py").read_text(encoding="utf-8")
DATA = (ROOT / "gui" / "ui_data_optimizations.py").read_text(encoding="utf-8")
CACHE = (ROOT / "gui" / "wallpaper_cache.py").read_text(encoding="utf-8")
RUN = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_obsolete_runtime_patch_modules_are_gone() -> None:
    assert not (ROOT / "gui" / "background_render_optimizations.py").exists()
    assert not (ROOT / "gui" / "ui_runtime_optimizations.py").exists()


def test_one_shared_clock_owns_all_high_frequency_python_input_sampling() -> None:
    assert "_PRESENTATION_TICK_MS = 8" in CLOCK
    assert CLOCK.count("QCursor.pos()") == 1
    assert CLOCK.count("QApplication.mouseButtons()") == 1
    for consumer in (
        "self.background.presentation_tick(",
        "self.card_fx.presentation_tick(",
        "self.effects.presentation_tick(",
    ):
        assert consumer in CLOCK

    for source in (NATIVE, CARD, EFFECTS):
        assert "QCursor.pos()" not in source
        assert "QApplication.mouseButtons()" not in source


def test_glass_mask_is_native_quick_geometry_not_cpu_full_window_texture() -> None:
    assert "id: glassMask" in NATIVE
    assert "maskSource: glassMask" in NATIVE
    assert "model: glassCardModel" in NATIVE
    assert "clip: true" in NATIVE
    assert 'color: "white"' in NATIVE
    assert "def render_mask" not in NATIVE
    assert "def _update_mask_texture" not in NATIVE
    assert "maskUrl" not in NATIVE
    assert "QQuickImageProvider" not in NATIVE + DATA
    assert "_mask_revision" not in NATIVE
    assert "_geometry_revision" in NATIVE


def test_background_has_no_python_pointer_timer_and_retains_gpu_scene_graph() -> None:
    assert "def presentation_tick" in NATIVE
    assert "def reset_pointer_identity" in NATIVE
    assert "_pointer_timer" not in NATIVE
    assert "setPersistentGraphics(True)" in NATIVE
    assert "setPersistentSceneGraph(True)" in NATIVE
    assert "_GEOMETRY_SYNC_MS = 16" in NATIVE


def test_card_motion_uses_shared_clock_and_one_frozen_composite_per_tween() -> None:
    assert "_NORMAL_SCALE = 1.00" in CARD
    assert "_HOVER_SCALE = 1.02" in CARD
    assert "_TRANSITION_MS = 300" in CARD
    assert "_MAX_MOTION_HZ = 90.0" in CARD
    assert "_MAX_CONCURRENT_MOTIONS = 2" in CARD
    assert "def presentation_tick" in CARD
    assert "self._motion_timer" not in CARD
    assert "self._pointer_timer" not in CARD
    assert "def _recapture_for_motion" in CARD
    recapture = CARD.split("def _recapture_for_motion", 1)[1].split(
        "def _retire_stale_motions", 1
    )[0]
    assert "self._set_content_frozen(state, False)" in recapture
    assert "self._set_content_frozen(state, True)" in recapture
    advance = CARD.split("def _advance_state", 1)[1].split("def _advance_motions", 1)[0]
    assert "if not state.moving:" in advance
    assert "self._set_content_frozen(state, False)" in advance
    assert "self.sourcePixmap(" in VISUAL
    assert "return self._frozen_source, self._frozen_offset" in VISUAL


def test_decorative_effects_reuse_clock_and_keep_dirty_region_budget() -> None:
    assert "def presentation_tick" in EFFECTS
    assert "_FRAME_MS = 16" in EFFECTS
    assert "self.timer = QTimer" not in EFFECTS
    assert "QRegion()" in EFFECTS
    assert "self.update(dirty)" in EFFECTS


def test_runtime_data_optimizations_are_separate_from_rendering() -> None:
    assert "class UiDataOptimizations" in DATA
    assert "self._batch_row_fingerprints" in DATA
    assert "item = table.item(row, column)" in DATA
    assert "QQuick" not in DATA
    assert "QCursor" not in DATA
    assert "mask" not in DATA.lower()
    assert "presentation_tick" not in DATA


def test_wallpaper_cache_is_startup_only() -> None:
    assert '_CACHE_MAGIC = b"ECBGRAW1"' in CACHE
    assert "def install_preblur_cache" in CACHE
    assert "_native._blur_wallpaper = cached_blur" in CACHE
    assert "QCursor" not in CACHE
    assert "QTimer" not in CACHE
    assert "presentation_tick" not in CACHE
    assert "setProperty(" not in CACHE


def test_launcher_wires_one_clean_presentation_pipeline() -> None:
    assert "from gui.presentation_clock import install_presentation_clock" in RUN
    assert "from gui.ui_data_optimizations import install_ui_data_optimizations" in RUN
    assert "from gui.wallpaper_cache import install_preblur_cache" in RUN
    assert "install_ui_runtime_optimizations" not in RUN
    assert "install_background_pointer_hotpath" not in RUN
    assert "background_render_optimizations" not in RUN
    assert "ui_runtime_optimizations" not in RUN

    assert "card_fx = install_nekro_card_fx(window, visual)" in RUN
    assert "effects = install_nekro_effects(window, sakura_count=3)" in RUN
    assert "install_presentation_clock(" in RUN
    assert RUN.index("card_fx = install_nekro_card_fx(window, visual)") < RUN.index(
        "install_presentation_clock("
    )
    assert RUN.index("effects = install_nekro_effects(window, sakura_count=3)") < RUN.index(
        "install_presentation_clock("
    )


def test_presentation_sources_compile_without_importing_qt() -> None:
    for relative in (
        "gui/presentation_clock.py",
        "gui/native_background.py",
        "gui/native_visual_style.py",
        "gui/nekro_card_fx.py",
        "gui/nekro_effects.py",
        "gui/ui_data_optimizations.py",
        "gui/wallpaper_cache.py",
        "run_local_gui.py",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        compile(source, relative, "exec")
