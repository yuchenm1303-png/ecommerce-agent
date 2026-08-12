from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CARD_FX = (ROOT / "gui" / "nekro_card_fx.py").read_text(encoding="utf-8")
NATIVE_VISUAL = (ROOT / "gui" / "native_visual_style.py").read_text(encoding="utf-8")
RUN = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def _body(source: str, start: str, end: str) -> str:
    return source.split(start, 1)[1].split(end, 1)[0]


def test_runtime_uses_native_card_controller_without_monkey_patch_layer() -> None:
    assert "install_card_interaction_performance" not in RUN
    assert "card_interaction_performance" not in RUN
    assert "install_nekro_card_fx(window, visual)" in RUN
    assert "MethodType" not in CARD_FX
    assert "MethodType" not in NATIVE_VISUAL


def test_high_refresh_motion_clock_is_capped_inside_card_controller() -> None:
    assert "_MAX_MOTION_HZ = 90.0" in CARD_FX
    body = _body(CARD_FX, "def _frame_interval_ms", "def _set_content_frozen")
    assert "target_hz = max(60.0, min(_MAX_MOTION_HZ, refresh_hz))" in body
    assert "int(1000.0 / target_hz)" in body


def test_only_current_card_and_one_outgoing_motion_can_survive() -> None:
    assert "_MAX_CONCURRENT_MOTIONS = 2" in CARD_FX
    retire = _body(CARD_FX, "def _retire_stale_motions", "def _card_rect_in_window")
    assert "while len(self._moving_frames) > _MAX_CONCURRENT_MOTIONS:" in retire
    assert "protected = {frame for frame in (self.hovered, self.pressed) if frame is not None}" in retire
    assert "stale = min(candidates, key=lambda frame: self.states[frame].started_s)" in retire
    assert "state.snap(_NORMAL_SCALE, _NORMAL_ALPHA)" in retire
    assert "self._set_content_frozen(state, False)" in retire
    assert "self._moving_frames.discard(stale)" in retire


def test_current_hover_and_pressed_cards_are_always_thawed() -> None:
    hover = _body(CARD_FX, "def _hover", "def _active")
    active = _body(CARD_FX, "def _active", "def _set_hover")
    assert "self._set_content_frozen(self.states.get(frame), False)" in hover
    assert "self._set_content_frozen(self.states.get(frame), False)" in active


def test_outgoing_normal_path_freezes_only_after_ownership_changes() -> None:
    normal = _body(CARD_FX, "def _normal", "def _hover")
    set_hover = _body(CARD_FX, "def _set_hover", "def _begin_press")
    begin_press = _body(CARD_FX, "def _begin_press", "def _end_press")

    assert "frame is not self.hovered and frame is not self.pressed" in normal
    assert "self._set_content_frozen(state, True)" in normal
    assert set_hover.index("self.hovered = frame") < set_hover.index("self._normal(previous)")
    assert begin_press.index("self.hovered = frame") < begin_press.index("self._normal(previous_hover)")
    assert begin_press.index("self.pressed = frame") < begin_press.index("self._normal(previous_hover)")


def test_live_widget_raster_budget_is_native_to_effect_boundary() -> None:
    assert "_CONTENT_EDGE_STEP_PX = 0.18" in NATIVE_VISUAL
    assert "_NORMAL_SCALE_EPSILON = 1e-5" in NATIVE_VISUAL
    set_scale = _body(NATIVE_VISUAL, "def set_scale", "def boundingRectFor")
    assert "edge_delta_px = self._content_span() * abs(requested - self._scale) * 0.5" in set_scale
    assert "if edge_delta_px < _CONTENT_EDGE_STEP_PX:" in set_scale
    assert "return" in set_scale
    assert "exact_rest = abs(requested - 1.0) <= _NORMAL_SCALE_EPSILON" in set_scale


def test_live_current_fresh_composite_and_frozen_outgoing_share_one_effect() -> None:
    effect = _body(NATIVE_VISUAL, "class _CardScaleEffect", "class NativeGlassProxy")
    composite = _body(effect, "def _current_composite", "def draw")
    assert "self.sourcePixmap(" in composite
    assert "return self._frozen_source, self._frozen_offset" in composite
    assert "if self._frozen:" in composite
    assert "self._freeze_requested = False" in composite
    assert "_cached_source" not in effect


def test_modal_and_cleanup_paths_thaw_every_card() -> None:
    suspend = _body(CARD_FX, "def suspend_for_modal", "def resume_from_modal")
    resume = _body(CARD_FX, "def resume_from_modal", "def _cleanup")
    cleanup = _body(CARD_FX, "def _cleanup", "def install_nekro_card_fx")
    for body in (suspend, resume, cleanup):
        assert "self._set_content_frozen(state, False)" in body
        assert "state.snap(_NORMAL_SCALE, _NORMAL_ALPHA)" in body


def test_native_performance_sources_compile_without_importing_pyside() -> None:
    compile(CARD_FX, str(ROOT / "gui" / "nekro_card_fx.py"), "exec")
    compile(NATIVE_VISUAL, str(ROOT / "gui" / "native_visual_style.py"), "exec")
    compile(RUN, str(ROOT / "run_local_gui.py"), "exec")
