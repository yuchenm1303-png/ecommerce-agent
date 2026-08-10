from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CARD_FX = (ROOT / "gui" / "nekro_card_fx.py").read_text(encoding="utf-8")
NATIVE_VISUAL = (ROOT / "gui" / "native_visual_style.py").read_text(encoding="utf-8")


def _body(source: str, start: str, end: str) -> str:
    return source.split(start, 1)[1].split(end, 1)[0]


def test_card_interaction_watches_the_entire_widget_subtree() -> None:
    assert "self._watched_to_card: dict[QObject, QFrame]" in CARD_FX
    assert "def _nearest_card" in CARD_FX
    assert "def _watch_widget" in CARD_FX
    assert "def _register_widget_tree" in CARD_FX
    assert "root.findChildren(QWidget)" in CARD_FX
    assert "widget.setMouseTracking(True)" in CARD_FX
    assert "widget.installEventFilter(self)" in CARD_FX


def test_card_feedback_is_a_direct_three_state_machine_without_timer_latency() -> None:
    assert "_NORMAL_ALPHA = 64.0" in CARD_FX
    assert "_HOVER_ALPHA = 90.0" in CARD_FX
    assert "_ACTIVE_ALPHA = 110.0" in CARD_FX
    assert "QTimer" not in CARD_FX
    assert "time.monotonic" not in CARD_FX
    assert "_css_ease" not in CARD_FX
    assert "_ANIMATION_FRAME_MS" not in CARD_FX
    assert "_HOVER_SECONDS" not in CARD_FX
    assert "_PRESS_SECONDS" not in CARD_FX
    assert "_RELEASE_SECONDS" not in CARD_FX


def test_enter_and_mouse_move_publish_hover_immediately() -> None:
    event_filter = _body(CARD_FX, "def eventFilter", "def _cleanup")
    assert "QEvent.Type.Enter" in event_filter
    assert "QEvent.Type.MouseMove" in event_filter
    assert "self._enter(frame)" in event_filter

    enter = _body(CARD_FX, "def _enter", "def _leave")
    assert "self._publish(frame, _HOVER_ALPHA)" in enter


def test_every_press_and_release_forms_one_complete_click_pulse() -> None:
    press = _body(CARD_FX, "def _press", "def _release")
    release = _body(CARD_FX, "def _release", "def _reset_card")

    assert "self._publish(frame, _ACTIVE_ALPHA)" in press
    assert "self._publish(frame, _HOVER_ALPHA)" in release
    assert "self._publish(frame, _NORMAL_ALPHA)" in release
    assert "_animate" not in press
    assert "_animate" not in release


def test_child_leave_only_clears_hover_after_pointer_leaves_whole_card() -> None:
    event_filter = _body(CARD_FX, "def eventFilter", "def _cleanup")
    assert "QEvent.Type.Leave" in event_filter
    assert "if not self._cursor_inside_card(frame):" in event_filter
    assert "self._leave(frame)" in event_filter
    assert "frame.mapFromGlobal(QCursor.pos())" in CARD_FX


def test_dynamic_card_children_are_registered_without_consuming_events() -> None:
    event_filter = _body(CARD_FX, "def eventFilter", "def _cleanup")
    assert "QEvent.Type.ChildAdded" in event_filter
    assert "self._register_widget_tree(child)" in event_filter
    assert event_filter.rstrip().endswith("return False")


def test_modal_resume_reconciles_hover_with_current_cursor() -> None:
    assert "def suspend_for_modal" in CARD_FX
    resume = _body(CARD_FX, "def resume_from_modal", "def eventFilter")
    assert "self._cursor_inside_card(frame)" in resume
    assert "state.snap(_HOVER_ALPHA)" in resume
    assert "state.snap(_NORMAL_ALPHA)" in resume


def test_interaction_tint_is_local_mouse_transparent_qwidget() -> None:
    assert "class _CardInteractionTint(QWidget)" in NATIVE_VISUAL
    tint = _body(NATIVE_VISUAL, "class _CardInteractionTint", "class NativeGlassProxy")
    assert "WA_TransparentForMouseEvents" in tint
    assert "WA_TranslucentBackground" in tint
    assert "self.lower()" in tint
    assert "self.repaint()" in tint
    assert "QColor(0, 0, 0, self._alpha)" in tint
    assert "drawRoundedRect" in tint


def test_local_tint_preserves_exact_composed_90_and_110_visual_targets() -> None:
    assert "def _interaction_overlay_alpha" in NATIVE_VISUAL
    helper = _body(
        NATIVE_VISUAL,
        "def _interaction_overlay_alpha",
        "class _CardInteractionTint",
    )
    assert "255.0 * (target - _NORMAL_GLASS_ALPHA) / denominator" in helper
    assert "_NORMAL_GLASS_ALPHA = 64.0" in NATIVE_VISUAL


def test_high_frequency_interaction_never_crosses_into_quick_model() -> None:
    setter = _body(NATIVE_VISUAL, "def set_interaction", "def sync_geometry")
    assert "self._interaction_tint.set_target_alpha(overlay_alpha)" in setter
    assert "self.background.set_card_alpha" not in setter
    assert "quick.requestUpdate" not in setter
    assert "dataChanged" not in setter


def test_quick_model_keeps_stable_base_glass_alpha() -> None:
    assert '"cardAlpha": _NORMAL_GLASS_ALPHA' in NATIVE_VISUAL
    assert "self._glass[frame] = NativeGlassProxy(frame, self.background)" in NATIVE_VISUAL


def test_sources_compile_without_importing_pyside() -> None:
    compile(CARD_FX, str(ROOT / "gui" / "nekro_card_fx.py"), "exec")
    compile(NATIVE_VISUAL, str(ROOT / "gui" / "native_visual_style.py"), "exec")
