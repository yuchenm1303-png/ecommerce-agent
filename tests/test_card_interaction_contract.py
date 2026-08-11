from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CARD_FX = (ROOT / "gui" / "nekro_card_fx.py").read_text(encoding="utf-8")
NATIVE_VISUAL = (ROOT / "gui" / "native_visual_style.py").read_text(encoding="utf-8")
NATIVE_BG = (ROOT / "gui" / "native_background.py").read_text(encoding="utf-8")


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
    assert expected in NATIVE_BG
    assert "for frame in window.findChildren(QFrame):" in CARD_FX


def test_quick_glass_and_widget_content_share_one_scale_state() -> None:
    assert "SCALE_ROLE = _ROLE_BASE + 11" in NATIVE_BG
    assert 'SCALE_ROLE: "cardScale"' in NATIVE_BG
    assert 'self.SCALE_ROLE: QByteArray(b"cardScale")' in NATIVE_BG
    assert '"cardScale": 1.0' in NATIVE_BG
    assert "scale: cardScale" in NATIVE_BG
    assert "transformOrigin: Item.Center" in NATIVE_BG

    proxy = _body(NATIVE_VISUAL, "class NativeGlassProxy", "class NativeVisualStyleController")
    assert "self.background.set_card_presentation(" in proxy
    assert "scale=scale" in proxy
    assert "alpha=overlay_alpha" in proxy
    assert "self._scale_effect.set_scale(scale)" in proxy


def test_quick_owns_glass_darkening_without_extra_qwidget_tint_layer() -> None:
    assert "class _CardInteractionTint" not in NATIVE_VISUAL
    assert "_interaction_overlay_alpha" not in NATIVE_VISUAL
    assert "nativeCardInteractionTint" not in NATIVE_VISUAL
    assert "QColor" not in NATIVE_VISUAL

    model = _body(NATIVE_BG, "class GlassCardModel", "def _qml_source")
    assert "def set_presentation" in model
    assert "changed_roles.append(self.SCALE_ROLE)" in model
    assert "changed_roles.append(self.ALPHA_ROLE)" in model
    assert "self.dataChanged.emit(index, index, changed_roles)" in model
    assert "cardAlpha / 255.0" in NATIVE_BG


def test_complete_widget_content_subtree_gets_same_transform_without_layout_resize() -> None:
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
    assert "self._scale_effect.set_scale(scale)" in proxy


def test_steady_state_disables_widget_content_scale_effect() -> None:
    effect = _body(NATIVE_VISUAL, "class _CardScaleEffect", "class NativeGlassProxy")
    assert "self.setEnabled(False)" in effect
    assert "active = abs(scale - 1.0) > 1e-4" in effect
    assert "self.setEnabled(active)" in effect


def test_high_frequency_interaction_updates_only_one_quick_model_row_not_global_mask() -> None:
    setter = _body(NATIVE_VISUAL, "def set_interaction", "def sync_geometry")
    assert "self.background.set_card_presentation(" in setter
    assert "self._scale_effect.set_scale(scale)" in setter
    assert "schedule_mask_update" not in setter
    assert "quick.requestUpdate" not in setter

    presentation = _body(NATIVE_BG, "def set_card_presentation", "def _sample_pointer")
    assert "self.card_model.set_presentation(frame, scale=scale, alpha=alpha)" in presentation
    assert "schedule_mask_update" not in presentation


def test_static_blur_mask_can_reflect_current_scale_when_geometry_refreshes() -> None:
    render_mask = _body(NATIVE_BG, "def render_mask", "def _qml_source")
    assert 'scale = float(state.get("cardScale", 1.0))' in render_mask
    assert "half_w = card.width() * scale * 0.5" in render_mask
    assert "half_h = card.height() * scale * 0.5" in render_mask


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
    compile(NATIVE_BG, str(ROOT / "gui" / "native_background.py"), "exec")
