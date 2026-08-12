from __future__ import annotations

from types import MethodType
from typing import Any

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QFrame, QMainWindow


# The reference interaction lasts 300 ms, but QWidget subtree rasterization is
# much more expensive than a browser compositor transform. Keep the motion clock
# smooth while putting a hard ceiling on Python -> QWidget -> QML churn on high
# refresh-rate displays.
_MAX_MOTION_HZ = 90.0
_MIN_MOTION_FRAME_MS = max(1, round(1000.0 / _MAX_MOTION_HZ))

# During rapid A -> B -> C traversal, browser CSS lets every old card finish its
# transition. That is cheap in a browser compositor but pathological here because
# each active QWidget effect may rasterize a large subtree. Only the current card
# and one immediately previous card are visually useful; older outgoing motions
# are stale and can be snapped back to rest.
_MAX_CONCURRENT_MOTIONS = 2
_NORMAL_SCALE = 1.0
_NORMAL_ALPHA = 64.0

# The visible hover lift is normalized to roughly three edge pixels. Re-capturing
# a complete QWidget subtree for a fraction of a pixel is wasted work, regardless
# of the card's absolute size. Accumulate scale until the card edge would move by
# at least this amount, then publish one fresh composite. 0.18 px is deliberately
# sub-pixel, so the content motion remains visually continuous while a full hover
# normally needs only ~15-20 expensive QWidget captures instead of 30-70+.
_CONTENT_EDGE_STEP_PX = 0.18
_NORMAL_SCALE_EPSILON = 1e-5


class CardInteractionPerformanceController(QObject):
    """Bound the cost of continuous cross-card hover without changing semantics.

    This is intentionally a scheduling layer around the proven card interaction
    implementation. It does not change hover targets, the 300 ms easing curve,
    pointer ownership, child-widget events, glass geometry, or business controls.

    The expensive path is bounded in three ways:
      * card motion updates never exceed 90 Hz on high-refresh displays;
      * at most two cards may keep an unfinished transform at once;
      * QWidget content re-rasterization ignores sub-pixel edge movement until it
        accumulates to a visible amount, while the Quick glass state stays smooth.
    """

    def __init__(self, window: QMainWindow, visual: Any, card_fx: Any) -> None:
        super().__init__(window)
        self.window = window
        self.visual = visual
        self.card_fx = card_fx

        self._original_frame_interval = getattr(card_fx, "_frame_interval_ms", None)
        self._original_animate_to = getattr(card_fx, "_animate_to", None)
        self._effect_originals: list[tuple[Any, Any]] = []
        self._motion_order: dict[QFrame, int] = {}
        self._motion_serial = 0

        self._install_motion_rate_cap()
        self._install_motion_budget()
        self._install_content_scale_quantization()
        window.destroyed.connect(self._cleanup)

    def _install_motion_rate_cap(self) -> None:
        original = self._original_frame_interval
        if not callable(original):
            return

        def frame_interval_ms(controller) -> int:  # noqa: ANN001
            try:
                baseline = max(1, int(original()))
            except (RuntimeError, TypeError, ValueError):
                baseline = _MIN_MOTION_FRAME_MS
            return max(baseline, _MIN_MOTION_FRAME_MS)

        self.card_fx._frame_interval_ms = MethodType(  # type: ignore[method-assign]  # noqa: SLF001
            frame_interval_ms,
            self.card_fx,
        )
        timer = getattr(self.card_fx, "_motion_timer", None)
        if timer is not None:
            try:
                timer.setInterval(self.card_fx._frame_interval_ms())  # noqa: SLF001
            except RuntimeError:
                pass

    def _retire_stale_motions(self) -> None:
        moving = getattr(self.card_fx, "_moving_frames", None)
        states = getattr(self.card_fx, "states", None)
        if not isinstance(moving, set) or not isinstance(states, dict):
            return

        # Drop order metadata for motions that already completed naturally.
        for frame in tuple(self._motion_order):
            if frame not in moving:
                self._motion_order.pop(frame, None)

        while len(moving) > _MAX_CONCURRENT_MOTIONS:
            hovered = getattr(self.card_fx, "hovered", None)
            pressed = getattr(self.card_fx, "pressed", None)
            protected = {frame for frame in (hovered, pressed) if frame is not None}
            candidates = [frame for frame in moving if frame not in protected]
            if not candidates:
                break

            stale = min(candidates, key=lambda frame: self._motion_order.get(frame, -1))
            state = states.get(stale)
            if state is not None:
                try:
                    # A card that is neither hovered nor pressed has only one
                    # semantically correct terminal state: exact normal/rest.
                    state.snap(_NORMAL_SCALE, _NORMAL_ALPHA)
                except RuntimeError:
                    state.moving = False
            moving.discard(stale)
            self._motion_order.pop(stale, None)

    def _install_motion_budget(self) -> None:
        original = self._original_animate_to
        if not callable(original):
            return

        performance = self

        def animate_to(controller, frame, *, scale: float, alpha: float) -> None:  # noqa: ANN001
            original(frame, scale=scale, alpha=alpha)
            moving = getattr(controller, "_moving_frames", None)
            if frame is not None and isinstance(moving, set) and frame in moving:
                performance._motion_serial += 1
                performance._motion_order[frame] = performance._motion_serial
            performance._retire_stale_motions()

        self.card_fx._animate_to = MethodType(  # type: ignore[method-assign]  # noqa: SLF001
            animate_to,
            self.card_fx,
        )

    @staticmethod
    def _effect_span(effect: Any) -> float:
        frame = effect.parent()
        if frame is None:
            return 1.0
        try:
            return max(1.0, float(frame.width()), float(frame.height()))
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return 1.0

    def _install_content_scale_quantization(self) -> None:
        surfaces = getattr(self.visual, "_glass", None)
        if not isinstance(surfaces, dict):
            return

        for surface in tuple(surfaces.values()):
            effect = getattr(surface, "_scale_effect", None)
            original = getattr(effect, "set_scale", None)
            if effect is None or not callable(original):
                continue

            performance = self

            def set_scale(effect_self, scale: float, _original=original) -> None:  # noqa: ANN001
                requested = float(scale)
                current = float(getattr(effect_self, "scale", _NORMAL_SCALE))

                if abs(requested - _NORMAL_SCALE) <= _NORMAL_SCALE_EPSILON:
                    # Exact rest is never quantized away; this also disables the
                    # QGraphicsEffect and returns QWidget to its cheapest path.
                    requested = _NORMAL_SCALE
                else:
                    span = performance._effect_span(effect_self)
                    edge_delta_px = span * abs(requested - current) * 0.5
                    if edge_delta_px < _CONTENT_EDGE_STEP_PX:
                        return

                _original(requested)

            effect.set_scale = MethodType(set_scale, effect)  # type: ignore[method-assign]
            self._effect_originals.append((effect, original))

    def _cleanup(self) -> None:
        timer = getattr(self.card_fx, "_motion_timer", None)
        if timer is not None:
            try:
                timer.stop()
            except RuntimeError:
                pass
        self._motion_order.clear()
        self._effect_originals.clear()


def install_card_interaction_performance(
    window: QMainWindow,
    visual: Any,
    card_fx: Any,
) -> CardInteractionPerformanceController:
    controller = CardInteractionPerformanceController(window, visual, card_fx)
    window._card_interaction_performance = controller  # type: ignore[attr-defined]
    return controller
