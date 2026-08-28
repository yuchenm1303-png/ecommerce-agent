from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "gui" / "startup_entrance.py").read_text(encoding="utf-8")
STABILITY = (ROOT / "gui" / "startup_entrance_stability.py").read_text(encoding="utf-8")
RUN = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_reference_entrance_visual_timing_is_preserved() -> None:
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
        "_curve(0.645, 0.045, 0.355, 1.0)",
        "_curve(0.25, 0.46, 0.45, 0.94)",
    ):
        assert token in SOURCE


def test_startup_uses_one_frozen_widget_snapshot() -> None:
    assert "central.render(" in SOURCE
    assert "self._ui_snapshot" in SOURCE
    assert "for record in self._glass_records:" in SOURCE
    assert "QPropertyAnimation" not in SOURCE
    assert "setGraphicsEffect" not in SOURCE
    assert "QTimer.singleShot(_CAPTURE_DELAY_MS, self._capture_and_reveal)" in SOURCE
    assert "self.overlay.begin_reveal()" in SOURCE


def test_startup_runtime_lifecycle_uses_shared_presentation_clock_only() -> None:
    freeze = SOURCE.split("def _freeze_runtime_presentation", 1)[1].split(
        "def raise_overlay", 1
    )[0]
    restore = SOURCE.split("def _restore_runtime_presentation", 1)[1].split(
        "def _finish", 1
    )[0]
    assert 'suspend_clock("startup")' in freeze
    assert 'resume_clock("startup")' in restore
    assert "suspend_for_modal" in freeze
    assert "resume_from_modal" in restore
    assert "_pointer_timer" not in SOURCE
    assert "_scroll_local_glass" not in SOURCE
    assert "_background_pointer_hotpath" not in SOURCE


def test_startup_stability_requires_live_paint_and_quiescent_layout() -> None:
    assert "_LAYOUT_POLL_MS = 16" in STABILITY
    assert "_LAYOUT_STABLE_SAMPLES = 4" in STABILITY
    assert "_LAYOUT_SETTLE_TIMEOUT_MS" not in STABILITY
    assert "self._layout_epoch" in STABILITY
    assert "event_type == QEvent.Type.Paint" in STABILITY
    assert "event_type in _LAYOUT_ACTIVITY_EVENTS" in STABILITY
    assert "self._live_paint_seen = False" in STABILITY
    assert "self._live_paint_seen and self._stable_samples >= _LAYOUT_STABLE_SAMPLES" in STABILITY
    assert "self.overlay.finished.disconnect(self.entrance._finish)" in STABILITY


def test_startup_overlay_does_not_occlusion_cull_live_widgets() -> None:
    assert "def _keep_live_surface_paintable" in STABILITY
    assert "self.overlay.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)" in STABILITY
    assert "central.repaint()" in STABILITY
    assert "frame.repaint()" in STABILITY


def test_startup_handoff_waits_for_rendered_quick_frames() -> None:
    assert "_NATIVE_SETTLE_FRAMES = 2" in STABILITY
    assert "def _arm_native_frame_barrier" in STABILITY
    assert "quick.frameSwapped.connect(self._on_native_frame_swapped)" in STABILITY
    assert "quick.frameSwapped.disconnect(self._on_native_frame_swapped)" in STABILITY
    assert "self._native_frames_remaining = _NATIVE_SETTLE_FRAMES" in STABILITY
    assert "def _on_native_frame_swapped" in STABILITY
    assert "QTimer.singleShot(_HANDOFF_FRAME_MS, self._settle_live_runtime)" not in STABILITY
    stage = STABILITY.split("def _stage_finish", 1)[1].split(
        "def _on_native_frame_swapped", 1
    )[0]
    assert stage.index("self._arm_native_frame_barrier()") < stage.index(
        "self._prime_static_runtime()"
    )
    assert "overlay.hide()" in STABILITY


def test_startup_resumes_shared_clock_only_after_overlay_handoff() -> None:
    assert "QTimer.singleShot(_HANDOFF_FRAME_MS, self._resume_effects)" in STABILITY
    assert "QTimer.singleShot(_HANDOFF_FRAME_MS * 2, self._resume_card_fx)" in STABILITY
    assert "QTimer.singleShot(_HANDOFF_FRAME_MS * 3, self._resume_presentation)" in STABILITY
    assert 'resume("startup")' in STABILITY
    assert "_pointer_timer" not in STABILITY
    assert "_background_pointer_hotpath" not in STABILITY
    assert "_scroll_local_glass" not in STABILITY


def test_formal_launcher_uses_stability_gate_after_native_show() -> None:
    assert "entrance = install_startup_entrance(window, visual)" in RUN
    assert "entrance_stability = install_startup_entrance_stability(window, entrance)" in RUN
    assert "shell.show()" in RUN
    assert "entrance.raise_overlay()" in RUN
    assert "entrance_stability.start()" in RUN
    assert RUN.index("entrance = install_startup_entrance(window, visual)") < RUN.index(
        "shell.show()"
    )
    assert RUN.index("shell.show()") < RUN.index("entrance_stability.start()")


def test_startup_sources_compile_without_importing_pyside() -> None:
    compile(SOURCE, "gui/startup_entrance.py", "exec")
    compile(STABILITY, "gui/startup_entrance_stability.py", "exec")
