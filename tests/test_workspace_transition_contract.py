from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TRANSITION = (ROOT / "gui" / "workspace_transition.py").read_text(encoding="utf-8")
TOGGLE = (ROOT / "gui" / "mode_toggle.py").read_text(encoding="utf-8")
RUNNER = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_workspace_transition_uses_large_surface_timing_without_lateral_motion() -> None:
    assert "_PREPARE_MS = 30" in TRANSITION
    assert "_HOLD_MS = 44" in TRANSITION
    assert "_EXIT_END_MS = 178" in TRANSITION
    assert "_ENTER_START_MS = 208" in TRANSITION
    assert "_TOTAL_MS = 438" in TRANSITION
    assert "_TRANSITION_MS = 300" in TOGGLE
    assert "_SLIDE_PX" not in TRANSITION
    assert "incoming_x" not in TRANSITION
    assert ".move(" not in TRANSITION


def test_transition_surface_is_root_level_so_stack_pages_cannot_overtake_it() -> None:
    assert "def __init__(self, root: QWidget)" in TRANSITION
    assert "super().__init__(root)" in TRANSITION
    assert "self._surface = _WorkspaceTransitionSurface(self.root)" in TRANSITION
    assert "top_left = self.stack.mapTo(self.root, QPoint(0, 0))" in TRANSITION
    assert "return QRect(top_left, self.stack.size())" in TRANSITION
    assert "self._set_mode(index)" in TRANSITION
    assert "self._raise_transition_surface()" in TRANSITION
    assert "super().__init__(stack)" not in TRANSITION
    assert "_WorkspaceTransitionSurface(self.stack)" not in TRANSITION


def test_snapshot_capture_renders_only_the_current_workspace_page() -> None:
    assert "def _render_current_page" in TRANSITION
    assert "page = self.stack.currentWidget()" in TRANSITION
    assert "target_offset = page.mapTo(self.stack, QPoint(0, 0))" in TRANSITION
    assert "page.render(" in TRANSITION
    assert "widget_frame = self._render_current_page()" in TRANSITION
    assert "self.stack.render(" not in TRANSITION


def test_readable_workspace_pixels_are_not_full_frame_alpha_animated() -> None:
    assert "_CURTAIN_EDGE_PX = 32.0" in TRANSITION
    assert "_CURTAIN_EDGE_STEPS = 6" in TRANSITION
    assert "def _draw_outgoing_curtain" in TRANSITION
    assert "def _draw_incoming_curtain" in TRANSITION
    assert "self._outgoing_visible" in TRANSITION
    assert "self._incoming_visible" in TRANSITION

    # Regression: full-frame opacity made translucent editors, table viewports and
    # small labels pulse in luminance even when there was no page overlap.
    assert "self._outgoing_alpha" not in TRANSITION
    assert "self._incoming_alpha" not in TRANSITION
    assert "painter.setOpacity(self._outgoing_alpha)" not in TRANSITION
    assert "painter.setOpacity(self._incoming_alpha)" not in TRANSITION


def test_soft_opacity_is_confined_to_the_narrow_curtain_edge() -> None:
    clipped = TRANSITION.split("def _draw_clipped", 1)[1].split(
        "def _draw_outgoing_curtain", 1
    )[0]
    assert "painter.setClipRect(clip_rect)" in clipped
    assert "if opacity < 0.9999:" in clipped
    assert "painter.setOpacity(opacity)" in clipped

    outgoing = TRANSITION.split("def _draw_outgoing_curtain", 1)[1].split(
        "def _draw_incoming_curtain", 1
    )[0]
    incoming = TRANSITION.split("def _draw_incoming_curtain", 1)[1].split(
        "def paintEvent", 1
    )[0]
    assert "_CURTAIN_EDGE_PX" in outgoing
    assert "_CURTAIN_EDGE_STEPS" in outgoing
    assert "_CURTAIN_EDGE_PX" in incoming
    assert "_CURTAIN_EDGE_STEPS" in incoming


def test_two_readable_workspace_fields_are_never_revealed_together() -> None:
    assert "if outgoing_visible > 1e-4:" in TRANSITION
    assert "incoming_visible = 0.0" in TRANSITION
    assert "_EXIT_END_MS = 178" in TRANSITION
    assert "_ENTER_START_MS = 208" in TRANSITION


def test_neutral_veil_is_painted_under_readable_content() -> None:
    assert "_VEIL_MAX_OPACITY = 0.045" in TRANSITION
    assert "_VEIL_START_MS = 158" in TRANSITION
    assert "_VEIL_PEAK_MS = 194" in TRANSITION
    assert "_VEIL_END_MS = 232" in TRANSITION
    paint = TRANSITION.split("def paintEvent", 1)[1].split(
        "class WorkspaceTransitionController", 1
    )[0]
    assert paint.index("painter.fillRect(self.rect(), _VEIL_COLOR)") < paint.index(
        "self._draw_outgoing_curtain(painter)"
    )
    assert paint.index("painter.fillRect(self.rect(), _VEIL_COLOR)") < paint.index(
        "self._draw_incoming_curtain(painter)"
    )
    assert "QGraphicsBlurEffect" not in TRANSITION


def test_root_surface_owns_opaque_pixels_and_blocks_hidden_workspace_input() -> None:
    assert "WA_TransparentForMouseEvents, False" in TRANSITION
    assert "WA_OpaquePaintEvent, True" in TRANSITION
    assert "CompositionMode_Source" in TRANSITION
    assert "single opaque backing image" in TRANSITION


def test_incoming_capture_waits_for_a_presented_quick_frame() -> None:
    assert "_QUICK_SYNC_TIMEOUT_MS = 64" in TRANSITION
    assert "quick.frameSwapped.connect(" in TRANSITION
    assert "type=Qt.ConnectionType.QueuedConnection" in TRANSITION
    assert "@Slot()" in TRANSITION
    assert "def _on_quick_frame_swapped" in TRANSITION
    assert "self._awaiting_quick_frame = True" in TRANSITION
    assert "quick.update()" in TRANSITION
    assert "def _capture_incoming_after_quick_sync" in TRANSITION

    prepare = TRANSITION.split("def _prepare_incoming", 1)[1].split("@Slot()", 1)[0]
    assert "self._capture_composite()" not in prepare


def test_final_handoff_prewarms_live_widget_backing_store_under_snapshot() -> None:
    assert "_HANDOFF_SETTLE_MS = 34" in TRANSITION
    assert "def prepare_live_handoff" in TRANSITION
    assert "WA_OpaquePaintEvent, False" in TRANSITION
    assert "def _prime_live_page_backing_store" in TRANSITION
    assert "for child in page.findChildren(QWidget):" in TRANSITION
    assert "child.update()" in TRANSITION
    assert "self.root.update(self._surface.geometry())" in TRANSITION
    assert "def _begin_live_handoff" in TRANSITION
    assert "self._surface.prepare_live_handoff()" in TRANSITION
    assert "self._handoff_timer.start(_HANDOFF_SETTLE_MS)" in TRANSITION


def test_handoff_uses_clean_fuji_background() -> None:
    assert "from .native_background import _OVERSCAN" in TRANSITION
    assert "def _capture_neutral_background" in TRANSITION
    assert 'quick.property("imageX")' in TRANSITION
    assert 'quick.property("imageY")' in TRANSITION


def test_workspace_transition_animates_cached_frames_not_live_widget_trees() -> None:
    assert "class _WorkspaceTransitionSurface(QWidget)" in TRANSITION
    assert "quick.grabWindow()" in TRANSITION
    assert "def _capture_composite" in TRANSITION
    assert "self.stack.setGraphicsEffect" not in TRANSITION
    assert "QPropertyAnimation" not in TRANSITION
    assert ".move(" not in TRANSITION
    assert ".resize(" not in TRANSITION


def test_header_mode_copy_uses_small_independent_fade_through_only() -> None:
    assert 'self._phase_badge = getattr(window, "phase_badge", None)' in TRANSITION
    assert "QGraphicsOpacityEffect" in TRANSITION
    assert "_HEADER_EXIT_START_MS = 50" in TRANSITION
    assert "_HEADER_EXIT_END_MS = 130" in TRANSITION
    assert "_HEADER_ENTER_START_MS = 155" in TRANSITION
    assert "_HEADER_ENTER_END_MS = 285" in TRANSITION
    assert "badge.setText(self._phase_new_text)" in TRANSITION
    assert "self.stack.setGraphicsEffect" not in TRANSITION


def test_transition_preserves_business_mode_state_machine_and_handles_repeated_clicks() -> None:
    assert 'self._set_mode = getattr(window, "_set_workspace_mode", None)' in TRANSITION
    assert "QStackedWidget()" not in TRANSITION
    assert "BatchWorkspace" not in TRANSITION
    assert "self._queued_index = index" in TRANSITION
    assert "queued != int(self.stack.currentIndex())" in TRANSITION
    assert 'request = getattr(transition, "request_mode", None)' in TOGGLE


def test_transition_leaves_sakura_independent_and_above_workspace_surface() -> None:
    assert 'suspend_cards = getattr(card_fx, "suspend_for_modal", None)' in TRANSITION
    assert 'pointer_timer = getattr(self.background, "_pointer_timer", None)' in TRANSITION
    assert 'quick.setProperty("animationRunning", False)' in TRANSITION
    assert 'effects = getattr(self.window, "_nekro_effects", None)' in TRANSITION
    assert "effects.raise_()" in TRANSITION
    assert "effects.timer.stop" not in TRANSITION
    assert "_effects_timer_was_active" not in TRANSITION


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
