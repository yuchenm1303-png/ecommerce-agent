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


def test_one_progress_clock_owns_blur_scrim_panel_and_text() -> None:
    assert "class _ModalTransitionCompositor(QWidget)" in STATIC
    assert 'QPropertyAnimation(self._compositor, b"progress", self)' in STATIC
    paint = _body(STATIC, "def paintEvent", "def mousePressEvent")
    assert "_full_blur" in paint
    assert "_SCRIM_ALPHA * progress" in paint
    assert "painter.setOpacity(progress)" in paint
    assert "_panel_frame" in paint
    assert "painter.translate(" in paint
    assert "painter.scale(scale, scale)" in paint


def test_transition_has_no_intermediate_white_haze_layer() -> None:
    assert "_soft_blur" not in STATIC
    assert "_soften_source" not in STATIC
    assert "_SOFT_BLUR_CROSSOVER" not in STATIC
    paint = _body(STATIC, "def paintEvent", "def mousePressEvent")
    assert "self._draw_scaled(painter, self._full_blur, viewport)" in paint
    assert "painter.setOpacity(progress)" in paint


def test_zero_progress_explicitly_clears_translucent_backing_store() -> None:
    paint = _body(STATIC, "def paintEvent", "def mousePressEvent")
    assert "QPainter.CompositionMode.CompositionMode_Source" in paint
    assert "painter.fillRect(self.rect(), QColor(0, 0, 0, 0))" in paint
    assert "QPainter.CompositionMode.CompositionMode_SourceOver" in paint
    assert paint.index("painter.fillRect(self.rect(), QColor(0, 0, 0, 0))") < paint.index(
        "if progress <= 0.0:"
    )


def test_background_blur_is_progressive_not_switched_before_animation() -> None:
    prepare = _body(STATIC, "def _prepare_open_state", "def _prepare_close_state")
    assert "source = self._capture_source_frame()" in prepare
    assert "full_blur = self.details._blur_pixmap(source)" in prepare
    assert "self.details.backdrop.hide()" in prepare
    assert "self.details.scrim.hide()" in prepare
    assert "self._compositor.set_frames(" in prepare
    assert "full_blur=full_blur" in prepare
    assert "progress=0.0" in prepare
    assert "soft_blur=" not in prepare
    assert "self.details.backdrop.show()" not in prepare
    assert "self.details.scrim.show()" not in prepare


def test_drawer_content_is_primed_as_real_visible_tree_outside_parent_clip() -> None:
    capture = _body(STATIC, "def _capture_panel_offscreen", "def _prepare_open_state")
    assert "self.root.width() + 64" in capture
    assert "drawer.setGeometry(offscreen)" in capture
    assert capture.index("drawer.show()") < capture.index("self._render_drawer_frame()")
    assert capture.index("self._render_drawer_frame()") < capture.index("drawer.hide()")
    assert "WA_DontShowOnScreen" not in capture


def test_cached_panel_contains_only_real_child_widgets_not_outer_glass_shell() -> None:
    settle = _body(STATIC, "def _settle_drawer_tree", "def _render_drawer_frame")
    render = _body(STATIC, "def _render_drawer_frame", "def _capture_panel_offscreen")
    assert "drawer.findChildren(QWidget)" in settle
    assert "widget.ensurePolished()" in settle
    assert "QCoreApplication.sendPostedEvents(None, QEvent.Type.PolishRequest)" in settle
    assert "QCoreApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest)" in settle
    assert "layout.activate()" in settle
    assert "for child in drawer.children()" in render
    assert "child.parentWidget() is not drawer" in render
    assert "child.render(" in render
    assert "drawer.render(" not in render
    assert "QWidget.RenderFlag.DrawWindowBackground" in render
    assert "QWidget.RenderFlag.DrawChildren" in render
    assert "drawer.grab()" not in STATIC


def test_compositor_paints_original_drawer_qss_shell_once() -> None:
    compositor = _body(STATIC, "class _ModalTransitionCompositor", "class StaticModalInteractionController")
    paint = _body(STATIC, "def paintEvent", "def mousePressEvent")
    assert "_DRAWER_FILL_RGBA = (220, 228, 238, 74)" in STATIC
    assert "_DRAWER_BORDER_RGBA = (255, 255, 255, 72)" in STATIC
    assert "_DRAWER_RADIUS = 14.0" in STATIC
    assert "def _draw_drawer_shell" in compositor
    assert "painter.drawRoundedRect(shell, _DRAWER_RADIUS, _DRAWER_RADIUS)" in compositor
    assert "self._draw_drawer_shell(painter, target.width(), target.height())" in paint
    assert paint.index("self._draw_drawer_shell(") < paint.index(
        "painter.drawPixmap(QPointF(0.0, 0.0), self._panel_frame)"
    )


def test_open_handoff_replaces_identical_100_percent_frame_atomically() -> None:
    finish = _body(STATIC, "def _finish_open", "def request_close")
    assert "self.root.setUpdatesEnabled(False)" in finish
    assert "self.details.backdrop.show()" in finish
    assert "self.details.scrim.show()" in finish
    assert "self.details.drawer.show()" in finish
    assert finish.index("self.details.drawer.show()") < finish.index("self._compositor.hide()")
    assert "self.root.repaint()" in finish


def test_close_first_covers_real_modal_with_identical_compositor_then_reverses() -> None:
    prepare = _body(STATIC, "def _prepare_close_state", "def _show_with_animation")
    assert "panel_frame = self._render_drawer_frame()" in prepare
    assert "self._compositor.set_panel_frame(panel_frame, target)" in prepare
    assert 'self._compositor.setProperty("progress", 1.0)' in prepare
    assert prepare.index("self._compositor.repaint()") < prepare.index("self._original_close()")
    assert "self._original_close()" in prepare

    close = _body(STATIC, "def request_close", "def _finish_close")
    assert "end=0.0" in close
    assert "duration_ms=_CLOSE_MS" in close
    assert "QEasingCurve.Type.InCubic" in close


def test_transition_is_fullscreen_qwidget_only_during_motion_and_blocks_stray_input() -> None:
    compositor = _body(STATIC, "class _ModalTransitionCompositor", "class StaticModalInteractionController")
    assert "self.setGeometry(parent.rect())" in compositor
    assert "WA_NoSystemBackground" in compositor
    assert "WA_TranslucentBackground" in compositor
    assert "WA_TransparentForMouseEvents" not in compositor
    assert "def mousePressEvent" in compositor
    assert "event.accept()" in compositor
    assert "QWindow" not in compositor


def test_motion_timing_and_float_panel_transform_are_preserved() -> None:
    assert "_OPEN_MS = 300" in STATIC
    assert "_CLOSE_MS = 250" in STATIC
    assert "_OPEN_RISE_PX = 18.0" in STATIC
    assert "_START_SCALE = 0.992" in STATIC
    assert "QPointF" in STATIC
    assert "QEasingCurve.Type.OutCubic" in STATIC
    assert "QEasingCurve.Type.InCubic" in STATIC
    assert "QGraphicsOpacityEffect" not in STATIC
    assert "QParallelAnimationGroup" not in STATIC
    assert 'QPropertyAnimation(self.details.drawer, b"pos")' not in STATIC
    assert 'b"geometry"' not in STATIC


def test_geometry_timer_and_obscured_underlay_cannot_compete_with_transition() -> None:
    guard = _body(STATIC, "def _schedule_geometry_guarded", "def _set_motion_active")
    assert "_STATE_OPENING" in guard
    assert "_STATE_CLOSING" in guard
    assert "self._geometry_sync_pending = True" in guard

    motion = _body(STATIC, "def _set_motion_active", "def _stop_animation")
    assert "timer.stop()" in motion

    suspend = _body(STATIC, "def _suspend_underlay", "def _resume_underlay")
    assert 'getattr(self.window, "_nekro_card_fx", None)' in suspend
    assert 'getattr(card_fx, "suspend_for_modal", None)' in suspend
    assert 'getattr(self.window, "_nekro_effects", None)' in suspend
    assert "effects_timer.stop()" in suspend
    assert 'getattr(self.background, "_pointer_timer", None)' in suspend
    assert "timer.stop()" in suspend
    assert 'quick.setProperty("animationRunning", False)' in suspend


def test_interrupted_open_reverses_same_complete_frame_instead_of_handoff() -> None:
    close = _body(STATIC, "def request_close", "def _finish_close")
    assert "if self._state == _STATE_OPENING:" in close
    assert "current = float(self._compositor.property" in close
    assert "duration = max(1, int(round(_CLOSE_MS * current)))" in close
    assert "end=0.0" in close


def test_fail_soft_keeps_static_modal_available() -> None:
    fallback_open = _body(STATIC, "def _fallback_open", "def _fallback_close")
    assert "self._original_close()" in fallback_open
    assert "self._original_show_prepared_modal(ratio=ratio)" in fallback_open
    fallback_close = _body(STATIC, "def _fallback_close", "def eventFilter")
    assert "self._original_close()" in fallback_close
    assert "self._resume_underlay()" in fallback_close


def test_real_modal_remains_existing_qwidget_content_after_handoff() -> None:
    assert "self.backdrop = QLabel(self.root)" in DETAILS
    assert "self.scrim.setGeometry(self.root.rect())" in DETAILS
    assert "self.drawer.setGeometry(self._drawer_rect())" in DETAILS
    assert "self.window.hide()" not in DETAILS
    assert "self.window.show()" not in DETAILS


def test_whole_card_body_remains_clickable() -> None:
    assert "def _label_is_passive" in STATIC
    assert "Qt.TextInteractionFlag.TextSelectableByMouse" in STATIC
    assert "Qt.TextInteractionFlag.LinksAccessibleByMouse" in STATIC
    assert "WA_TransparentForMouseEvents" in STATIC
    assert "card.setCursor(Qt.CursorShape.PointingHandCursor)" in STATIC


def test_widget_animation_sources_compile_without_importing_pyside() -> None:
    compile(STATIC, str(ROOT / "gui" / "static_modal_interaction.py"), "exec")
    compile(DETAILS, str(ROOT / "gui" / "card_details_fast.py"), "exec")
