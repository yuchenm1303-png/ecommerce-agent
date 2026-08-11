from __future__ import annotations

"""Presentation-only motion profile for the stable Single/Batch transition.

The transition implementation itself is intentionally left untouched.  Keeping
all duration tuning here means visual pacing can be adjusted without reopening
the snapshot / Quick / glass ownership code that has already been stabilized on
Windows.
"""

from . import workspace_transition as _transition


# Approved slower profile.  The 390 ms choreography is stretched to 480 ms while
# preserving its relative exit -> clean Fuji handoff -> enter rhythm.  The tiny
# 300 ms mode switch remains unchanged so input acknowledgement still feels fast.
_WORKSPACE_MOTION_PROFILE: dict[str, int] = {
    "_HOLD_MS": 50,
    "_EXIT_END_MS": 190,
    "_ENTER_START_MS": 215,
    "_TOTAL_MS": 480,
    "_ENTER_DURATION_MS": 265,
    "_HEADER_EXIT_START_MS": 55,
    "_HEADER_EXIT_END_MS": 155,
    "_HEADER_ENTER_START_MS": 185,
    "_HEADER_ENTER_END_MS": 330,
    "_VEIL_START_MS": 165,
    "_VEIL_PEAK_MS": 210,
    "_VEIL_END_MS": 270,
}


def apply_workspace_transition_tuning() -> None:
    """Apply timing tokens only; never replace transition drawing behavior."""

    for name, value in _WORKSPACE_MOTION_PROFILE.items():
        setattr(_transition, name, int(value))
