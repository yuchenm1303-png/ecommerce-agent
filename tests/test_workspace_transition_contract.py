from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TRANSITION = (ROOT / "gui" / "workspace_transition.py").read_text(encoding="utf-8")
SNAPSHOT = (ROOT / "gui" / "workspace_transition_snapshot.py").read_text(encoding="utf-8")
TOGGLE = (ROOT / "gui" / "mode_toggle.py").read_text(encoding="utf-8")
RUNNER = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_workspace_transition_keeps_original_fade_through_timing() -> None:
    assert "_HOLD_MS = 40" in TRANSITION
    assert "_EXIT_END_MS = 155" in TRANSITION
    assert "_ENTER_START_MS = 175" in TRANSITION
    assert "_TOTAL_MS = 390" in TRANSITION
    assert "_TRANSITION_MS = 300" in TOGGLE
    assert "_SLIDE_PX" not in TRANSITION
    assert "incoming_x" not in TRANSITION


def test_transition_surface_is_root_level_and_input_blocking() -> None:
    assert "def __init__(self, root: QWidget)" in TRANSITION
    assert "super().__init__(root)" in TRANSITION
    assert "self._surface = _WorkspaceTransitionSurface(self.root)" in TRANSITION
    assert "WA_TransparentForMouseEvents, False" in TRANSITION
    assert "top_left = self.stack.mapTo(self.root, QPoint(0, 0))" in TRANSITION
    assert "return QRect(top_left, self.stack.size())" in TRANSITION


def test_transition_never_renders_a_second_workspace_ui() -> None:
    assert "class WorkspaceTransitionSnapshotRenderer" in SNAPSHOT
    assert "def capture_neutral" in SNAPSHOT

    # No transition-only capture/rebuild of cards, controls, or live Quick glass.
    assert "page.render(" not in SNAPSHOT
    assert "quick.grabWindow()" not in SNAPSHOT
    assert "def _capture_composite" not in SNAPSHOT
    assert "def _paint_glass" not in SNAPSHOT
    assert "def _card_geometry" not in SNAPSHOT
    assert "visual._glass" not in SNAPSHOT
    assert "QPainterPath" not in SNAPSHOT

    # The transition surface owns only one neutral backdrop plus the subtle veil.
    assert "self._backdrop = QPixmap()" in TRANSITION
    assert "self._outgoing" not in TRANSITION
    assert "self._incoming" not in TRANSITION
    assert "painter.drawPixmap(0, 0, self._backdrop)" in TRANSITION


def test_mode_click_performs_zero_layout_or_snapshot_preparation() -> None:
    request = TOGGLE.split("def request_mode", 1)[1].split("toggle.clicked.connect", 1)[0]
    assert 'request = getattr(transition, "request_mode", None)' in request
    assert "request(target)" in request
    assert "prepare_page" not in request
    assert "prime_live" not in request
    assert "snapshot_renderer" not in request


def test_real_workspace_mutation_occurs_only_after_opaque_cover() -> None:
    switch = TRANSITION.split("def _switch_under_cover", 1)[1].split("@Slot()", 1)[0]
    assert "self._surface.set_mix(backdrop_alpha=1.0" in switch
    assert "self._surface.repaint()" in switch
    assert "self._suspend_presentation()" in switch
    assert "self._prepare_target_under_cover()" in switch
    assert "self._set_mode(self._target_index)" in switch

    assert switch.index("self._surface.repaint()") < switch.index("self._suspend_presentation()")
    assert switch.index("self._surface.repaint()") < switch.index("self._prepare_target_under_cover()")
    assert switch.index("self._surface.repaint()") < switch.index("self._set_mode(self._target_index)")


def test_request_mode_does_not_mutate_live_workspace_before_animation() -> None:
    request = TRANSITION.split("def request_mode", 1)[1].split("def _elapsed_ms", 1)[0]
    assert "backdrop = self._snapshot_renderer.capture_neutral()" in request
    assert "self._surface.begin(backdrop)" in request
    assert "self._surface.show()" in request
    assert "self._surface.repaint()" in request
    assert "self._timer.start()" in request
    assert "self._set_mode(index)" not in request.split("self._surface.show()", 1)[1]
    assert "_suspend_presentation()" not in request
    assert "prepare_page" not in request


def test_target_page_is_prepared_only_under_cover() -> None:
    assert 'layout_keeper = getattr(self.window, "_workspace_layout_commit", None)' in TRANSITION
    assert 'prepare_page = getattr(layout_keeper, "prepare_page", None)' in TRANSITION
    assert "prepare_page(self._target_index)" in TRANSITION
    assert "install_workspace_layout_commit(window)" in TOGGLE


def test_reveal_waits_for_presented_quick_target_frame() -> None:
    assert "_QUICK_SYNC_TIMEOUT_MS = 64" in TRANSITION
    assert "quick.frameSwapped.connect(" in TRANSITION
    assert "type=Qt.ConnectionType.QueuedConnection" in TRANSITION
    assert "self._awaiting_quick_frame = True" in TRANSITION
    assert "quick.update()" in TRANSITION
    assert "def _mark_reveal_ready" in TRANSITION
    assert "self._reveal_start_ms = max(float(_ENTER_START_MS), self._elapsed_ms())" in TRANSITION


def test_live_presentation_resumes_before_target_reveal() -> None:
    ready = TRANSITION.split("def _mark_reveal_ready", 1)[1].split("def _sync_toggle_to_stack", 1)[0]
    assert "self._resume_presentation()" in ready
    assert "self._surface.repaint()" in ready


def test_fuji_backdrop_tracks_live_parallax_without_painting_cards() -> None:
    assert "from .native_background import _OVERSCAN" in SNAPSHOT
    assert 'quick.property("imageX")' in SNAPSHOT
    assert 'quick.property("imageY")' in SNAPSHOT
    assert "_VEIL_MAX_OPACITY = 0.06" in TRANSITION
    assert "_VEIL_START_MS = 135" in TRANSITION
    assert "_VEIL_PEAK_MS = 170" in TRANSITION
    assert "_VEIL_END_MS = 220" in TRANSITION


def test_transition_preserves_business_mode_state_machine_and_repeated_clicks() -> None:
    assert 'self._set_mode = getattr(window, "_set_workspace_mode", None)' in TRANSITION
    assert "QStackedWidget()" not in TRANSITION
    assert "BatchWorkspace" not in TRANSITION
    assert "self._queued_index = index" in TRANSITION
    assert "queued != int(self.stack.currentIndex())" in TRANSITION
    assert 'request = getattr(transition, "request_mode", None)' in TOGGLE


def test_transition_keeps_sakura_above_the_workspace_cover() -> None:
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


def test_workspace_transition_sources_compile_without_importing_pyside() -> None:
    compile(TRANSITION, str(ROOT / "gui" / "workspace_transition.py"), "exec")
    compile(SNAPSHOT, str(ROOT / "gui" / "workspace_transition_snapshot.py"), "exec")
    compile(TOGGLE, str(ROOT / "gui" / "mode_toggle.py"), "exec")
