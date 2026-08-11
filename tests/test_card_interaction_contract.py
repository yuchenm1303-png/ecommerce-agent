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


def test_motion_clock_touches_only_cards_that_are_actually_animating() -> None:
    assert "self._moving_frames: set[QFrame] = set()" in CARD_FX
    advance = _body(CARD_FX, "def _advance_motions", "def _animate_to")
    assert "for frame in tuple(self._moving_frames):" in advance
    assert "for state in self.states.values():" not in advance
    assert "self._moving_frames.discard(frame)" in advance
    animate = _body(CARD_FX, "def _animate_to", "def _normal")
    assert "self._moving_frames.add(frame)" in animate


def test_static_hover_and_held_press_avoid_discarded_full_window_hit_tests() -> None:
    assert "def _hover_still_owns_global" in CARD_FX
    hover_owner = _body(CARD_FX, "def _hover_still_owns_global", "def _advance_state")
    assert "frame.mapFromGlobal(global_pos)" in hover_owner
    assert "frame.childAt(local)" in hover_owner
    assert "nested is None or nested is frame" in hover_owner

    sample = _body(CARD_FX, "def _sample_pointer", "def suspend_for_modal")
    held_guard = "if left_down and self._left_down and self.pressed is not None:"
    assert held_guard in sample
    assert sample.index(held_guard) < sample.index("current = self._card_at_global(global_pos)")
    assert "self._hover_still_owns_global(self.hovered, global_pos)" in sample


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


def test_large_cards_normalize_hover_by_reference_edge_growth_and_real_clearance() -> None:
    assert "_REFERENCE_CARD_SPAN_PX = 300.0" in CARD_FX
    assert "_REFERENCE_EDGE_GROWTH_PX" in CARD_FX
    assert "_MIN_NEIGHBOR_GAP_PX = 1.0" in CARD_FX
    assert "_WINDOW_EDGE_GAP_PX = 1.0" in CARD_FX
    assert "def _card_rect_in_window" in CARD_FX
    assert "def _available_edge_growth" in CARD_FX
    assert "def _hover_scale_for(self, frame: QFrame) -> float:" in CARD_FX

    clearance = _body(CARD_FX, "def _available_edge_growth", "def _hover_scale_for")
    assert "rect.left() - _WINDOW_EDGE_GAP_PX" in clearance
    assert "window_w - rect.right() - _WINDOW_EDGE_GAP_PX" in clearance
    assert "window_h - rect.bottom() - _WINDOW_EDGE_GAP_PX" in clearance
    assert "frame.isAncestorOf(other)" in clearance
    assert "other.isAncestorOf(frame)" in clearance
    assert "horizontal_overlap" in clearance
    assert "vertical_overlap" in clearance
    assert "gap - _MIN_NEIGHBOR_GAP_PX" in clearance

    normalizer = _body(CARD_FX, "def _hover_scale_for", "def _nearest_card")
    assert "max(1.0, float(frame.width()), float(frame.height()))" in normalizer
    assert "reference_growth = min(" in normalizer
    assert "self._available_edge_growth(frame, reference_growth)" in normalizer
    assert "2.0 * growth / span" in normalizer
    assert "min(_HOVER_SCALE, normalized)" in normalizer

    hover = _body(CARD_FX, "def _hover", "def _active")
    assert "scale=self._hover_scale_for(frame)" in hover


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


def test_hover_shell_uses_window_coordinates_without_internal_clip_regions() -> None:
    qml = _body(NATIVE_BG, "def _qml_source", "class NativeQuickBackground")
    repeater = qml.split("Repeater {{", 1)[1].split("FrameAnimation {{", 1)[0]
    assert "x: 0" in repeater
    assert "y: 0" in repeater
    assert "width: root.width" in repeater
    assert "height: root.height" in repeater
    assert "clip: false" in repeater
    assert "x: cardX" in repeater
    assert "y: cardY" in repeater
    assert "x: clipX" not in repeater
    assert "y: clipY" not in repeater
    assert "width: clipW" not in repeater
    assert "height: clipH" not in repeater


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


def test_card_effect_uses_fixed_active_bounds_instead_of_per_frame_geometry_churn() -> None:
    effect = _body(NATIVE_VISUAL, "class _CardScaleEffect", "class NativeGlassProxy")
    assert "_EFFECT_BOUND_SCALE = 1.04" in NATIVE_VISUAL
    assert "if self.isEnabled() != active:" in effect
    active_block = effect.split("if self.isEnabled() != active:", 1)[1].split("self.update()", 1)[0]
    assert "self.setEnabled(active)" in active_block
    assert "self.updateBoundingRect()" in active_block
    assert effect.count("self.updateBoundingRect()") == 1
    assert "source_rect.width() * _EFFECT_BOUND_SCALE" in effect
    assert "source_rect.height() * _EFFECT_BOUND_SCALE" in effect


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


def test_global_blur_mask_remains_geometry_only_during_hover() -> None:
    render_mask = _body(NATIVE_BG, "def render_mask", "def _qml_source")
    assert 'state.get("cardScale"' not in render_mask
    assert "painter.drawRoundedRect(card, _GLASS_RADIUS, _GLASS_RADIUS)" in render_mask


def test_modal_suspend_returns_cards_to_exact_normal_state() -> None:
    suspend = _body(CARD_FX, "def suspend_for_modal", "def resume_from_modal")
    resume = _body(CARD_FX, "def resume_from_modal", "def _cleanup")
    assert "self._pointer_timer.stop()" in suspend
    assert "self._motion_timer.stop()" in suspend
    assert "self._moving_frames.clear()" in suspend
    assert "state.snap(_NORMAL_SCALE, _NORMAL_ALPHA)" in suspend
    assert "state.snap(_NORMAL_SCALE, _NORMAL_ALPHA)" in resume
    assert "current = self._card_at_global(QCursor.pos())" in resume
    assert "self._pointer_timer.start()" in resume


def test_sources_compile_without_importing_pyside() -> None:
    compile(CARD_FX, str(ROOT / "gui" / "nekro_card_fx.py"), "exec")
    compile(NATIVE_VISUAL, str(ROOT / "gui" / "native_visual_style.py"), "exec")
    compile(NATIVE_BG, str(ROOT / "gui" / "native_background.py"), "exec")
