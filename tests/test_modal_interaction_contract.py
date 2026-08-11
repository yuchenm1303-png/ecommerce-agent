from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = (ROOT / "gui" / "static_modal_interaction.py").read_text(encoding="utf-8")
DETAILS = (ROOT / "gui" / "card_details_fast.py").read_text(encoding="utf-8")
BASE_DETAILS = (ROOT / "gui" / "card_details.py").read_text(encoding="utf-8")
REQUIRED = (ROOT / "gui" / "required_input_support.py").read_text(encoding="utf-8")
RUNNER = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def _body(source: str, start: str, end: str) -> str:
    return source.split(start, 1)[1].split(end, 1)[0]


def test_runtime_uses_only_the_existing_qwidget_modal_adapter() -> None:
    assert "from gui.static_modal_interaction import install_static_modal_interaction" in RUNNER
    assert "install_static_modal_interaction(window, details)" in RUNNER
    assert "from gui.modal_interaction import install_modal_interaction" not in RUNNER
    assert "modal_overlay_zorder" not in RUNNER


def test_final_architecture_has_exactly_one_non_native_transition_surface() -> None:
    assert "class _ModalTransitionSurface(QWidget):" in STATIC
    assert 'self.setObjectName("cardDetailTransitionSurface")' in STATIC
    assert "WA_OpaquePaintEvent" in STATIC
    assert "WA_NoSystemBackground" in STATIC
    assert STATIC.count("_ModalTransitionSurface(self.root)") == 1

    forbidden = (
        "QQuickWindow(",
        "QQmlComponent",
        "QQuickItem",
        ".winId()",
        "_FrozenBackdrop",
        "_ModalTransitionCompositor",
        "cardDetailFadeLayer",
    )
    for token in forbidden:
        assert token not in STATIC


def test_complex_live_modal_widgets_never_receive_animation_effects_or_transforms() -> None:
    forbidden = (
        "QGraphicsOpacityEffect",
        "QGraphicsEffect",
        "self._drawer_effect",
        "QPropertyAnimation",
        "QParallelAnimationGroup",
        "painter.translate(",
        "painter.scale(",
        "_START_SCALE",
        "_OPEN_RISE_PX",
    )
    for token in forbidden:
        assert token not in STATIC
    assert "self.details.drawer.setGraphicsEffect(None)" in STATIC


def test_no_drawer_or_child_snapshot_pipeline_can_return() -> None:
    forbidden = (
        "drawer.render(",
        "drawer.grab(",
        "child.render(",
        "child.grab(",
        "_panel_frame",
        "_render_drawer_frame",
        "_capture_panel_offscreen",
        "_suppress_drawer_paint_for_capture",
        "DrawWindowBackground",
        "_draw_drawer_shell",
        "_DRAWER_FILL_RGBA",
        "_DRAWER_BORDER_RGBA",
    )
    for token in forbidden:
        assert token not in STATIC


def test_transition_surface_has_opaque_open_mode_and_live_underlay_close_mode() -> None:
    assert "self._live_underlay = False" in STATIC
    assert "def set_live_underlay_fade" in STATIC
    opaque = _body(STATIC, "def _sync_opaque_attribute", "def set_capture_suppressed")
    assert "not self._capture_suppressed and not self._live_underlay" in opaque
    assert "WA_OpaquePaintEvent" in opaque

    live = _body(STATIC, "def set_live_underlay_fade", "def clear_frames")
    assert "self._live_underlay = True" in live
    assert "self._base = QPixmap()" in live
    assert "self._top = _fit_frame(top, self)" in live


def test_transition_hot_path_never_renders_live_complex_widgets() -> None:
    paint = _body(STATIC, "def paintEvent", "def mousePressEvent")
    assert "if self._live_underlay:" in paint
    assert "painter.setOpacity(self._progress)" in paint
    assert "self._draw_fitted(painter, self._top)" in paint
    assert "CompositionMode_Source" in paint
    assert "CompositionMode_SourceOver" in paint

    # Close mode must not draw a cached/synthetic base at all.
    live_branch = paint.split("if self._live_underlay:", 1)[1].split(
        "painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)", 1
    )[0]
    assert "self._draw_fitted(painter, self._base)" not in live_branch
    assert "fillRect" not in live_branch

    for token in (
        "self.details",
        "_blur_pixmap",
        "_capture_source",
        ".render(",
        ".grab(",
        "layout",
        "QGraphics",
    ):
        assert token not in paint


def test_whole_root_capture_is_open_preparation_only_and_excludes_surface() -> None:
    assert "from PySide6.QtGui import QKeyEvent, QMouseEvent, QPainter, QPixmap, QRegion" in STATIC

    suppress = _body(STATIC, "def set_capture_suppressed", "def set_hold_frame")
    assert "self._capture_suppressed = suppressed" in suppress
    assert "self._sync_opaque_attribute()" in suppress

    capture = _body(STATIC, "def _render_root_without_transition", "def _frame_interval_ms")
    assert "self._transition.set_capture_suppressed(True)" in capture
    assert "self.root.render(" in capture
    assert "QWidget.RenderFlag.DrawChildren" in capture
    assert "self._transition.set_capture_suppressed(False)" in capture
    assert "drawer.render(" not in capture
    assert "drawer.grab(" not in capture


def test_detail_body_retirement_removes_old_glass_from_same_turn_capture() -> None:
    retire = _body(BASE_DETAILS, "def _retire_widget", "def _clear_body")
    assert retire.index("widget.hide()") < retire.index("widget.setParent(None)")
    assert retire.index("widget.setParent(None)") < retire.index("widget.deleteLater()")

    clear = _body(BASE_DETAILS, "def _clear_body", "@classmethod")
    assert "self._retire_widget(widget)" in clear
    assert "widget.deleteLater()" not in clear

    delete_layout = _body(BASE_DETAILS, "def _delete_layout", "def _section")
    assert "cls._retire_widget(widget)" in delete_layout
    assert "widget.deleteLater()" not in delete_layout


def test_opening_is_entry_a_to_final_live_modal_b() -> None:
    prepare = _body(STATIC, "def _prepare_open_transition", "def _show_with_animation")
    assert "entry = self.details._capture_source()" in prepare
    assert "blurred = self.details._blur_pixmap(entry)" in prepare
    assert "self._transition.set_hold_frame(self._entry_workspace_frame)" in prepare
    assert "self._show_real_modal(blurred)" in prepare
    assert "final_modal = self._render_root_without_transition()" in prepare
    assert "self._transition.set_transition_frames(" in prepare
    assert "self._entry_workspace_frame" in prepare
    assert "final_modal" in prepare

    real = _body(STATIC, "def _show_real_modal", "def _render_root_without_transition")
    assert "self.details.backdrop.setPixmap(blurred)" in real
    assert "self.details.backdrop.show()" in real
    assert "self.details.scrim.show()" in real
    assert "self.details.drawer.show()" in real
    assert "setOpacity" not in real


def test_endpoint_handoff_is_immediate_without_guessed_present_fence() -> None:
    assert "_handoff_timer" not in STATIC
    assert "_handoff_delay_ms" not in STATIC

    advance = _body(STATIC, "def _advance_motion", "def _prepare_open_transition")
    endpoint = advance.split("if linear >= 1.0:", 1)[1]
    assert endpoint.index("self._set_progress(self._motion_to)") < endpoint.index(
        "self._finish_motion()"
    )
    assert "self._transition.repaint()" not in endpoint
    assert "singleShot" not in endpoint


def test_open_handoff_reveals_the_same_live_widgets_without_sync_repaint() -> None:
    finish = _body(STATIC, "def _finish_open", "def _prepare_close_transition")
    assert "self._transition.hide()" in finish
    assert "self._transition.clear_frames()" in finish
    assert "self.details.close_button.setFocus" in finish
    assert "self.details.drawer.hide()" not in finish
    assert "setGraphicsEffect" not in finish
    assert "repaint()" not in finish
    assert "_cache_quick_base" not in finish


def test_close_fades_current_modal_over_the_real_live_workspace() -> None:
    close = _body(STATIC, "def _prepare_close_transition", "def request_close")
    assert "current_modal = self.details._capture_source()" in close
    assert "self._transition.set_hold_frame(current_modal)" in close
    assert "self._original_close()" in close
    assert "self._resume_underlay()" in close
    assert "self.root.update()" in close
    assert "self._transition.set_live_underlay_fade(current_modal, 1.0)" in close
    assert "self._modal_closed_for_motion = True" in close

    # The real workspace must be restored before the first transparent close frame.
    assert close.index("self._original_close()") < close.index("self._resume_underlay()")
    assert close.index("self._resume_underlay()") < close.index(
        "self._transition.set_live_underlay_fade(current_modal, 1.0)"
    )


def test_close_has_no_fake_workspace_capture_or_quick_widget_composition() -> None:
    forbidden = (
        "_capture_workspace_frame",
        "_capture_quick_base",
        "_cache_quick_base_if_open",
        "_quick_base_frame",
        "quick.grabWindow()",
        "latest_workspace",
    )
    for token in forbidden:
        assert token not in STATIC

    close = _body(STATIC, "def _prepare_close_transition", "def request_close")
    assert "_render_root_without_transition" not in close
    assert "CompositionMode_Source" not in close
    assert "CompositionMode_SourceOver" not in close


def test_close_handoff_surface_is_already_transparent_before_hide() -> None:
    finish = _body(STATIC, "def _finish_close", "def _fallback_open")
    assert "self._progress = 0.0" in finish
    assert "self._transition.set_progress(0.0)" in finish
    assert finish.index("self._transition.set_progress(0.0)") < finish.index(
        "self._transition.hide()"
    )
    assert "self._transition.clear_frames()" in finish
    assert "self.root.repaint()" not in finish
    assert "self.root.update()" in finish
    assert "if self._underlay_suspended:" in finish


def test_expensive_capture_work_is_outside_visible_animation_hot_path() -> None:
    advance = _body(STATIC, "def _advance_motion", "def _prepare_open_transition")
    for token in (
        "grabWindow",
        "_capture_source",
        "_render_root_without_transition",
        "_blur_pixmap",
        "layout",
    ):
        assert token not in advance


def test_one_progress_value_only_invalidates_the_transition_surface() -> None:
    setter = _body(STATIC, "def _set_progress", "def _advance_motion")
    assert "self._progress = value" in setter
    assert "self._transition.set_progress(value)" in setter
    assert "self.details" not in setter
    assert "_capture" not in setter
    assert "_blur_pixmap" not in setter
    assert "layout" not in setter


def test_motion_clock_is_precise_refresh_aware_and_time_based() -> None:
    assert "self._motion_timer = QTimer(self)" in STATIC
    assert "Qt.TimerType.PreciseTimer" in STATIC
    assert "screen.refreshRate()" in STATIC
    assert "min(240.0, refresh_hz)" in STATIC

    start = _body(STATIC, "def _start_fade", "def _set_progress")
    assert "time.perf_counter()" in start
    assert "self._motion_timer.setInterval(self._frame_interval_ms())" in start

    advance = _body(STATIC, "def _advance_motion", "def _prepare_open_transition")
    assert "time.perf_counter() - self._motion_started_s" in advance
    assert "self._motion_easing.valueForProgress(linear)" in advance
    assert "self._set_progress(value)" in advance


def test_reference_web_visible_timing_and_curves_are_unchanged() -> None:
    assert "_OPEN_MS = 500" in STATIC
    assert "_CLOSE_MS = 300" in STATIC
    assert "cubic-bezier(.25, .1, .25, 1)" in STATIC
    assert "cubic-bezier(.42, 0, .58, 1)" in STATIC


def test_interrupted_open_converts_current_visible_frame_to_live_underlay_close() -> None:
    close = _body(STATIC, "def request_close", "def _finish_close")
    assert "if self._state in {_STATE_OPENING, _STATE_OPEN}:" in close
    assert "prior_progress = max(0.0, min(1.0, float(self._progress)))" in close
    assert "self._prepare_close_transition()" in close
    assert "duration = max(1, int(round(_CLOSE_MS * prior_progress)))" in close
    assert "end=0.0" in close


def test_resize_during_motion_snaps_instead_of_rebuilding_frames() -> None:
    snap = _body(STATIC, "def _snap_motion_for_resize", "def eventFilter")
    assert "self._stop_animation()" in snap
    assert "self._sync_modal_geometry()" in snap
    assert "self._transition.set_progress(target)" in snap
    assert "self._finish_motion()" in snap
    assert "_capture" not in snap
    assert "_blur_pixmap" not in snap


def test_underlay_is_suspended_while_modal_is_open_but_resumes_before_close_fade() -> None:
    suspend = _body(STATIC, "def _suspend_underlay", "def _resume_underlay")
    resume = _body(STATIC, "def _resume_underlay", "def _sync_modal_geometry")
    assert "effects_timer.stop()" in suspend
    assert "activity_timer.stop()" in suspend
    assert "pointer_timer.stop()" in suspend
    assert 'quick.setProperty("animationRunning", False)' in suspend

    assert "pointer_timer.start()" in resume
    assert "effects_timer.start()" in resume
    assert "activity_timer.start()" in resume
    assert 'quick.setProperty("animationRunning", True)' in resume
    assert "schedule_mask()" in resume

    close = _body(STATIC, "def _prepare_close_transition", "def request_close")
    assert "self._resume_underlay()" in close


def test_fail_soft_keeps_existing_static_modal_available() -> None:
    fallback_open = _body(STATIC, "def _fallback_open", "def _fallback_close")
    assert "self._original_show_prepared_modal(ratio=ratio)" in fallback_open
    fallback_close = _body(STATIC, "def _fallback_close", "def _snap_motion_for_resize")
    assert "self._original_close()" in fallback_close
    assert "self._resume_underlay()" in fallback_close


def test_detail_start_action_still_uses_canonical_request() -> None:
    settings = _body(DETAILS, "def open_real_settings", "def _clone_table_widget")
    assert 'getattr(self.window, "_request_real_execution", None)' in settings
    assert 'getattr(self.window, "_stop_real_execution", None)' in settings
    assert "start_source.click()" not in settings
    assert "stop_source.click()" not in settings
    assert "start.setText(start_source.text())" in settings
    assert "start.setToolTip(start_source.toolTip())" in settings


def test_required_input_support_preserves_latest_auto_completion_preflight() -> None:
    assert "def request_start(self, _checked: bool = False)" in REQUIRED
    assert "window.real_start_button.clicked.connect(self.request_start)" in REQUIRED
    assert "window._request_real_execution = self.request_start" in REQUIRED
    request = _body(REQUIRED, "def request_start", "def _on_start_clicked")
    assert "self._all_required_covered()" in request
    assert "self._start_auto_completion(result)" in request
    assert "self._merged_overrides()" in request
    assert "self._write_overrides()" in request
    assert "self._original_start()" in request


def test_whole_card_body_remains_clickable() -> None:
    assert "Qt.TextInteractionFlag.TextSelectableByMouse" in STATIC
    assert "Qt.TextInteractionFlag.LinksAccessibleByMouse" in STATIC
    assert "WA_TransparentForMouseEvents" in STATIC
    assert "card.setCursor(Qt.CursorShape.PointingHandCursor)" in STATIC


def test_cleanup_restores_original_controller_and_deletes_only_surface() -> None:
    cleanup = _body(STATIC, "def cleanup", "def install_static_modal_interaction")
    assert "self.root.removeEventFilter(self)" in cleanup
    assert "self.details._show_prepared_modal = self._original_show_prepared_modal" in cleanup
    assert "self.details.close = self._original_close" in cleanup
    assert "self._transition.deleteLater()" in cleanup
    assert "setGraphicsEffect" not in cleanup


def test_sources_compile_without_importing_pyside() -> None:
    compile(STATIC, str(ROOT / "gui" / "static_modal_interaction.py"), "exec")
    compile(DETAILS, str(ROOT / "gui" / "card_details_fast.py"), "exec")
    compile(BASE_DETAILS, str(ROOT / "gui" / "card_details.py"), "exec")
    compile(REQUIRED, str(ROOT / "gui" / "required_input_support.py"), "exec")
