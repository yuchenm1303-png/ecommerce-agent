from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = (ROOT / "gui" / "static_modal_interaction.py").read_text(encoding="utf-8")
DETAILS = (ROOT / "gui" / "card_details_fast.py").read_text(encoding="utf-8")
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


def test_reference_web_model_is_one_live_modal_layer_and_one_opacity() -> None:
    assert 'self._modal_layer = QWidget(self.root)' in STATIC
    assert 'self._modal_layer.setObjectName("cardDetailFadeLayer")' in STATIC
    assert "QGraphicsOpacityEffect(self._modal_layer)" in STATIC
    assert 'QPropertyAnimation(self._fade_effect, b"opacity", self)' in STATIC

    for name in ("backdrop", "scrim", "drawer"):
        assert f"self.details.{name}" in STATIC
    assert "widget.setParent(self._modal_layer)" in STATIC


def test_modal_animation_is_subtractive_and_has_no_snapshot_compositor_pipeline() -> None:
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
        "_DRAWER_FILL_RGBA",
        "_DRAWER_BORDER_RGBA",
        "_START_SCALE",
        "_OPEN_RISE_PX",
        "painter.translate(",
        "painter.scale(",
        "QPainter",
        "QRegion",
    )
    for token in forbidden:
        assert token not in STATIC


def test_open_prepares_final_live_geometry_then_fades_only_parent_opacity() -> None:
    prepare = _body(STATIC, "def _prepare_live_modal", "def _show_with_animation")
    assert "snapshot = self.details._capture_backdrop()" in prepare
    assert "self.details.backdrop.setPixmap(snapshot)" in prepare
    assert "self._sync_modal_geometry()" in prepare
    assert "self._fade_effect.setOpacity(0.0)" in prepare
    assert "self.details.backdrop.show()" in prepare
    assert "self.details.scrim.show()" in prepare
    assert "self.details.drawer.show()" in prepare
    assert "self._modal_layer.show()" in prepare
    assert "render(" not in prepare
    assert "grab(" not in prepare

    show = _body(STATIC, "def _show_with_animation", "def _finish_motion")
    assert "end=1.0" in show
    assert "duration_ms=_OPEN_MS" in show
    assert "easing=_css_ease()" in show


def test_reference_web_fade_timing_and_curves_are_locked() -> None:
    assert "_OPEN_MS = 500" in STATIC
    assert "_CLOSE_MS = 300" in STATIC
    assert "cubic-bezier(.25, .1, .25, 1)" in STATIC
    assert "cubic-bezier(.42, 0, .58, 1)" in STATIC
    assert "QEasingCurve.Type.BezierSpline" in STATIC
    assert "addCubicBezierSegment" in STATIC


def test_blur_is_final_once_and_appears_gradually_only_through_parent_fade() -> None:
    prepare = _body(STATIC, "def _prepare_live_modal", "def _show_with_animation")
    assert "self.details._capture_backdrop()" in prepare
    assert "self.details.backdrop.setPixmap(snapshot)" in prepare
    assert "_blur_pixmap" not in STATIC
    assert "blurRadius" not in STATIC
    assert "soft_blur" not in STATIC
    assert "_full_blur" not in STATIC


def test_text_controls_and_glass_are_the_same_live_subtree_not_copies() -> None:
    assert "self.details.drawer.setParent(self._modal_layer)" not in STATIC  # loop owns all three uniformly
    assert "for widget in (self.details.backdrop, self.details.scrim, self.details.drawer):" in STATIC
    assert "widget.setParent(self._modal_layer)" in STATIC
    assert "self.details.drawer.show()" in STATIC
    assert "QPixmap" not in STATIC
    assert "QGraphicsOpacityEffect(self.details.drawer)" not in STATIC


def test_steady_open_disables_fullscreen_opacity_effect_for_direct_widget_paint() -> None:
    finish = _body(STATIC, "def _finish_open", "def request_close")
    assert "self._fade_effect.setOpacity(1.0)" in finish
    assert "self._fade_effect.setEnabled(False)" in finish
    assert "self._modal_layer.repaint()" in finish
    assert "self.details.close_button.setFocus" in finish


def test_close_reuses_same_live_tree_and_only_reverses_parent_opacity() -> None:
    close = _body(STATIC, "def request_close", "def _finish_close")
    assert "self._fade_effect.setOpacity(1.0)" in close
    assert "self._fade_effect.setEnabled(True)" in close
    assert "end=0.0" in close
    assert "duration_ms=_CLOSE_MS" in close
    assert "easing=_css_ease_in_out()" in close
    assert "_capture_backdrop" not in close
    assert "render(" not in close

    finish = _body(STATIC, "def _finish_close", "def _fallback_open")
    assert finish.index("self._fade_effect.setOpacity(0.0)") < finish.index("self._original_close()")
    assert "self._modal_layer.hide()" in finish
    assert "self._resume_underlay()" in finish


def test_interrupted_open_reverses_from_current_live_opacity() -> None:
    close = _body(STATIC, "def request_close", "def _finish_close")
    assert "if self._state == _STATE_OPENING:" in close
    assert "float(self._fade_effect.opacity())" in close
    assert "duration = max(1, int(round(_CLOSE_MS * current)))" in close
    assert "end=0.0" in close


def test_resize_only_updates_real_modal_geometry_without_transition_ownership() -> None:
    sync = _body(STATIC, "def _sync_modal_geometry", "def _stop_animation")
    assert "self._modal_layer.setGeometry(self.root.rect())" in sync
    assert "self.details.backdrop.setGeometry(self._modal_layer.rect())" in sync
    assert "self.details.scrim.setGeometry(self._modal_layer.rect())" in sync
    assert "self.details.drawer.setGeometry(self.details._drawer_rect())" in sync
    assert "animation" not in sync.lower()

    event_filter = _body(STATIC, "def eventFilter", "def cleanup")
    assert "QEvent.Type.Resize" in event_filter
    assert "self._sync_modal_geometry()" in event_filter


def test_underlay_work_is_suspended_only_as_lifecycle_not_visual_layers() -> None:
    suspend = _body(STATIC, "def _suspend_underlay", "def _resume_underlay")
    resume = _body(STATIC, "def _resume_underlay", "def _sync_modal_geometry")
    assert 'getattr(self.window, "_nekro_card_fx", None)' in suspend
    assert 'getattr(card_fx, "suspend_for_modal", None)' in suspend
    assert "effects_timer.stop()" in suspend
    assert "pointer_timer.stop()" in suspend
    assert 'quick.setProperty("animationRunning", False)' in suspend

    assert 'getattr(card_fx, "resume_from_modal", None)' in resume
    assert "pointer_timer.start()" in resume
    assert "effects_timer.start()" in resume
    assert 'quick.setProperty("animationRunning", True)' in resume


def test_fail_soft_keeps_existing_static_modal_available() -> None:
    fallback_open = _body(STATIC, "def _fallback_open", "def _fallback_close")
    assert "self._original_show_prepared_modal(ratio=ratio)" in fallback_open
    assert "self._modal_layer.show()" in fallback_open
    fallback_close = _body(STATIC, "def _fallback_close", "def eventFilter")
    assert "self._original_close()" in fallback_close
    assert "self._modal_layer.hide()" in fallback_close
    assert "self._resume_underlay()" in fallback_close


def test_real_modal_remains_existing_business_widget_content() -> None:
    assert "self.backdrop = QLabel(self.root)" in DETAILS
    assert "self.drawer.setGeometry(self._drawer_rect())" in DETAILS
    assert "self.window.hide()" not in DETAILS
    assert "self.window.show()" not in DETAILS


def test_whole_card_body_remains_clickable() -> None:
    assert "def _label_is_passive" in STATIC
    assert "Qt.TextInteractionFlag.TextSelectableByMouse" in STATIC
    assert "Qt.TextInteractionFlag.LinksAccessibleByMouse" in STATIC
    assert "WA_TransparentForMouseEvents" in STATIC
    assert "card.setCursor(Qt.CursorShape.PointingHandCursor)" in STATIC


def test_cleanup_restores_original_controller_structure() -> None:
    cleanup = _body(STATIC, "def cleanup", "def install_static_modal_interaction")
    assert "self.root.removeEventFilter(self)" in cleanup
    assert "self.details._show_prepared_modal = self._original_show_prepared_modal" in cleanup
    assert "self.details.close = self._original_close" in cleanup
    assert "widget.setParent(self.root)" in cleanup
    assert "self._modal_layer.deleteLater()" in cleanup


def test_widget_animation_sources_compile_without_importing_pyside() -> None:
    compile(STATIC, str(ROOT / "gui" / "static_modal_interaction.py"), "exec")
    compile(DETAILS, str(ROOT / "gui" / "card_details_fast.py"), "exec")
