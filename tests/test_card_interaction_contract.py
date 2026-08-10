from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CARD_FX = (ROOT / "gui" / "nekro_card_fx.py").read_text(encoding="utf-8")
NATIVE_VISUAL = (ROOT / "gui" / "native_visual_style.py").read_text(encoding="utf-8")


def _body(source: str, start: str, end: str) -> str:
    return source.split(start, 1)[1].split(end, 1)[0]


def test_card_feedback_keeps_simple_three_state_visuals() -> None:
    assert "_NORMAL_ALPHA = 64.0" in CARD_FX
    assert "_HOVER_ALPHA = 90.0" in CARD_FX
    assert "_ACTIVE_ALPHA = 110.0" in CARD_FX
    assert "_css_ease" not in CARD_FX
    assert "_HOVER_SECONDS" not in CARD_FX
    assert "_PRESS_SECONDS" not in CARD_FX
    assert "_RELEASE_SECONDS" not in CARD_FX


def test_one_pointer_sampler_is_the_authoritative_hover_fallback() -> None:
    assert "_POINTER_SAMPLE_MS = 8" in CARD_FX
    assert "self._pointer_timer = QTimer(self)" in CARD_FX
    assert "self._pointer_timer.setTimerType(Qt.TimerType.PreciseTimer)" in CARD_FX
    assert "self._pointer_timer.setInterval(_POINTER_SAMPLE_MS)" in CARD_FX
    assert "self._pointer_timer.timeout.connect(self._sample_pointer)" in CARD_FX
    assert "def _card_at_global" in CARD_FX
    assert "QApplication.widgetAt(global_pos)" in CARD_FX
    assert "self._card_at_global(QCursor.pos())" in CARD_FX


def test_existing_widget_events_are_only_zero_latency_resample_hints() -> None:
    event_filter = _body(CARD_FX, "def eventFilter", "def _cleanup")
    assert "QEvent.Type.Enter" in event_filter
    assert "QEvent.Type.MouseMove" in event_filter
    assert "QEvent.Type.Leave" in event_filter
    assert "self._sample_pointer()" in event_filter
    assert "self._begin_press(self._card_at_global(QCursor.pos()))" in event_filter
    assert "self._end_press()" in event_filter
    assert event_filter.rstrip().endswith("return False")


def test_fast_press_release_cannot_collapse_before_one_presented_frame() -> None:
    assert "_MIN_PRESSED_MS = 24" in CARD_FX
    assert "self._press_clock = QElapsedTimer()" in CARD_FX
    assert "self._release_timer = QTimer(self)" in CARD_FX
    assert "self._release_timer.setSingleShot(True)" in CARD_FX
    assert "self._release_timer.setTimerType(Qt.TimerType.PreciseTimer)" in CARD_FX

    begin = _body(CARD_FX, "def _begin_press", "def _end_press")
    end = _body(CARD_FX, "def _end_press", "def _finish_release")
    finish = _body(CARD_FX, "def _finish_release", "def _sample_pointer")
    assert "self._press_clock.start()" in begin
    assert "self._publish(frame, _ACTIVE_ALPHA)" in begin
    assert "_MIN_PRESSED_MS - int(elapsed)" in end
    assert "self._release_timer.start(remaining)" in end
    assert "self._publish(current, _HOVER_ALPHA)" in finish


def test_subtree_events_are_preserved_without_becoming_pointer_truth() -> None:
    assert "self._watched_to_card: dict[QObject, QFrame]" in CARD_FX
    assert "def _nearest_card" in CARD_FX
    assert "def _watch_widget" in CARD_FX
    assert "def _register_widget_tree" in CARD_FX
    assert "root.findChildren(QWidget)" in CARD_FX
    assert "widget.setMouseTracking(True)" in CARD_FX
    assert "widget.installEventFilter(self)" in CARD_FX
    assert "QEvent.Type.ChildAdded" in CARD_FX


def test_modal_suspend_stops_pointer_work_and_resume_reconciles_immediately() -> None:
    suspend = _body(CARD_FX, "def suspend_for_modal", "def resume_from_modal")
    resume = _body(CARD_FX, "def resume_from_modal", "def eventFilter")
    assert "self._pointer_timer.stop()" in suspend
    assert "self._release_timer.stop()" in suspend
    assert "state.republish()" in suspend
    assert "current = self._card_at_global(QCursor.pos())" in resume
    assert "state.snap(_HOVER_ALPHA if frame is current else _NORMAL_ALPHA)" in resume
    assert "self._pointer_timer.start()" in resume


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
