from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TRANSITION = (ROOT / "gui" / "workspace_transition.py").read_text(encoding="utf-8")
TOGGLE = (ROOT / "gui" / "mode_toggle.py").read_text(encoding="utf-8")
RUNNER = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_workspace_transition_matches_switch_total_timing_and_direction() -> None:
    assert "_PREPARE_MS = 30" in TRANSITION
    assert "_TRANSITION_MS = 270" in TRANSITION
    assert "_SLIDE_PX = 18.0" in TRANSITION
    assert "_TRANSITION_MS = 300" in TOGGLE
    assert "self._direction = 1 if index > current else -1" in TRANSITION
    assert "incoming_x = self._direction * _SLIDE_PX * (1.0 - self._progress)" in TRANSITION


def test_workspace_transition_animates_cached_frames_not_live_widget_trees() -> None:
    assert "class _WorkspaceTransitionSurface(QWidget)" in TRANSITION
    assert "self._from = QPixmap()" in TRANSITION
    assert "self._to = QPixmap()" in TRANSITION
    assert "self.stack.render(" in TRANSITION
    assert "quick.grabWindow()" in TRANSITION
    assert "def _capture_composite" in TRANSITION
    assert "QGraphicsOpacityEffect" not in TRANSITION
    assert "QPropertyAnimation" not in TRANSITION
    assert ".move(" not in TRANSITION


def test_target_workspace_is_prepared_behind_the_held_old_frame() -> None:
    assert "self._surface.set_hold_frame(outgoing, self._direction)" in TRANSITION
    assert "self._surface.repaint()" in TRANSITION
    assert "self._set_mode(index)" in TRANSITION
    assert "page_layout.activate()" in TRANSITION
    assert "QTimer.singleShot(_PREPARE_MS, self._prepare_incoming)" in TRANSITION
    assert 'flush_geometry = getattr(self.background, "_flush_geometry", None)' in TRANSITION
    assert "self._surface.set_transition_frames(" in TRANSITION


def test_transition_is_frame_rate_independent_and_refresh_rate_aware() -> None:
    assert "time.perf_counter()" in TRANSITION
    assert "screen.refreshRate()" in TRANSITION
    assert "Qt.TimerType.PreciseTimer" in TRANSITION
    assert "elapsed_s / max(0.001, _TRANSITION_MS / 1000.0)" in TRANSITION
    assert "QEasingCurve.Type.BezierSpline" in TRANSITION
    assert "QPointF(0.22, 1.0)" in TRANSITION
    assert "QPointF(0.36, 1.0)" in TRANSITION


def test_transition_preserves_business_mode_state_machine_and_handles_repeated_clicks() -> None:
    assert 'self._set_mode = getattr(window, "_set_workspace_mode", None)' in TRANSITION
    assert "QStackedWidget()" not in TRANSITION
    assert "BatchWorkspace" not in TRANSITION
    assert "self._queued_index = index" in TRANSITION
    assert "queued != int(self.stack.currentIndex())" in TRANSITION
    assert 'request = getattr(transition, "request_mode", None)' in TOGGLE


def test_transition_suspends_only_hot_presentation_paths_during_motion() -> None:
    assert 'suspend_cards = getattr(card_fx, "suspend_for_modal", None)' in TRANSITION
    assert 'pointer_timer = getattr(self.background, "_pointer_timer", None)' in TRANSITION
    assert 'quick.setProperty("animationRunning", False)' in TRANSITION
    assert 'resume_cards = getattr(card_fx, "resume_from_modal", None)' in TRANSITION
    assert 'self.background._last_pointer_norm = None' in TRANSITION


def test_formal_runner_installs_transition_after_interaction_controllers_exist() -> None:
    assert "from gui.workspace_transition import install_workspace_transition" in RUNNER
    assert "install_workspace_transition(window, visual)" in RUNNER
    assert RUNNER.index("install_nekro_card_fx(window, visual)") < RUNNER.index(
        "install_workspace_transition(window, visual)"
    )
    assert RUNNER.index("install_nekro_effects(window, sakura_count=3)") < RUNNER.index(
        "install_workspace_transition(window, visual)"
    )
    assert RUNNER.index("install_workspace_transition(window, visual)") < RUNNER.index("shell.show()")


def test_workspace_transition_source_compiles_without_importing_pyside() -> None:
    compile(TRANSITION, str(ROOT / "gui" / "workspace_transition.py"), "exec")
