from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = (ROOT / "gui" / "static_modal_interaction.py").read_text(encoding="utf-8")
DETAILS = (ROOT / "gui" / "card_details_fast.py").read_text(encoding="utf-8")
RUNNER = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def _body(source: str, start: str, end: str) -> str:
    return source.split(start, 1)[1].split(end, 1)[0]


def test_runtime_uses_widget_modal_and_never_installs_second_quick_window() -> None:
    assert "from gui.static_modal_interaction import install_static_modal_interaction" in RUNNER
    assert "install_static_modal_interaction(window, details)" in RUNNER
    assert "from gui.modal_interaction import install_modal_interaction" not in RUNNER
    assert "install_modal_interaction(window, details)" not in RUNNER
    assert "modal_overlay_zorder" not in RUNNER
    assert "QQuickWindow(" not in STATIC
    assert "QQmlComponent" not in STATIC
    assert "QQuickItem" not in STATIC


def test_real_drawer_owns_motion_and_text_fade_without_snapshot_handoff() -> None:
    assert "QGraphicsOpacityEffect" in STATIC
    assert "QParallelAnimationGroup" in STATIC
    assert "QPropertyAnimation" in STATIC
    assert "self._drawer_effect = QGraphicsOpacityEffect(self.details.drawer)" in STATIC
    assert "self.details.drawer.setGraphicsEffect(self._drawer_effect)" in STATIC
    assert 'QPropertyAnimation(self.details.drawer, b"pos")' in STATIC
    assert 'QPropertyAnimation(self._drawer_effect, b"opacity")' in STATIC

    assert "_PanelTransitionWidget" not in STATIC
    assert "QPainter" not in STATIC
    assert "QPixmap" not in STATIC
    assert "QElapsedTimer" not in STATIC
    assert "drawer.grab()" not in STATIC


def test_visual_timing_and_motion_constants_are_unchanged() -> None:
    assert "_OPEN_MS = 220" in STATIC
    assert "_OPEN_FADE_MS = 205" in STATIC
    assert "_CLOSE_MS = 170" in STATIC
    assert "_CLOSE_FADE_MS = 150" in STATIC
    assert "_OPEN_RISE_PX = 12" in STATIC
    assert "_CLOSE_DROP_PX = 9" in STATIC
    assert "QEasingCurve.Type.OutQuart" in STATIC
    assert "QEasingCurve.Type.OutCubic" in STATIC
    assert "QEasingCurve.Type.InCubic" in STATIC
    assert 'b"geometry"' not in STATIC


def test_animation_graph_is_allocated_once_and_reused() -> None:
    init = _body(STATIC, "def __init__", "def _label_is_passive")
    assert "self._motion_group = QParallelAnimationGroup(self)" in init
    assert "self._position_animation = QPropertyAnimation" in init
    assert "self._opacity_animation = QPropertyAnimation" in init
    assert "self._motion_group.addAnimation(self._position_animation)" in init
    assert "self._motion_group.addAnimation(self._opacity_animation)" in init
    assert "self._motion_group.finished.connect(self._finish_motion)" in init

    show = _body(STATIC, "def _show_with_animation", "def _finish_open")
    close = _body(STATIC, "def request_close", "def _finish_close")
    assert "QParallelAnimationGroup(" not in show
    assert "QPropertyAnimation(" not in show
    assert "QParallelAnimationGroup(" not in close
    assert "QPropertyAnimation(" not in close
    assert "self._configure_motion(" in show
    assert "self._configure_motion(" in close
    assert "self._motion_group.start()" in show
    assert "self._motion_group.start()" in close
    assert "deleteLater()" not in STATIC


def test_opacity_effect_is_enabled_only_during_transitions() -> None:
    init = _body(STATIC, "def __init__", "def _label_is_passive")
    assert "self._drawer_effect.setEnabled(False)" in init

    prepare = _body(STATIC, "def _prepare_open_state", "def _show_with_animation")
    assert "self._drawer_effect.setEnabled(True)" in prepare
    assert "self._drawer_effect.setOpacity(0.0)" in prepare

    finish_open = _body(STATIC, "def _finish_open", "def request_close")
    assert finish_open.index("self._drawer_effect.setOpacity(1.0)") < finish_open.index(
        "self._drawer_effect.setEnabled(False)"
    )
    assert "self.details.drawer.repaint()" in finish_open

    close = _body(STATIC, "def request_close", "def _finish_close")
    assert "self._drawer_effect.setEnabled(True)" in close
    assert "self.details.drawer.repaint()" in close

    finish_close = _body(STATIC, "def _finish_close", "def _fallback_open")
    assert "self._drawer_effect.setEnabled(False)" in finish_close


def test_first_open_frame_is_atomic_and_pays_initial_composition_before_motion() -> None:
    prepare = _body(STATIC, "def _prepare_open_state", "def _show_with_animation")
    assert "self.details._capture_backdrop()" in prepare
    assert "self.details.drawer.setGeometry(target)" in prepare
    assert "self.details.body_layout.activate()" in prepare
    assert "self.details.drawer.move(start_pos)" in prepare
    assert "self.details.backdrop.show()" in prepare
    assert "self.details.scrim.show()" in prepare
    assert "self.details.drawer.show()" in prepare
    assert "self.root.repaint()" in prepare
    assert prepare.index("self._drawer_effect.setOpacity(0.0)") < prepare.index(
        "self.details.drawer.show()"
    )


def test_close_reverses_from_current_real_drawer_state_without_handoff() -> None:
    configure = _body(STATIC, "def _configure_motion", "def _stop_animation")
    assert "self.details.drawer.pos()" in configure
    assert "self._drawer_effect.opacity()" in configure

    close = _body(STATIC, "def request_close", "def _finish_close")
    assert "self._stop_animation()" in close
    assert "self.details.drawer.hide()" not in close
    assert "drawer.grab()" not in close


def test_geometry_sync_cannot_fight_drawer_position_animation() -> None:
    assert "self._original_schedule_geometry = self.details._schedule_geometry" in STATIC
    assert "self.details._schedule_geometry = self._schedule_geometry_guarded" in STATIC
    guard = _body(STATIC, "def _schedule_geometry_guarded", "def _set_motion_active")
    assert "_STATE_OPENING" in guard
    assert "_STATE_CLOSING" in guard
    assert "self._geometry_sync_pending = True" in guard
    assert "self._original_schedule_geometry()" in guard

    motion = _body(STATIC, "def _set_motion_active", "def _configure_property_animation")
    assert "timer.stop()" in motion
    assert "self._geometry_sync_pending" in motion
    assert "self._original_schedule_geometry()" in motion


def test_all_obscured_underlay_animation_is_suspended_for_modal_lifetime() -> None:
    suspend = _body(STATIC, "def _suspend_underlay", "def _resume_underlay")
    assert 'getattr(self.window, "_nekro_card_fx", None)' in suspend
    assert 'getattr(card_fx, "suspend_for_modal", None)' in suspend
    assert 'getattr(self.window, "_nekro_effects", None)' in suspend
    assert 'getattr(effects, "timer", None)' in suspend
    assert "effects_timer.stop()" in suspend
    assert 'getattr(self.background, "_pointer_timer", None)' in suspend
    assert "timer.stop()" in suspend
    assert 'quick.setProperty("animationRunning", False)' in suspend

    resume = _body(STATIC, "def _resume_underlay", "def _schedule_geometry_guarded")
    assert 'getattr(card_fx, "resume_from_modal", None)' in resume
    assert "self.background._last_pointer_norm = None" in resume
    assert "effects_timer.start()" in resume
    assert "timer.start()" in resume

    show = _body(STATIC, "def _show_with_animation", "def _finish_open")
    assert show.index("self._suspend_underlay()") < show.index("self._prepare_open_state")
    finish_open = _body(STATIC, "def _finish_open", "def request_close")
    assert "self._resume_underlay()" not in finish_open
    finish_close = _body(STATIC, "def _finish_close", "def _fallback_open")
    assert "self._resume_underlay()" in finish_close


def test_close_paths_are_rewired_but_escape_stays_on_shared_detail_controller() -> None:
    assert "self._original_close = self.details.close" in STATIC
    assert "self.details.close = self.request_close" in STATIC
    assert "self.details.close_button.clicked.connect(self.request_close)" in STATIC
    assert "self.details.scrim.clicked.connect(self.request_close)" in STATIC
    assert "event.key() == Qt.Key.Key_Escape" in DETAILS
    assert "self.close()" in DETAILS


def test_fail_soft_keeps_static_modal_available() -> None:
    assert "def _fallback_open" in STATIC
    assert "def _fallback_close" in STATIC
    fallback_open = _body(STATIC, "def _fallback_open", "def _fallback_close")
    assert "self._original_close()" in fallback_open
    assert "self._original_show_prepared_modal(ratio=ratio)" in fallback_open
    assert "self._drawer_effect.setEnabled(False)" in fallback_open
    fallback_close = _body(STATIC, "def _fallback_close", "def cleanup")
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
