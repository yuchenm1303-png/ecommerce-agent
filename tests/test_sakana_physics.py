from gui.sakana_physics import (
    FRAME_MILLISECONDS,
    SakanaSpringState,
    advance_spring,
    move_spring,
)


def _reference_step(state: SakanaSpringState, elapsed_ms: float) -> bool:
    inertia = state.i
    if elapsed_ms < 16.0:
        inertia = state.i / (1000.0 / 60.0) * elapsed_ms
    w = state.w - 2.0 * state.r
    state.r += w * inertia * 1.2
    state.w = w * state.d
    t = state.t - 2.0 * state.y
    state.y += t * inertia * 2.0
    state.t = t * state.d
    return max(abs(state.w), abs(state.r), abs(state.t), abs(state.y)) >= 0.1


def test_takina_defaults_match_source_bundle() -> None:
    assert SakanaSpringState() == SakanaSpringState(
        i=0.08, s=0.1, d=0.988, r=12.0, y=2.0, t=0.0, w=0.0
    )


def test_drag_mapping_matches_source_bundle() -> None:
    state = SakanaSpringState()
    move_spring(state, 500.0, -400.0, max_rotation=36.0, max_y=45.0, min_y=-45.0)
    assert state.r == 36.0
    assert state.y == -45.0
    assert state.w == 0.0
    assert state.t == 0.0


def test_frame_equations_match_source_bundle() -> None:
    for elapsed_ms in (8.0, 15.5, 16.0, FRAME_MILLISECONDS, 25.0):
        actual = SakanaSpringState(r=21.0, y=-13.0, t=7.5, w=-4.0)
        expected = SakanaSpringState(r=21.0, y=-13.0, t=7.5, w=-4.0)
        actual_running = advance_spring(actual, elapsed_ms)
        expected_running = _reference_step(expected, elapsed_ms)
        assert actual == expected
        assert actual_running == expected_running


def test_horizontal_spring_does_not_use_vertical_velocity() -> None:
    a = SakanaSpringState(r=10.0, y=0.0, t=100.0, w=0.0)
    b = SakanaSpringState(r=10.0, y=0.0, t=-100.0, w=0.0)
    advance_spring(a, 16.0)
    advance_spring(b, 16.0)
    assert a.r == b.r
    assert a.w == b.w


def test_stop_condition_uses_absolute_state_not_frame_delta() -> None:
    state = SakanaSpringState(r=0.01, y=0.01, t=0.01, w=0.01)
    assert advance_spring(state, 16.0) is False
