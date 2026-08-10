from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODAL = (ROOT / "gui" / "modal_interaction.py").read_text(encoding="utf-8")
CARD_FX = (ROOT / "gui" / "nekro_card_fx.py").read_text(encoding="utf-8")
NATIVE_BACKGROUND = (ROOT / "gui" / "native_background.py").read_text(encoding="utf-8")
RUNNER = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def _body(source: str, start: str, end: str) -> str:
    return source.split(start, 1)[1].split(end, 1)[0]


def test_gpu_modal_interaction_is_installed_for_shared_details() -> None:
    assert "from gui.modal_interaction import install_modal_interaction" in RUNNER
    assert "details = install_card_details(window)" in RUNNER
    assert "install_console_summary_mode(window)" in RUNNER
    assert "install_modal_interaction(window, details)" in RUNNER
    assert RUNNER.index("install_console_summary_mode(window)") < RUNNER.index(
        "install_modal_interaction(window, details)"
    )
    assert RUNNER.index("install_modal_interaction(window, details)") < RUNNER.index("shell.show()")


def test_modal_motion_stays_in_quick_scene_graph() -> None:
    assert "QQmlComponent" in MODAL
    assert "QQuickItem" in MODAL
    assert "QQuickWindow" in MODAL
    assert "OpacityAnimator" in MODAL
    assert "YAnimator" in MODAL
    assert "ScaleAnimator" in MODAL
    assert "Easing.OutQuart" in MODAL
    assert "transitionFinished" in MODAL
    assert "QGraphicsOpacityEffect" not in MODAL
    assert "QPropertyAnimation" not in MODAL
    assert "QParallelAnimationGroup" not in MODAL
    assert "QQuickWidget" not in MODAL
    assert "import QtQuick.Effects" not in MODAL
    assert "MultiEffect" not in MODAL


def test_transition_overlay_is_primed_once_and_never_toggled_per_modal() -> None:
    assert "QTimer.singleShot(0, self._prime_transition_surface)" in MODAL
    assert 'surface.setObjectName("glassModalTransitionWindow")' in MODAL
    assert "Qt.WindowType.WindowDoesNotAcceptFocus" in MODAL
    assert "Qt.WindowType.WindowTransparentForInput" in MODAL
    assert "surface.setPersistentGraphics(True)" in MODAL
    assert "surface.setPersistentSceneGraph(True)" in MODAL

    ensure = _body(MODAL, "def _ensure_transition_surface", "def _rewire_close_inputs")
    assert "self.transition_window.show()" in ensure
    assert "self.transition_window.raise_()" in ensure

    issue = _body(MODAL, "def _issue_transition", "def _begin_pending_open")
    assert ".show()" not in issue
    assert ".hide()" not in issue
    assert ".raise_()" not in issue
    assert 'item.setProperty("active", True)' in issue

    deactivate = _body(MODAL, "def _deactivate_transition", "def _clear_snapshots")
    assert 'item.setProperty("active", False)' in deactivate
    assert ".hide()" not in deactivate
    assert "self.window.hide()" not in MODAL
    assert "self.window.show()" not in MODAL


def test_transition_does_not_duplicate_the_live_base_ui_snapshot() -> None:
    assert "property url baseUrl" not in MODAL
    assert "id: baseImage" not in MODAL
    assert "_base_snapshot" not in MODAL
    assert "_base_url" not in MODAL
    assert "modal_base" not in MODAL
    assert "property url blurUrl" in MODAL
    assert "property url panelUrl" in MODAL
    assert "modal_blur" in MODAL
    assert "modal_panel" in MODAL


def test_snapshot_files_are_two_slot_reused_instead_of_unbounded() -> None:
    assert "def _next_snapshot_slot" in MODAL
    assert "return self._snapshot_revision & 1" in MODAL
    assert 'return temp_dir / f"{stem}_{slot}{suffix}"' in MODAL
    assert "def _configure_open_assets" in MODAL
    assert "def _configure_close_assets" in MODAL


def test_native_glass_mask_is_not_invalidated_by_modal_parent_visibility() -> None:
    assert "QEvent.Type.Show" in NATIVE_BACKGROUND
    assert "QEvent.Type.Hide" in NATIVE_BACKGROUND
    assert "self.schedule_mask_update()" in NATIVE_BACKGROUND
    assert "self.window.hide()" not in MODAL
    assert "self.window.show()" not in MODAL


def test_open_waits_for_stable_underlay_before_capture() -> None:
    assert '_STATE_OPENING_PENDING = "opening-pending"' in MODAL
    assert "_UNDERLAY_SETTLE_FALLBACK_MS = 48" in MODAL
    assert "quick.frameSwapped.connect(" in MODAL
    assert "Qt.ConnectionType.QueuedConnection" in MODAL
    assert "quick.requestUpdate()" in MODAL

    entry = _body(MODAL, "def _show_modal_with_transition", "def request_close")
    assert entry.index("self._state = _STATE_OPENING_PENDING") < entry.index(
        "self._suspend_underlay()"
    )
    assert entry.index("self._suspend_underlay()") < entry.index(
        "self._wait_for_stable_underlay_frame()"
    )
    assert "_capture_raw_backdrop" not in entry

    begin = _body(MODAL, "def _begin_pending_open", "def _show_modal_with_transition")
    assert "raw = self._capture_raw_backdrop()" in begin
    assert "blurred = self.details._blur_pixmap(raw)" in begin
    assert "self._prepare_hidden_modal(ratio=ratio, blurred=blurred)" in begin
    assert "self.details.drawer.grab()" in begin
    assert "self._issue_transition(closing=False)" in begin


def test_underlay_card_feedback_and_parallax_are_frozen_for_modal_lifetime() -> None:
    suspend = _body(MODAL, "def _suspend_underlay", "def _resume_underlay")
    assert 'getattr(self.window, "_nekro_card_fx", None)' in suspend
    assert 'getattr(card_fx, "suspend_for_modal", None)' in suspend
    assert 'getattr(self.background, "_pointer_timer", None)' in suspend
    assert "timer.stop()" in suspend
    assert 'quick.setProperty("animationRunning", False)' in suspend

    resume = _body(MODAL, "def _resume_underlay", "def _disconnect_underlay_frame_wait")
    assert 'getattr(card_fx, "resume_from_modal", None)' in resume
    assert "self.background._last_pointer_norm = None" in resume
    assert "timer.start()" in resume

    assert "def suspend_for_modal" in CARD_FX
    assert "def resume_from_modal" in CARD_FX
    assert "state.freeze()" in CARD_FX
    freeze = _body(CARD_FX, "def freeze(self) -> None:", "class NekroCardInteractionController")
    assert "self.target_alpha = self.current_alpha" in freeze
    assert "overlay_alpha=self.current_alpha" in freeze
    assert "_NORMAL_ALPHA" not in freeze


def test_real_modal_is_prepared_hidden_before_open_animation() -> None:
    prepare = _body(MODAL, "def _prepare_hidden_modal", "def _reveal_prepared_modal")
    assert "self.details.backdrop.setPixmap(blurred)" in prepare
    assert "self.details.backdrop.hide()" in prepare
    assert "self.details.scrim.hide()" in prepare
    assert "self.details.drawer.hide()" in prepare
    assert "self.details.body_layout.activate()" in prepare


def test_open_handoff_repaints_real_widget_before_quick_overlay_clears() -> None:
    finish = _body(MODAL, "def _on_transition_finished", "def eventFilter")
    open_branch = finish.split("if opened:", 1)[1].split(
        "if self._state != _STATE_CLOSING:", 1
    )[0]
    assert open_branch.index("self._reveal_prepared_modal()") < open_branch.index(
        "self.root.repaint()"
    )
    assert open_branch.index("self.root.repaint()") < open_branch.index(
        "self._deactivate_transition()"
    )
    assert "self._resume_underlay()" not in open_branch


def test_close_handoff_repaints_base_before_quick_overlay_clears_and_motion_resumes() -> None:
    finish = _body(MODAL, "def _on_transition_finished", "def eventFilter")
    close_branch = finish.split("if self._state != _STATE_CLOSING:", 1)[1]
    assert close_branch.index("self.details.close()") < close_branch.index(
        "self.root.repaint()"
    )
    assert close_branch.index("self.root.repaint()") < close_branch.index(
        "self._deactivate_transition()"
    )
    assert close_branch.index("self._deactivate_transition()") < close_branch.index(
        "self._resume_underlay()"
    )


def test_quick_and_widget_scrim_rgba_match_exactly() -> None:
    assert "color: Qt.rgba(12 / 255.0, 17 / 255.0, 26 / 255.0, 94 / 255.0)" in MODAL
    assert "#67101822" not in MODAL


def test_modal_no_longer_overrides_backdrop_capture_private_method() -> None:
    assert "self.details._capture_backdrop =" not in MODAL
    assert "self._original_capture_backdrop" not in MODAL
    assert "def _capture_raw_backdrop" in MODAL


def test_quick_failure_falls_back_to_atomic_widget_modal() -> None:
    assert "if not self._ensure_transition_surface():" in MODAL
    assert "self._fallback_open()" in MODAL
    assert "self._fallback_closed()" in MODAL
    assert "def _abort_open" in MODAL
    assert "self._original_show_prepared_modal(ratio=ratio)" in MODAL
    assert 'raise RuntimeError("Glass modal transition' not in MODAL


def test_modal_state_machine_has_no_drawer_show_reentry_driver() -> None:
    for state in (
        "_STATE_CLOSED",
        "_STATE_OPENING_PENDING",
        "_STATE_OPENING",
        "_STATE_OPEN",
        "_STATE_CLOSING",
    ):
        assert state in MODAL
    assert "self.details.drawer.installEventFilter(self)" not in MODAL
    filter_body = _body(MODAL, "def eventFilter", "def cleanup")
    assert "QEvent.Type.Show" not in filter_body
    assert "self.details.drawer" not in filter_body


def test_open_close_motion_remains_short_and_subtle() -> None:
    assert "_OPEN_MS = 235" in MODAL
    assert "_CLOSE_MS = 165" in MODAL
    assert "_PANEL_RISE_PX = 18" in MODAL
    assert "_PANEL_CLOSE_DROP_PX = 12" in MODAL
    assert "_PANEL_OPEN_SCALE = 0.985" in MODAL
    assert "_PANEL_CLOSE_SCALE = 0.990" in MODAL
    assert "function prepareOpen()" in MODAL
    assert "function prepareClose()" in MODAL


def test_passive_card_text_is_clickable_without_global_event_filter() -> None:
    assert "def _label_is_passive" in MODAL
    assert "Qt.TextInteractionFlag.TextSelectableByMouse" in MODAL
    assert "Qt.TextInteractionFlag.LinksAccessibleByMouse" in MODAL
    assert "WA_TransparentForMouseEvents" in MODAL
    assert "QApplication.instance().installEventFilter" not in MODAL


def test_card_hover_stays_event_driven() -> None:
    assert "_ANIMATION_FRAME_MS = 8" in CARD_FX
    assert "_POINTER_SAMPLE_MS" not in CARD_FX
    assert "_sample_timer" not in CARD_FX
    assert "QCursor" not in CARD_FX
    assert "QApplication" not in CARD_FX
    assert "frame.installEventFilter(self)" in CARD_FX
    assert "QEvent.Type.Enter" in CARD_FX
    assert "QEvent.Type.Leave" in CARD_FX
    assert "QEvent.Type.MouseButtonPress" in CARD_FX
    assert "QEvent.Type.MouseButtonRelease" in CARD_FX


def test_modal_sources_compile_without_importing_pyside() -> None:
    compile(MODAL, str(ROOT / "gui" / "modal_interaction.py"), "exec")
    compile(CARD_FX, str(ROOT / "gui" / "nekro_card_fx.py"), "exec")
