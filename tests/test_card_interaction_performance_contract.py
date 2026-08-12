from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PERF = (ROOT / "gui" / "card_interaction_performance.py").read_text(encoding="utf-8")
RUN = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")
NATIVE_VISUAL = (ROOT / "gui" / "native_visual_style.py").read_text(encoding="utf-8")


def _body(source: str, start: str, end: str) -> str:
    return source.split(start, 1)[1].split(end, 1)[0]


def test_runtime_installs_card_perf_after_native_card_controller() -> None:
    assert "from gui.card_interaction_performance import install_card_interaction_performance" in RUN
    assert "card_fx = install_nekro_card_fx(window, visual)" in RUN
    assert "install_card_interaction_performance(window, visual, card_fx)" in RUN
    assert RUN.index("card_fx = install_nekro_card_fx(window, visual)") < RUN.index(
        "install_card_interaction_performance(window, visual, card_fx)"
    )


def test_high_refresh_card_motion_is_hard_capped_without_slowing_60hz() -> None:
    assert "_MAX_MOTION_HZ = 90.0" in PERF
    assert "_MIN_MOTION_FRAME_MS = max(1, round(1000.0 / _MAX_MOTION_HZ))" in PERF
    body = _body(PERF, "def _install_motion_rate_cap", "def _set_frame_frozen")
    assert "baseline = max(1, int(original()))" in body
    assert "return max(baseline, _MIN_MOTION_FRAME_MS)" in body
    assert "timer.setInterval(self.card_fx._frame_interval_ms())" in body


def test_only_interaction_owner_remains_live_and_outgoing_card_freezes() -> None:
    install = _body(PERF, "def _install_motion_budget", "def _effect_span")
    assert "frame is getattr(controller, \"hovered\", None)" in install
    assert 'controller, "pressed", None' in install
    assert "performance._set_frame_frozen(frame, False)" in install
    assert "performance._should_freeze_outgoing(frame, controller)" in install
    assert "performance._set_frame_frozen(frame, True)" in install
    assert install.index("performance._set_frame_frozen(frame, False)") < install.index(
        "original(frame, scale=scale, alpha=alpha)"
    )


def test_freeze_policy_never_freezes_hovered_or_pressed_card() -> None:
    policy = _body(PERF, "def _should_freeze_outgoing", "def _retire_stale_motions")
    assert 'frame is getattr(controller, "hovered", None)' in policy
    assert 'frame is getattr(controller, "pressed", None)' in policy
    assert "return False" in policy
    assert 'getattr(state, "moving", False)' in policy
    assert 'getattr(state, "current_scale", _NORMAL_SCALE)' in policy


def test_rapid_cross_card_traversal_keeps_at_most_current_plus_previous() -> None:
    assert "_MAX_CONCURRENT_MOTIONS = 2" in PERF
    retire = _body(PERF, "def _retire_stale_motions", "def _install_motion_budget")
    assert "while len(moving) > _MAX_CONCURRENT_MOTIONS:" in retire
    assert "protected = {frame for frame in (hovered, pressed) if frame is not None}" in retire
    assert "state.snap(_NORMAL_SCALE, _NORMAL_ALPHA)" in retire
    assert "self._set_frame_frozen(stale, False)" in retire
    assert "moving.discard(stale)" in retire


def test_widget_live_rasterization_is_quantized_by_subpixel_edge_motion() -> None:
    assert "_CONTENT_EDGE_STEP_PX = 0.18" in PERF
    assert "_NORMAL_SCALE_EPSILON = 1e-5" in PERF

    span = _body(PERF, "def _effect_span", "def _install_content_scale_quantization")
    assert "effect.parent()" in span
    assert "float(frame.width())" in span
    assert "float(frame.height())" in span

    body = _body(PERF, "def _install_content_scale_quantization", "def _cleanup")
    assert "current = float(getattr(effect_self, \"scale\", _NORMAL_SCALE))" in body
    assert "span = performance._effect_span(effect_self)" in body
    assert "edge_delta_px = span * abs(requested - current) * 0.5" in body
    assert "if edge_delta_px < _CONTENT_EDGE_STEP_PX:" in body
    assert "requested = _NORMAL_SCALE" in body
    assert "_original(requested)" in body


def test_performance_layer_does_not_intercept_child_widgets_or_layout() -> None:
    assert "installEventFilter" not in PERF
    assert "findChildren" not in PERF
    assert "setGeometry" not in PERF
    assert ".resize(" not in PERF
    assert "QApplication" not in PERF
    assert "QCursor" not in PERF


def test_native_effect_supports_live_and_outgoing_modes_without_global_cache() -> None:
    effect = _body(NATIVE_VISUAL, "class _CardScaleEffect", "class NativeGlassProxy")
    assert "def set_frozen" in effect
    assert "def _current_composite" in effect
    assert "self.sourcePixmap(" in effect
    assert "self._frozen_source" in effect
    assert "_cached_source" not in effect
    assert "findChildren(" not in effect


def test_cleanup_thaws_all_remaining_outgoing_cards() -> None:
    cleanup = _body(PERF, "def _cleanup", "def install_card_interaction_performance")
    assert "for frame in tuple(self._effects_by_frame):" in cleanup
    assert "self._set_frame_frozen(frame, False)" in cleanup
    assert "self._effects_by_frame.clear()" in cleanup


def test_performance_source_compiles_without_importing_pyside() -> None:
    compile(
        PERF,
        str(ROOT / "gui" / "card_interaction_performance.py"),
        "exec",
    )
    compile(RUN, str(ROOT / "run_local_gui.py"), "exec")
