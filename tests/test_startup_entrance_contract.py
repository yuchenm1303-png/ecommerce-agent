from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "gui" / "startup_entrance.py").read_text(encoding="utf-8")
STABILITY = (ROOT / "gui" / "startup_entrance_stability.py").read_text(encoding="utf-8")
RUN = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_reference_entrance_keeps_curtain_camera_choreography_without_loader() -> None:
    for token in (
        "_UI_FADE_MS = 300",
        "_CURTAIN_DELAY_MS = 0",
        "_CURTAIN_MS = 500",
        "_BACKGROUND_DELAY_MS = 150",
        "_BACKGROUND_MS = 800",
        "_UI_SCALE_DELAY_MS = 200",
        "_UI_SCALE_MS = 650",
        "_TOTAL_MS = 1000",
        "_BG_START_SCALE = 1.60",
        "_UI_START_SCALE = 1.20",
        "_CURTAIN_FRACTION = 0.51",
        'QColor("#333333")',
    ):
        assert token in SOURCE

    for obsolete in (
        "_HOLD_MIN_MS",
        "_HOLD_MAX_MS",
        "_LOADER_FADE_MS",
        "def _paint_loader",
        '"LOADING"',
        '"ecommerce-agent")',
        "random.randint",
        "_capture_and_hold",
    ):
        assert obsolete not in SOURCE


def test_reference_easing_curves_are_explicit() -> None:
    assert "_curve(0.645, 0.045, 0.355, 1.0)" in SOURCE
    assert "_curve(0.25, 0.46, 0.45, 0.94)" in SOURCE


def test_startup_is_one_snapshot_not_per_card_widget_animation() -> None:
    assert "central.render(" in SOURCE
    assert "self._ui_snapshot" in SOURCE
    assert "for record in self._glass_records:" in SOURCE
    assert "QPropertyAnimation" not in SOURCE
    assert "setGraphicsEffect" not in SOURCE


def test_capture_still_reveals_immediately_once_stable_snapshot_exists() -> None:
    assert "QTimer.singleShot(_CAPTURE_DELAY_MS, self._capture_and_reveal)" in SOURCE
    assert "self.overlay.begin_reveal()" in SOURCE
    assert "random" not in SOURCE


def test_startup_stability_gate_waits_for_maximized_layout_before_capture() -> None:
    assert "_LAYOUT_POLL_MS = 16" in STABILITY
    assert "_LAYOUT_STABLE_SAMPLES = 3" in STABILITY
    assert "_LAYOUT_SETTLE_TIMEOUT_MS = 240" in STABILITY
    assert "def _geometry_signature" in STABILITY
    assert "frame.mapTo(self.window, QPoint(0, 0))" in STABILITY
    assert "self._stable_samples >= _LAYOUT_STABLE_SAMPLES" in STABILITY
    assert "start = getattr(self.entrance, \"start\", None)" in STABILITY


def test_final_snapshot_to_live_handoff_flushes_native_quick_before_overlay_hides() -> None:
    assert "self.overlay.finished.disconnect(self.entrance._finish)" in STABILITY
    assert "_NATIVE_SETTLE_FRAMES = 2" in STABILITY
    assert "def _flush_native_background" in STABILITY
    assert 'geometry_timer = getattr(background, "_geometry_timer", None)' in STABILITY
    assert "geometry_timer.stop()" in STABILITY
    assert 'flush = getattr(background, "_flush_geometry", None)' in STABILITY
    assert 'quick.setProperty("animationRunning", False)' in STABILITY
    assert 'request_update = getattr(quick, "requestUpdate", None)' in STABILITY
    assert "self._prime_static_runtime()" in STABILITY
    assert "QTimer.singleShot(_HANDOFF_FRAME_MS, self._settle_live_runtime)" in STABILITY
    assert "self._flush_native_background()" in STABILITY
    assert "overlay.hide()" in STABILITY
    assert STABILITY.index("def _flush_native_background") < STABILITY.index("overlay.hide()")
    assert STABILITY.index("def _settle_live_runtime") < STABILITY.index("overlay.hide()")


def test_runtime_effects_resume_only_after_live_surface_is_visible() -> None:
    assert "QTimer.singleShot(_HANDOFF_FRAME_MS, self._resume_effects)" in STABILITY
    assert "QTimer.singleShot(_HANDOFF_FRAME_MS * 2, self._resume_card_fx)" in STABILITY
    assert "QTimer.singleShot(_HANDOFF_FRAME_MS * 3, self._resume_pointer)" in STABILITY
    assert STABILITY.index("overlay.hide()") < STABILITY.index("def _resume_card_fx")
    assert STABILITY.index("def _resume_card_fx") < STABILITY.index("def _resume_pointer")
    assert "hotpath._last_global = None" in STABILITY
    assert "hotpath._last_geometry = None" in STABILITY


def test_startup_freezes_runtime_until_staged_handoff_finishes() -> None:
    assert 'quick.setProperty("animationRunning", False)' in SOURCE
    assert 'quick.setProperty("offsetX", 0.0)' in SOURCE
    assert "suspend_for_modal" in SOURCE
    assert "pointer_timer.stop()" in SOURCE
    assert "resume_from_modal" in STABILITY
    assert "pointer_timer.start()" in STABILITY


def test_formal_launcher_covers_first_frame_then_uses_stability_gate_after_show() -> None:
    assert "from gui.startup_entrance import install_startup_entrance" in RUN
    assert "from gui.startup_entrance_stability import install_startup_entrance_stability" in RUN
    assert "entrance = install_startup_entrance(window, visual)" in RUN
    assert "entrance_stability = install_startup_entrance_stability(window, entrance)" in RUN
    assert "shell.show()" in RUN
    assert "entrance.raise_overlay()" in RUN
    assert "entrance_stability.start()" in RUN
    assert RUN.index("entrance = install_startup_entrance(window, visual)") < RUN.index("shell.show()")
    assert RUN.index("shell.show()") < RUN.index("entrance_stability.start()")
    assert "entrance.start()" not in RUN


def test_startup_sources_compile_without_importing_pyside() -> None:
    compile(SOURCE, str(ROOT / "gui" / "startup_entrance.py"), "exec")
    compile(STABILITY, str(ROOT / "gui" / "startup_entrance_stability.py"), "exec")
