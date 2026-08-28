from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "gui" / "startup_entrance.py").read_text(encoding="utf-8")
STABILITY = (ROOT / "gui" / "startup_entrance_stability.py").read_text(encoding="utf-8")
STATIC_VIEW = (ROOT / "gui" / "static_qml_view.py").read_text(encoding="utf-8")
RUN = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_startup_entrance_is_geometry_only_curtain_animation() -> None:
    for token in (
        "_REVEAL_DELAY_MS = 48",
        "_CURTAIN_MS = 500",
        "_CURTAIN_FRACTION = 0.51",
        'QColor("#333333")',
        "_curve(0.645, 0.045, 0.355, 1.0)",
        "QVariantAnimation",
        "self._left_curtain.setGeometry",
        "self._right_curtain.setGeometry",
    ):
        assert token in SOURCE

    for forbidden in (
        "QPainter",
        "QPixmap",
        "SmoothPixmapTransform",
        "_FRAME_MS",
        "_sharp_scene",
        "_blur_scene",
        "_paint_background",
        "central.render(",
        "QGraphicsEffect",
    ):
        assert forbidden not in SOURCE


def test_curtain_completion_releases_input_before_handoff() -> None:
    assert "self.setFocusPolicy(Qt.FocusPolicy.NoFocus)" in SOURCE
    finish = SOURCE.split("def _on_animation_finished", 1)[1].split(
        "def _apply_progress", 1
    )[0]
    assert "WA_TransparentForMouseEvents" in finish
    assert finish.index("self.hide()") < finish.index("self.finished.emit()")


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
    assert "_LAYOUT_STABLE_SAMPLES = 5" in STABILITY
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


def test_heavy_quick_scene_work_is_prepared_before_visible_reveal() -> None:
    assert "revealPreparing = Signal()" in STABILITY
    assert "layoutInvalidated = Signal()" in STABILITY
    barrier = STABILITY.split("def _begin_reveal_frame_barrier", 1)[1].split(
        "def _disconnect_reveal_frame_barrier", 1
    )[0]
    assert barrier.index("self.revealPreparing.emit()") < barrier.index(
        "self._prime_live_runtime()"
    )

    assert "reveal_preparing.connect(self._prepare_startup_scene)" in STATIC_VIEW
    assert "layout_invalidated.connect(self._invalidate_startup_snapshot)" in STATIC_VIEW
    assert "self._ensure_scene_loaded()" in STATIC_VIEW
    assert "self.bridge.refresh()" in STATIC_VIEW
    assert "self._startup_snapshot_prepared = True" in STATIC_VIEW

    activate = STATIC_VIEW.split("def _activate_quick", 1)[1].split(
        "def _arm_handoff", 1
    )[0]
    assert "if not self._startup_snapshot_prepared:" in activate
    assert "self.bridge.refresh()" in activate


def test_reveal_has_frame_barrier_but_finished_overlay_has_no_liveness_dependency() -> None:
    assert "_REVEAL_SETTLE_FRAMES = 2" in STABILITY
    assert "quick.frameSwapped.connect(self._on_reveal_frame_swapped)" in STABILITY
    assert "quick.frameSwapped.disconnect(self._on_reveal_frame_swapped)" in STABILITY
    assert "self._reveal_frames_remaining = _REVEAL_SETTLE_FRAMES" in STABILITY

    for forbidden in (
        "_arm_native_frame_barrier",
        "_on_native_frame_swapped",
        "_native_frames_remaining",
        "_native_frame_quick",
    ):
        assert forbidden not in STABILITY

    stage = STABILITY.split("def _stage_finish", 1)[1].split(
        "def _commit_overlay_handoff", 1
    )[0]
    assert "QTimer.singleShot(0, self._commit_overlay_handoff)" in stage

    commit = STABILITY.split("def _commit_overlay_handoff", 1)[1].split(
        "def _resume_effects", 1
    )[0]
    assert commit.index("overlay.hide()") < commit.index("self.handoffReady.emit()")


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
    compile(STATIC_VIEW, "gui/static_qml_view.py", "exec")
