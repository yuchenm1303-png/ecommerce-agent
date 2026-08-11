from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TRANSITION = (ROOT / "gui" / "workspace_transition.py").read_text(encoding="utf-8")
TOGGLE = (ROOT / "gui" / "mode_toggle.py").read_text(encoding="utf-8")
RUNNER = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_workspace_transition_uses_asymmetric_top_level_fade_through_timing() -> None:
    assert "_PREPARE_MS = 30" in TRANSITION
    assert "_HOLD_MS = 40" in TRANSITION
    assert "_EXIT_END_MS = 155" in TRANSITION
    assert "_ENTER_START_MS = 175" in TRANSITION
    assert "_TOTAL_MS = 390" in TRANSITION
    assert "_TRANSITION_MS = 300" in TOGGLE
    assert "_SLIDE_PX" not in TRANSITION
    assert "incoming_x" not in TRANSITION
    assert ".move(" not in TRANSITION


def test_two_readable_workspace_frames_are_never_cross_faded_together() -> None:
    assert "self._neutral = QPixmap()" in TRANSITION
    assert "self._outgoing = QPixmap()" in TRANSITION
    assert "self._incoming = QPixmap()" in TRANSITION
    assert "if outgoing_alpha > 1e-4:" in TRANSITION
    assert "incoming_alpha = 0.0" in TRANSITION
    assert "_EXIT_END_MS = 155" in TRANSITION
    assert "_ENTER_START_MS = 175" in TRANSITION
    assert "painter.setOpacity(self._outgoing_alpha)" in TRANSITION
    assert "painter.setOpacity(self._incoming_alpha)" in TRANSITION


def test_handoff_uses_clean_fuji_background_and_subtle_static_veil() -> None:
    assert "from .native_background import _OVERSCAN" in TRANSITION
    assert "def _capture_neutral_background" in TRANSITION
    assert 'quick.property("imageX")' in TRANSITION
    assert 'quick.property("imageY")' in TRANSITION
    assert "_VEIL_MAX_OPACITY = 0.06" in TRANSITION
    assert "_VEIL_START_MS = 135" in TRANSITION
    assert "_VEIL_PEAK_MS = 170" in TRANSITION
    assert "_VEIL_END_MS = 220" in TRANSITION
    assert "QGraphicsBlurEffect" not in TRANSITION


def test_workspace_transition_animates_cached_frames_not_live_widget_trees() -> None:
    assert "class _WorkspaceTransitionSurface(QWidget)" in TRANSITION
    assert "self.stack.render(" in TRANSITION
    assert "quick.grabWindow()" in TRANSITION
    assert "def _capture_composite" in TRANSITION
    assert "self.stack.setGraphicsEffect" not in TRANSITION
    assert "QPropertyAnimation" not in TRANSITION
    assert ".move(" not in TRANSITION
    assert ".resize(" not in TRANSITION


def test_target_workspace_is_prepared_behind_opaque_old_frame() -> None:
    assert "self._surface.begin(neutral, outgoing)" in TRANSITION
    assert "self._surface.repaint()" in TRANSITION
    assert "self._set_mode(index)" in TRANSITION
    assert "page_layout.activate()" in TRANSITION
    assert "QTimer.singleShot(_PREPARE_MS, self._prepare_incoming)" in TRANSITION
    assert 'flush_geometry = getattr(self.background, "_flush_geometry", None)' in TRANSITION
    assert "self._surface.set_incoming(incoming)" in TRANSITION


def test_transition_is_frame_rate_independent_and_uses_separate_exit_enter_easing() -> None:
    assert "time.perf_counter()" in TRANSITION
    assert "screen.refreshRate()" in TRANSITION
    assert "Qt.TimerType.PreciseTimer" in TRANSITION
    assert "def _exit_easing()" in TRANSITION
    assert "0.40, 0.00, 1.00, 1.00" in TRANSITION
    assert "def _enter_easing()" in TRANSITION
    assert "0.16, 1.00, 0.30, 1.00" in TRANSITION
    assert "_incoming_enter_start_ms" in TRANSITION


def test_header_mode_copy_uses_small_independent_fade_through_only() -> None:
    assert 'self._phase_badge = getattr(window, "phase_badge", None)' in TRANSITION
    assert "QGraphicsOpacityEffect" in TRANSITION
    assert "_HEADER_EXIT_START_MS = 45" in TRANSITION
    assert "_HEADER_EXIT_END_MS = 125" in TRANSITION
    assert "_HEADER_ENTER_START_MS = 150" in TRANSITION
    assert "_HEADER_ENTER_END_MS = 270" in TRANSITION
    assert "badge.setText(self._phase_new_text)" in TRANSITION
    assert "self.stack.setGraphicsEffect" not in TRANSITION


def test_transition_preserves_business_mode_state_machine_and_handles_repeated_clicks() -> None:
    assert 'self._set_mode = getattr(window, "_set_workspace_mode", None)' in TRANSITION
    assert "QStackedWidget()" not in TRANSITION
    assert "BatchWorkspace" not in TRANSITION
    assert "self._queued_index = index" in TRANSITION
    assert "queued != int(self.stack.currentIndex())" in TRANSITION
    assert 'request = getattr(transition, "request_mode", None)' in TOGGLE


def test_transition_suspends_only_hot_presentation_paths_but_leaves_sakura_independent() -> None:
    assert 'suspend_cards = getattr(card_fx, "suspend_for_modal", None)' in TRANSITION
    assert 'pointer_timer = getattr(self.background, "_pointer_timer", None)' in TRANSITION
    assert 'quick.setProperty("animationRunning", False)' in TRANSITION
    assert 'resume_cards = getattr(card_fx, "resume_from_modal", None)' in TRANSITION
    assert "self.background._last_pointer_norm = None" in TRANSITION
    assert "_nekro_effects" not in TRANSITION


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
