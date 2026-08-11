from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = (ROOT / "gui" / "static_modal_interaction.py").read_text(encoding="utf-8")
DETAILS = (ROOT / "gui" / "card_details_fast.py").read_text(encoding="utf-8")
RUNNER = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def _body(source: str, start: str, end: str) -> str:
    return source.split(start, 1)[1].split(end, 1)[0]


def test_runtime_stays_in_one_widget_modal_tree_without_second_quick_window() -> None:
    assert "from gui.static_modal_interaction import install_static_modal_interaction" in RUNNER
    assert "install_static_modal_interaction(window, details)" in RUNNER
    assert "from gui.modal_interaction import install_modal_interaction" not in RUNNER
    assert "modal_overlay_zorder" not in RUNNER
    assert "QQuickWindow(" not in STATIC
    assert "QQmlComponent" not in STATIC
    assert "QQuickItem" not in STATIC


def test_transition_animates_one_cached_panel_layer_not_the_real_drawer_tree() -> None:
    assert "class _PanelCompositor(QWidget)" in STATIC
    assert "QPropertyAnimation(self._compositor, b\"progress\", self)" in STATIC
    assert "QPainter.RenderHint.SmoothPixmapTransform" in STATIC
    assert "painter.translate(" in STATIC
    assert "painter.scale(scale, scale)" in STATIC
    assert "painter.setOpacity(progress)" in STATIC
    assert "QGraphicsOpacityEffect" not in STATIC
    assert "QParallelAnimationGroup" not in STATIC
    assert 'QPropertyAnimation(self.details.drawer, b"pos")' not in STATIC
    assert 'b"geometry"' not in STATIC


def test_cached_frame_is_rendered_from_the_fully_laid_out_real_drawer() -> None:
    render = _body(STATIC, "def _render_drawer_frame", "def _prepare_open_state")
    assert "drawer.ensurePolished()" in render
    assert "self.details.body_layout.activate()" in render
    assert "drawer.layout().activate()" in render
    assert "drawer.devicePixelRatioF()" in render
    assert "frame.setDevicePixelRatio(dpr)" in render
    assert "drawer.render(" in render
    assert "QWidget.RenderFlag.DrawWindowBackground" in render
    assert "QWidget.RenderFlag.DrawChildren" in render
    assert "drawer.grab()" not in STATIC


def test_open_builds_snapshot_without_exposing_intermediate_real_drawer() -> None:
    prepare = _body(STATIC, "def _prepare_open_state", "def _prepare_close_state")
    assert "self.root.setUpdatesEnabled(False)" in prepare
    assert "self.details.drawer.setGeometry(target)" in prepare
    assert prepare.index("self.details.drawer.show()") < prepare.index("self._render_drawer_frame()")
    assert prepare.index("self._render_drawer_frame()") < prepare.index("self.details.drawer.hide()")
    assert "self.details.backdrop.show()" in prepare
    assert "self.details.scrim.show()" in prepare
    assert "self._compositor.set_frame(frame, target, progress=0.0)" in prepare
    assert "self._compositor.show()" in prepare


def test_open_handoff_is_atomic_and_final_interaction_returns_to_real_drawer() -> None:
    finish = _body(STATIC, "def _finish_open", "def request_close")
    assert "self.root.setUpdatesEnabled(False)" in finish
    assert finish.index("self.details.drawer.show()") < finish.index("self._compositor.hide()")
    assert "self._compositor.clear_frame()" in finish
    assert "self.root.repaint()" in finish
    assert "self.details.close_button.setFocus" in finish


def test_close_captures_current_real_content_before_hiding_it() -> None:
    prepare = _body(STATIC, "def _prepare_close_state", "def _show_with_animation")
    assert prepare.index("self._render_drawer_frame()") < prepare.index("self.details.drawer.hide()")
    assert "self._compositor.set_frame(frame, target, progress=1.0)" in prepare
    assert "self._compositor.repaint()" in prepare


def test_motion_uses_float_transform_and_slower_presentation_timing() -> None:
    assert "_OPEN_MS = 300" in STATIC
    assert "_CLOSE_MS = 250" in STATIC
    assert "_OPEN_RISE_PX = 18.0" in STATIC
    assert "_START_SCALE = 0.992" in STATIC
    assert "QPointF" in STATIC
    assert "QEasingCurve.Type.OutCubic" in STATIC
    assert "QEasingCurve.Type.InCubic" in STATIC


def test_compositor_is_small_mouse_transparent_widget_not_fullscreen_native_overlay() -> None:
    init = _body(STATIC, "class _PanelCompositor", "class StaticModalInteractionController")
    assert "WA_TransparentForMouseEvents" in init
    assert "WA_NoSystemBackground" in init
    assert "WA_TranslucentBackground" in init
    assert "target.adjusted(" in init
    assert "_COMPOSITOR_PAD" in init
    assert "self.setGeometry(bounds)" in init
    assert "self.parentWidget()" in init
    assert "QWindow" not in init


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


def test_close_inputs_and_escape_work_even_while_real_drawer_is_hidden_for_animation() -> None:
    assert "self.details.close = self.request_close" in STATIC
    assert "self.details.close_button.clicked.connect(self.request_close)" in STATIC
    assert "self.details.scrim.clicked.connect(self.request_close)" in STATIC
    assert "self.root.installEventFilter(self)" in STATIC
    event_filter = _body(STATIC, "def eventFilter", "def cleanup")
    assert "Qt.Key.Key_Escape" in event_filter
    assert "_STATE_OPENING" in event_filter
    assert "self.request_close()" in event_filter


def test_fail_soft_keeps_static_modal_available() -> None:
    fallback_open = _body(STATIC, "def _fallback_open", "def _fallback_close")
    assert "self._original_close()" in fallback_open
    assert "self._original_show_prepared_modal(ratio=ratio)" in fallback_open
    fallback_close = _body(STATIC, "def _fallback_close", "def eventFilter")
    assert "self._original_close()" in fallback_close
    assert "self._resume_underlay()" in fallback_close


def test_real_modal_stays_in_existing_qwidget_tree() -> None:
    assert "self.backdrop = QLabel(self.root)" in DETAILS
    assert "self.scrim.setGeometry(self.root.rect())" in DETAILS
    assert "self.drawer.setGeometry(self._drawer_rect())" in DETAILS
    assert "self.drawer.show()" in DETAILS
    assert "self.drawer.hide()" in DETAILS
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
