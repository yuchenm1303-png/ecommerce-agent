from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = (ROOT / "gui" / "static_modal_interaction.py").read_text(encoding="utf-8")
DETAILS = (ROOT / "gui" / "card_details_fast.py").read_text(encoding="utf-8")
REQUIRED = (ROOT / "gui" / "required_input_support.py").read_text(encoding="utf-8")
RUNNER = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def _body(source: str, start: str, end: str) -> str:
    return source.split(start, 1)[1].split(end, 1)[0]


def test_runtime_stays_in_existing_qwidget_tree_without_second_quick_window() -> None:
    assert "from gui.static_modal_interaction import install_static_modal_interaction" in RUNNER
    assert "install_static_modal_interaction(window, details)" in RUNNER
    assert "from gui.modal_interaction import install_modal_interaction" not in RUNNER
    assert "modal_overlay_zorder" not in RUNNER
    assert "QQuickWindow(" not in STATIC
    assert "QQmlComponent" not in STATIC
    assert "QQuickItem" not in STATIC


def test_reference_detail_model_freezes_old_cards_instead_of_repainting_them() -> None:
    assert "class _FrozenBackdrop(QWidget):" in STATIC
    assert 'self.setObjectName("cardDetailFrozenBackdrop")' in STATIC
    assert "WA_OpaquePaintEvent" in STATIC
    assert "self._clear" in STATIC
    assert "self._blurred" in STATIC
    assert "painter.drawPixmap" in STATIC
    assert "94.0 * self._progress" in STATIC


def test_no_fullscreen_graphics_opacity_or_modal_parent_compositor_remains() -> None:
    assert "_modal_layer" not in STATIC
    assert "cardDetailFadeLayer" not in STATIC
    assert "QGraphicsOpacityEffect(self.details.drawer)" in STATIC
    assert "QGraphicsOpacityEffect(self.root" not in STATIC
    assert "QGraphicsOpacityEffect(self._frozen_backdrop" not in STATIC
    assert "setGraphicsEffect(self._drawer_effect)" in STATIC


def test_one_scalar_progress_drives_background_and_live_drawer_together() -> None:
    assert 'QPropertyAnimation(self, b"progress", self)' in STATIC
    setter = _body(STATIC, "def _set_progress", "progress = Property")
    assert "self._frozen_backdrop.set_progress(value)" in setter
    assert "self._drawer_effect.setOpacity(value)" in setter
    assert "progress = Property(float, _get_progress, _set_progress)" in STATIC


def test_modal_animation_has_no_drawer_snapshot_or_handoff_pipeline() -> None:
    forbidden = (
        "_ModalTransitionCompositor",
        "_panel_frame",
        "_render_drawer_frame",
        "_capture_panel_offscreen",
        "_suppress_drawer_paint_for_capture",
        "drawer.render(",
        "drawer.grab(",
        "DrawChildren",
        "DrawWindowBackground",
        "_draw_drawer_shell",
        "_START_SCALE",
        "_OPEN_RISE_PX",
        "painter.translate(",
        "painter.scale(",
        "QParallelAnimationGroup",
    )
    for token in forbidden:
        assert token not in STATIC


def test_open_captures_clear_once_blurs_once_then_animates_cached_frames() -> None:
    prepare = _body(STATIC, "def _prepare_live_modal", "def _show_with_animation")
    assert "source = self.details._capture_source()" in prepare
    assert "blurred = self.details._blur_pixmap(source)" in prepare
    assert "self._frozen_backdrop.set_frames(source, blurred)" in prepare
    assert "self.details.backdrop.hide()" in prepare
    assert "self.details.scrim.hide()" in prepare
    assert "self._drawer_effect.setEnabled(True)" in prepare
    assert "self._set_progress(0.0)" in prepare
    assert "self._frozen_backdrop.show()" in prepare
    assert "self.details.drawer.show()" in prepare

    setter = _body(STATIC, "def _set_progress", "progress = Property")
    assert "_blur_pixmap" not in setter
    assert "_capture_source" not in setter


def test_reference_web_fade_timing_and_curves_are_locked() -> None:
    assert "_OPEN_MS = 500" in STATIC
    assert "_CLOSE_MS = 300" in STATIC
    assert "cubic-bezier(.25, .1, .25, 1)" in STATIC
    assert "cubic-bezier(.42, 0, .58, 1)" in STATIC
    assert "QEasingCurve.Type.BezierSpline" in STATIC
    assert "addCubicBezierSegment" in STATIC


def test_steady_open_disables_only_drawer_effect() -> None:
    finish = _body(STATIC, "def _finish_open", "def request_close")
    assert "self._set_progress(1.0)" in finish
    assert "self._drawer_effect.setEnabled(False)" in finish
    assert "self.details.drawer.repaint()" in finish
    assert "self._frozen_backdrop.hide()" not in finish


def test_close_reuses_same_frozen_background_and_live_drawer() -> None:
    close = _body(STATIC, "def request_close", "def _finish_close")
    assert "self._drawer_effect.setEnabled(True)" in close
    assert "end=0.0" in close
    assert "duration_ms=_CLOSE_MS" in close
    assert "_capture_source" not in close
    assert "_blur_pixmap" not in close
    assert "render(" not in close

    finish = _body(STATIC, "def _finish_close", "def _fallback_open")
    assert "self._set_progress(0.0)" in finish
    assert "self._original_close()" in finish
    assert "self._frozen_backdrop.hide()" in finish
    assert "self._frozen_backdrop.clear_frames()" in finish
    assert "self._resume_underlay()" in finish


def test_interrupted_open_reverses_from_current_progress() -> None:
    close = _body(STATIC, "def request_close", "def _finish_close")
    assert "if self._state == _STATE_OPENING:" in close
    assert "float(self._progress)" in close
    assert "duration = max(1, int(round(_CLOSE_MS * current)))" in close
    assert "end=0.0" in close


def test_resize_only_updates_frozen_backdrop_and_real_drawer_geometry() -> None:
    sync = _body(STATIC, "def _sync_modal_geometry", "def _stop_animation")
    assert "self._frozen_backdrop.setGeometry(self.root.rect())" in sync
    assert "self.details.drawer.setGeometry(self.details._drawer_rect())" in sync
    assert "self.details.backdrop.setGeometry" not in sync
    assert "self.details.scrim.setGeometry" not in sync


def test_underlay_animation_sources_are_suspended_including_activity_presence() -> None:
    suspend = _body(STATIC, "def _suspend_underlay", "def _resume_underlay")
    resume = _body(STATIC, "def _resume_underlay", "def _sync_modal_geometry")
    assert 'getattr(self.window, "_nekro_card_fx", None)' in suspend
    assert "effects_timer.stop()" in suspend
    assert "activity_timer.stop()" in suspend
    assert "pointer_timer.stop()" in suspend
    assert 'quick.setProperty("animationRunning", False)' in suspend

    assert "pointer_timer.start()" in resume
    assert "effects_timer.start()" in resume
    assert "activity_timer.start()" in resume
    assert 'quick.setProperty("animationRunning", True)' in resume


def test_source_capture_is_split_from_blur_for_frozen_crossfade() -> None:
    assert "def _capture_source(self) -> QPixmap:" in DETAILS
    capture = _body(DETAILS, "def _capture_source", "def _capture_backdrop")
    assert "screen.grabWindow" in capture
    assert "self.root.grab()" in capture
    backdrop = _body(DETAILS, "def _capture_backdrop", "def _schedule_geometry")
    assert "self._blur_pixmap(self._capture_source())" in backdrop


def test_detail_start_action_calls_canonical_request_not_hidden_button_click() -> None:
    settings = _body(DETAILS, "def open_real_settings", "def _clone_table_widget")
    assert 'getattr(self.window, "_request_real_execution", None)' in settings
    assert 'getattr(self.window, "_stop_real_execution", None)' in settings
    assert "start_source.click()" not in settings
    assert "stop_source.click()" not in settings
    assert "start.setText(start_source.text())" in settings
    assert "start.setToolTip(start_source.toolTip())" in settings
    assert "QPushButton#modalPrimaryButton:pressed" in DETAILS


def test_required_input_support_owns_one_canonical_execution_request() -> None:
    assert "def request_start(self, _checked: bool = False)" in REQUIRED
    assert "window.real_start_button.clicked.connect(self.request_start)" in REQUIRED
    assert "window._request_real_execution = self.request_start" in REQUIRED
    request = _body(REQUIRED, "def request_start", "def _on_start_clicked")
    assert "result.ready <= 0" in request
    assert "self._all_required_filled()" in request
    assert "self._write_overrides()" in request
    assert "self._original_start()" in request


def test_fail_soft_keeps_existing_static_modal_available() -> None:
    fallback_open = _body(STATIC, "def _fallback_open", "def _fallback_close")
    assert "self._original_show_prepared_modal(ratio=ratio)" in fallback_open
    fallback_close = _body(STATIC, "def _fallback_close", "def eventFilter")
    assert "self._original_close()" in fallback_close
    assert "self._resume_underlay()" in fallback_close


def test_whole_card_body_remains_clickable() -> None:
    assert "def _label_is_passive" in STATIC
    assert "Qt.TextInteractionFlag.TextSelectableByMouse" in STATIC
    assert "Qt.TextInteractionFlag.LinksAccessibleByMouse" in STATIC
    assert "WA_TransparentForMouseEvents" in STATIC
    assert "card.setCursor(Qt.CursorShape.PointingHandCursor)" in STATIC


def test_cleanup_restores_original_controller_and_removes_local_effect() -> None:
    cleanup = _body(STATIC, "def cleanup", "def install_static_modal_interaction")
    assert "self.root.removeEventFilter(self)" in cleanup
    assert "self.details._show_prepared_modal = self._original_show_prepared_modal" in cleanup
    assert "self.details.close = self._original_close" in cleanup
    assert "self.details.drawer.setGraphicsEffect(None)" in cleanup
    assert "self._frozen_backdrop.deleteLater()" in cleanup


def test_widget_animation_sources_compile_without_importing_pyside() -> None:
    compile(STATIC, str(ROOT / "gui" / "static_modal_interaction.py"), "exec")
    compile(DETAILS, str(ROOT / "gui" / "card_details_fast.py"), "exec")
    compile(REQUIRED, str(ROOT / "gui" / "required_input_support.py"), "exec")
