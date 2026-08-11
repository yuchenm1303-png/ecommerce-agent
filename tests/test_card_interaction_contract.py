from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CARD_FX = (ROOT / "gui" / "nekro_card_fx.py").read_text(encoding="utf-8")
NATIVE_VISUAL = (ROOT / "gui" / "native_visual_style.py").read_text(encoding="utf-8")


def _body(source: str, start: str, end: str) -> str:
    return source.split(start, 1)[1].split(end, 1)[0]


def test_reference_website_list_card_visual_states_are_exact() -> None:
    assert "_NORMAL_SCALE = 1.00" in CARD_FX
    assert "_HOVER_SCALE = 1.02" in CARD_FX
    assert "_ACTIVE_SCALE = 1.00" in CARD_FX
    assert "_NORMAL_ALPHA = 64.0" in CARD_FX
    assert "_HOVER_ALPHA = 102.0" in CARD_FX
    assert "_ACTIVE_ALPHA = 102.0" in CARD_FX
    assert "_TRANSITION_MS = 300" in CARD_FX
    assert "cubic-bezier(.25, .1, .25, 1)" in CARD_FX


def test_browser_like_motion_reverses_from_current_interpolated_state() -> None:
    assert "def _advance_state" in CARD_FX
    animate = _body(CARD_FX, "def _animate_to", "def _normal")
    assert "self._advance_state(state, now_s)" in animate
    assert "state.from_scale = state.current_scale" in animate
    assert "state.from_alpha = state.current_alpha" in animate
    assert "state.target_scale = scale" in animate
    assert "state.target_alpha = alpha" in animate
    assert "_MIN_PRESSED_MS" not in CARD_FX
    assert "QElapsedTimer" not in CARD_FX
    assert "_release_timer" not in CARD_FX


def test_one_pointer_sampler_remains_authoritative_without_per_child_filters() -> None:
    assert "_POINTER_SAMPLE_MS = 8" in CARD_FX
    assert "self._pointer_timer = QTimer(self)" in CARD_FX
    assert "self._pointer_timer.setTimerType(Qt.TimerType.PreciseTimer)" in CARD_FX
    assert "self._pointer_timer.setInterval(_POINTER_SAMPLE_MS)" in CARD_FX
    assert "self._pointer_timer.timeout.connect(self._sample_pointer)" in CARD_FX
    assert "def _card_at_global" in CARD_FX
    assert "self.window.childAt(local)" in CARD_FX
    assert "self._card_at_global(QCursor.pos())" in CARD_FX
    assert "widget.installEventFilter(self)" not in CARD_FX
    assert "setMouseTracking" not in CARD_FX
    assert "QMouseEvent" not in CARD_FX


def test_press_and_release_follow_reference_hover_active_semantics() -> None:
    begin = _body(CARD_FX, "def _begin_press", "def _end_press")
    end = _body(CARD_FX, "def _end_press", "def _sample_pointer")
    assert "self._active(frame)" in begin
    assert "if previous is current:" in end
    assert "self._hover(previous)" in end
    assert "self._normal(previous)" in end
    assert "self._hover(current)" in end


def test_all_big_and_small_glass_cards_use_the_same_full_interaction() -> None:
    expected = '_GLASS_NAMES = {"glassCard", "heroCard", "statusCard", "microCard"}'
    assert expected in CARD_FX
    assert expected in NATIVE_VISUAL
    assert "for frame in window.findChildren(QFrame):" in CARD_FX


def test_interaction_tint_is_local_mouse_transparent_and_continuous() -> None:
    assert "class _CardInteractionTint(QWidget)" in NATIVE_VISUAL
    tint = _body(NATIVE_VISUAL, "class _CardInteractionTint", "class _CardScaleEffect")
    assert "WA_TransparentForMouseEvents" in tint
    assert "WA_TranslucentBackground" in tint
    assert "self.lower()" in tint
    assert "self.update()" in tint
    assert "self.repaint()" not in tint
    assert "QColor(0, 0, 0, self._alpha)" in tint
    assert "drawRoundedRect" in tint


def test_local_tint_composes_reference_hover_black_alpha_102_from_base_64() -> None:
    assert "def _interaction_overlay_alpha" in NATIVE_VISUAL
    helper = _body(
        NATIVE_VISUAL,
        "def _interaction_overlay_alpha",
        "class _CardInteractionTint",
    )
    assert "255.0 * (target - _NORMAL_GLASS_ALPHA) / denominator" in helper
    assert "_NORMAL_GLASS_ALPHA = 64.0" in NATIVE_VISUAL
    assert "_HOVER_ALPHA = 102.0" in CARD_FX


def test_complete_card_subtree_gets_one_transform_without_layout_resize() -> None:
    assert "class _CardScaleEffect(QGraphicsEffect)" in NATIVE_VISUAL
    effect = _body(NATIVE_VISUAL, "class _CardScaleEffect", "class NativeGlassProxy")
    assert "def boundingRectFor" in effect
    assert "painter.translate(center)" in effect
    assert "painter.scale(scale, scale)" in effect
    assert "self.drawSource(painter)" in effect
    assert "setGeometry" not in effect
    assert ".resize(" not in effect

    proxy = _body(NATIVE_VISUAL, "class NativeGlassProxy", "class NativeVisualStyleController")
    assert "frame.setGraphicsEffect(self._scale_effect)" in proxy
    assert "self._interaction_tint.set_target_alpha(overlay_alpha)" in proxy
    assert "self._scale_effect.set_scale(scale)" in proxy


def test_steady_state_disables_card_scale_effect() -> None:
    effect = _body(NATIVE_VISUAL, "class _CardScaleEffect", "class NativeGlassProxy")
    assert "self.setEnabled(False)" in effect
    assert "active = abs(scale - 1.0) > 1e-4" in effect
    assert "self.setEnabled(active)" in effect


def test_high_frequency_interaction_never_rebuilds_quick_glass_mask() -> None:
    setter = _body(NATIVE_VISUAL, "def set_interaction", "def sync_geometry")
    assert "self._interaction_tint.set_target_alpha(overlay_alpha)" in setter
    assert "self._scale_effect.set_scale(scale)" in setter
    assert "self.background.set_card_alpha" not in setter
    assert "schedule_mask_update" not in setter
    assert "quick.requestUpdate" not in setter
    assert "dataChanged" not in setter


def test_quick_model_keeps_stable_base_glass_alpha() -> None:
    assert '"cardAlpha": _NORMAL_GLASS_ALPHA' in NATIVE_VISUAL
    assert "self._glass[frame] = NativeGlassProxy(frame, self.background)" in NATIVE_VISUAL


def test_modal_suspend_returns_cards_to_exact_normal_state() -> None:
    suspend = _body(CARD_FX, "def suspend_for_modal", "def resume_from_modal")
    resume = _body(CARD_FX, "def resume_from_modal", "def _cleanup")
    assert "self._pointer_timer.stop()" in suspend
    assert "self._motion_timer.stop()" in suspend
    assert "state.snap(_NORMAL_SCALE, _NORMAL_ALPHA)" in suspend
    assert "state.snap(_NORMAL_SCALE, _NORMAL_ALPHA)" in resume
    assert "current = self._card_at_global(QCursor.pos())" in resume
    assert "self._pointer_timer.start()" in resume


def test_sources_compile_without_importing_pyside() -> None:
    compile(CARD_FX, str(ROOT / "gui" / "nekro_card_fx.py"), "exec")
    compile(NATIVE_VISUAL, str(ROOT / "gui" / "native_visual_style.py"), "exec")
