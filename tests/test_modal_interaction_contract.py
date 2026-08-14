from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = (ROOT / "gui" / "static_modal_interaction.py").read_text(encoding="utf-8")
DETAILS = (ROOT / "gui" / "card_details_fast.py").read_text(encoding="utf-8")
REQUIRED = (ROOT / "gui" / "required_input_support.py").read_text(encoding="utf-8")
RUN = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def _body(source: str, start: str, end: str) -> str:
    return source.split(start, 1)[1].split(end, 1)[0]


def test_runtime_uses_one_qwidget_modal_adapter() -> None:
    assert "from gui.static_modal_interaction import install_static_modal_interaction" in RUN
    assert "install_static_modal_interaction(window, details)" in RUN
    assert "modal_overlay_zorder" not in RUN
    assert "install_modal_interaction" not in RUN


def test_modal_has_one_transition_surface_and_no_complex_widget_animation() -> None:
    assert "class _ModalTransitionSurface(QWidget):" in STATIC
    assert STATIC.count("_ModalTransitionSurface(self.root)") == 1
    assert "self.details.drawer.setGraphicsEffect(None)" in STATIC
    for forbidden in (
        "QGraphicsOpacityEffect",
        "QPropertyAnimation",
        "QParallelAnimationGroup",
        "drawer.grab(",
        "drawer.render(",
        "quick.grabWindow()",
    ):
        assert forbidden not in STATIC


def test_open_and_close_keep_existing_snapshot_semantics() -> None:
    prepare = _body(STATIC, "def _prepare_open_transition", "def _show_with_animation")
    assert "entry = self.details._capture_source()" in prepare
    assert "blurred = self.details._blur_pixmap(entry)" in prepare
    assert "final_modal = self._render_root_without_transition()" in prepare
    assert "self._transition.set_transition_frames(" in prepare

    close = _body(STATIC, "def _prepare_close_transition", "def request_close")
    assert "current_modal = self.details._capture_source()" in close
    assert "self._original_close()" in close
    assert "self._resume_underlay()" in close
    assert "self._transition.set_live_underlay_fade(current_modal, 1.0)" in close
    assert close.index("self._original_close()") < close.index("self._resume_underlay()")


def test_modal_hot_path_only_updates_transition_surface() -> None:
    setter = _body(STATIC, "def _set_progress", "def _advance_motion")
    assert "self._progress = value" in setter
    assert "self._transition.set_progress(value)" in setter
    assert "self.details" not in setter
    assert "_capture" not in setter

    advance = _body(STATIC, "def _advance_motion", "def _prepare_open_transition")
    for forbidden in ("_capture_source", "_render_root_without_transition", "_blur_pixmap"):
        assert forbidden not in advance


def test_modal_lifecycle_uses_one_shared_presentation_hold() -> None:
    suspend = _body(STATIC, "def _suspend_underlay", "def _resume_underlay")
    resume = _body(STATIC, "def _resume_underlay", "def _sync_modal_geometry")
    assert 'suspend_clock("modal")' in suspend
    assert 'resume_clock("modal")' in resume
    assert "suspend_for_modal" in suspend
    assert "resume_from_modal" in resume
    assert "activity_timer.stop()" in suspend
    assert "activity_timer.start()" in resume
    for obsolete in (
        "_pointer_timer",
        "effects_timer",
        "_quick_animation_was_running",
        "_pointer_timer_was_active",
        "_effects_timer_was_active",
    ):
        assert obsolete not in STATIC


def test_modal_timing_and_easing_are_preserved() -> None:
    assert "_OPEN_MS = 500" in STATIC
    assert "_CLOSE_MS = 300" in STATIC
    assert "QTimer(self)" in STATIC
    assert "Qt.TimerType.PreciseTimer" in STATIC
    assert "screen.refreshRate()" in STATIC
    assert "min(240.0, refresh_hz)" in STATIC
    assert "QPointF(0.25, 0.10)" in STATIC
    assert "QPointF(0.42, 0.00)" in STATIC


def test_resize_snaps_transition_instead_of_recapturing() -> None:
    snap = _body(STATIC, "def _snap_motion_for_resize", "def eventFilter")
    assert "self._stop_animation()" in snap
    assert "self._sync_modal_geometry()" in snap
    assert "self._transition.set_progress(target)" in snap
    assert "_capture" not in snap
    assert "_blur_pixmap" not in snap


def test_detail_start_action_still_uses_canonical_execution_request() -> None:
    settings = _body(DETAILS, "def open_real_settings", "def _clone_table_widget")
    assert 'getattr(self.window, "_request_real_execution", None)' in settings
    assert 'getattr(self.window, "_stop_real_execution", None)' in settings
    assert "start_source.click()" not in settings
    assert "stop_source.click()" not in settings


def test_required_input_support_still_owns_real_execution_preflight() -> None:
    assert "window._request_real_execution = self.request_start" in REQUIRED
    request = _body(REQUIRED, "def request_start", "def _on_start_clicked")
    assert "self._all_required_covered()" in request
    assert "self._merged_overrides()" in request
    assert "self._original_start()" in request


def test_modal_source_compiles_without_importing_pyside() -> None:
    compile(STATIC, "gui/static_modal_interaction.py", "exec")
