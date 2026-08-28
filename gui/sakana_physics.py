from __future__ import annotations

from dataclasses import dataclass


FRAME_MILLISECONDS = 1000.0 / 60.0
FAST_FRAME_CUTOFF_MILLISECONDS = 16.0
STOP_THRESHOLD = 0.1
ORIGIN_ROTATE = 0.0


@dataclass
class SakanaSpringState:
    """Sakana Widget spring state using the upstream Takina defaults."""

    i: float = 0.08
    s: float = 0.10
    d: float = 0.988
    r: float = 12.0
    y: float = 2.0
    t: float = 0.0
    w: float = 0.0


def move_spring(
    state: SakanaSpringState,
    x: float,
    y: float,
    *,
    max_rotation: float,
    max_y: float,
    min_y: float,
) -> None:
    """Mirror Sakana Widget's `_move(x, y)` drag mapping exactly."""

    rotation = x * state.s
    rotation = max(-max_rotation, rotation)
    rotation = min(max_rotation, rotation)

    height = y * state.s * 2.0
    height = max(min_y, height)
    height = min(max_y, height)

    state.r = rotation
    state.y = height
    state.w = 0.0
    state.t = 0.0


def advance_spring(
    state: SakanaSpringState,
    elapsed_milliseconds: float,
    *,
    origin_rotate: float = ORIGIN_ROTATE,
    threshold: float = STOP_THRESHOLD,
) -> bool:
    """Advance one Sakana Widget animation frame.

    Returns True while the upstream implementation would request another
    animation frame. The equations, sub-16 ms inertia scaling, damping order and
    stop test intentionally match the pcp.moe Sakana bundle one-for-one.
    """

    inertia = state.i
    elapsed_milliseconds = max(0.0, float(elapsed_milliseconds))
    if elapsed_milliseconds < FAST_FRAME_CUTOFF_MILLISECONDS:
        inertia = state.i / FRAME_MILLISECONDS * elapsed_milliseconds

    # Upstream horizontal spring is independent of vertical velocity. In
    # particular, this is `w = w - r * 2 - originRotate`, not `... - t`.
    w = state.w - state.r * 2.0 - origin_rotate
    state.r = state.r + w * inertia * 1.2
    state.w = w * state.d

    t = state.t - state.y * 2.0
    state.y = state.y + t * inertia * 2.0
    state.t = t * state.d

    return max(abs(state.w), abs(state.r), abs(state.t), abs(state.y)) >= threshold
