from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TRANSITION = (ROOT / "gui" / "workspace_transition.py").read_text(encoding="utf-8")
TOGGLE = (ROOT / "gui" / "mode_toggle.py").read_text(encoding="utf-8")
RUNNER = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_workspace_transition_preserves_approved_fade_through_motion_tokens() -> None:
    assert "_PREPARE_MS = 30" in TRANSITION
    assert "_HOLD_MS = 40" in TRANSITION
    assert "_EXIT_END_MS = 155" in TRANSITION
    assert "_ENTER_START_MS = 175" in TRANSITION
    assert "_TOTAL_MS = 390" in TRANSITION
    assert "_VEIL_MAX_OPACITY = 0.06" in TRANSITION
    assert "_TRANSITION_MS = 300" in TOGGLE

    # Regression: do not replace the approved fade-through with the later curtain.
    assert "_CURTAIN_EDGE_PX" not in TRANSITION
    assert "_draw_outgoing_curtain" not in TRANSITION
    assert "_draw_incoming_curtain" not in TRANSITION


def test_transition_surface_remains_root_level_and_opaque_during_motion() -> None:
    assert "def __init__(self, root: QWidget)" in TRANSITION
    assert "super().__init__(root)" in TRANSITION
    assert "self._surface = _WorkspaceTransitionSurface(self.root)" in TRANSITION
    assert "top_left = self.stack.mapTo(self.root, QPoint(0, 0))" in TRANSITION
    assert "return QRect(top_left, self.stack.size())" in TRANSITION
    assert "CompositionMode_Source" in TRANSITION
    assert "self._surface.set_opaque_hint(True)" in TRANSITION
    assert "super().__init__(stack)" not in TRANSITION


def test_outgoing_frame_is_captured_before_card_fx_is_normalized() -> None:
    request = TRANSITION.split("def request_mode", 1)[1].split(
        "def _elapsed_ms", 1
    )[0]
    assert "outgoing = self._capture_composite()" in request
    assert "self._surface.begin(neutral, outgoing)" in request
    assert "self._surface.repaint()" in request
    assert "self._suspend_presentation()" in request
    assert request.index("outgoing = self._capture_composite()") < request.index(
        "self._suspend_presentation()"
    )


def test_current_widget_capture_uses_widget_grab_not_recursive_stack_render() -> None:
    assert "def _grab_current_page_widgets" in TRANSITION
    assert "page = self.stack.currentWidget()" in TRANSITION
    assert "page_pixmap = page.grab()" in TRANSITION
    assert "widget_frame = self._grab_current_page_widgets()" in TRANSITION
    assert "self.stack.render(" not in TRANSITION


def test_incoming_snapshot_waits_for_widget_and_quick_readiness() -> None:
    assert "_WIDGET_SETTLE_MS = 24" in TRANSITION
    assert "_QUICK_SYNC_TIMEOUT_MS = 64" in TRANSITION
    assert "self._widget_settle_timer" in TRANSITION
    assert "self._quick_sync_timeout" in TRANSITION
    assert "quick.frameSwapped.connect(" in TRANSITION
    assert "type=Qt.ConnectionType.QueuedConnection" in TRANSITION
    assert "@Slot()" in TRANSITION
    assert "def _prime_target_widget_tree" in TRANSITION
    assert "page.ensurePolished()" in TRANSITION
    assert "warm = page.grab()" in TRANSITION
    assert "self._surface.set_opaque_hint(False)" in TRANSITION
    assert "not self._quick_ready" in TRANSITION
    assert "or not self._widget_ready" in TRANSITION
    assert "def _maybe_capture_incoming" in TRANSITION


def test_two_cached_workspace_frames_are_never_readable_together() -> None:
    assert "if outgoing_alpha > 1e-4:" in TRANSITION
    assert "incoming_alpha = 0.0" in TRANSITION
    assert "painter.setOpacity(self._outgoing_alpha)" in TRANSITION
    assert "painter.setOpacity(self._incoming_alpha)" in TRANSITION


def test_final_snapshot_is_held_while_live_backing_store_is_primed() -> None:
    assert "_HANDOFF_SETTLE_MS = 34" in TRANSITION
    assert "def _begin_live_handoff" in TRANSITION
    handoff = TRANSITION.split("def _begin_live_handoff", 1)[1].split(
        "def _refresh_phase_copy_for_current_mode", 1
    )[0]
    assert "incoming_alpha=1.0" in handoff
    assert "self._surface.set_opaque_hint(False)" in handoff
    assert "self._prime_target_widget_tree()" in handoff
    assert "self._handoff_timer.start(_HANDOFF_SETTLE_MS)" in handoff
    assert "self._surface.hide()" not in handoff


def test_header_mode_copy_keeps_original_small_fade_through() -> None:
    assert "_HEADER_EXIT_START_MS = 45" in TRANSITION
    assert "_HEADER_EXIT_END_MS = 125" in TRANSITION
    assert "_HEADER_ENTER_START_MS = 150" in TRANSITION
    assert "_HEADER_ENTER_END_MS = 270" in TRANSITION
    assert "QGraphicsOpacityEffect" in TRANSITION


def test_transition_preserves_business_mode_state_machine_and_queueing() -> None:
    assert 'self._set_mode = getattr(window, "_set_workspace_mode", None)' in TRANSITION
    assert "QStackedWidget()" not in TRANSITION
    assert "BatchWorkspace" not in TRANSITION
    assert "self._queued_index = index" in TRANSITION
    assert "queued != int(self.stack.currentIndex())" in TRANSITION
    assert 'request = getattr(transition, "request_mode", None)' in TOGGLE


def test_transition_keeps_sakura_independent() -> None:
    assert 'effects = getattr(self.window, "_nekro_effects", None)' in TRANSITION
    assert "effects.raise_()" in TRANSITION
    assert "effects.timer.stop" not in TRANSITION


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
