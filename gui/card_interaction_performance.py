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

# During rapid A -> B -> C traversal, only the current interactive card and one
# immediately previous outgoing card are useful. The previous card may finish its
# return animation from one frozen composite; anything older is stale work.
_MAX_CONCURRENT_MOTIONS = 2
_NORMAL_SCALE = 1.0
_NORMAL_ALPHA = 64.0

# The visible hover lift is normalized to roughly three edge pixels. Re-capturing
# a complete LIVE QWidget subtree for a fraction of a pixel is wasted work.
# Accumulate until the edge would move by at least this sub-pixel amount.
_CONTENT_EDGE_STEP_PX = 0.18
_NORMAL_SCALE_EPSILON = 1e-5


class CardInteractionPerformanceController(QObject):
    """Reduce continuous cross-card hover to one live raster owner.

    The proven card interaction controller still owns pointer semantics, targets,
    the 300 ms easing curve and Quick glass presentation. This scheduler changes
    only how expensive QWidget content transforms are paid for:

      * motion ticks are capped at 90 Hz on high-refresh displays;
      * the hovered/pressed card is always LIVE and keeps fresh child feedback;
      * the immediately previous outgoing card is FROZEN after one composite;
      * older outgoing cards snap to exact rest and release their images;
      * LIVE content re-rasterization skips imperceptible sub-pixel scale deltas.

    No child event filters, layout mutations or business-control interception are
    introduced. The interaction owner remains the real QWidget tree.
    """

    def __init__(self, window: QMainWindow, visual: Any, card_fx: Any) -> None:
        super().__init__(window)
        self.window = window
        self.visual = visual
        self.card_fx = card_fx

        self._original_frame_interval = getattr(card_fx, "_frame_interval_ms", None)
        self._original_animate_to = getattr(card_fx, "_animate_to", None)
        self._effect_originals: list[tuple[Any, Any]] = []
        self._effects_by_frame: dict[QFrame, Any] = {}
        self._motion_order: dict[QFrame, int] = {}
        self._motion_serial = 0

        surfaces = getattr(self.visual, "_glass", None)
        if isinstance(surfaces, dict):
            for frame, surface in surfaces.items():
                effect = getattr(surface, "_scale_effect", None)
                if isinstance(frame, QFrame) and effect is not None:
                    self._effects_by_frame[frame] = effect

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

    def _set_frame_frozen(self, frame: QFrame | None, frozen: bool) -> None:
        if frame is None:
            return
        effect = self._effects_by_frame.get(frame)
        setter = getattr(effect, "set_frozen", None)
        if callable(setter):
            try:
                setter(bool(frozen))
            except RuntimeError:
                pass

    def _should_freeze_outgoing(self, frame: QFrame, controller: Any) -> bool:
        if frame is getattr(controller, "hovered", None):
            return False
        if frame is getattr(controller, "pressed", None):
            return False

        states = getattr(controller, "states", None)
        state = states.get(frame) if isinstance(states, dict) else None
        if state is None:
            return False
        return bool(getattr(state, "moving", False)) or (
            abs(float(getattr(state, "current_scale", _NORMAL_SCALE)) - _NORMAL_SCALE)
            > _NORMAL_SCALE_EPSILON
        )

    def _retire_stale_motions(self) -> None:
        moving = getattr(self.card_fx, "_moving_frames", None)
        states = getattr(self.card_fx, "states", None)
        if not isinstance(moving, set) or not isinstance(states, dict):
            return

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
                    state.snap(_NORMAL_SCALE, _NORMAL_ALPHA)
                except RuntimeError:
                    state.moving = False
            self._set_frame_frozen(stale, False)
            moving.discard(stale)
            self._motion_order.pop(stale, None)

    def _install_motion_budget(self) -> None:
        original = self._original_animate_to
        if not callable(original):
            return

        performance = self

        def animate_to(controller, frame, *, scale: float, alpha: float) -> None:  # noqa: ANN001
            # Ownership is already updated by NekroCardInteractionController before
            # it asks a card to animate. Keep the active owner live; an outgoing
            # card may freeze before its first return-frame rasterization.
            if frame is not None:
                if frame is getattr(controller, "hovered", None) or frame is getattr(
                    controller, "pressed", None
                ):
                    performance._set_frame_frozen(frame, False)
                elif performance._should_freeze_outgoing(frame, controller):
                    performance._set_frame_frozen(frame, True)

            original(frame, scale=scale, alpha=alpha)

            moving = getattr(controller, "_moving_frames", None)
            states = getattr(controller, "states", None)
            if frame is not None and isinstance(moving, set) and frame in moving:
                performance._motion_serial += 1
                performance._motion_order[frame] = performance._motion_serial
            elif frame is not None and isinstance(states, dict):
                state = states.get(frame)
                if state is not None and (
                    not bool(getattr(state, "moving", False))
                    and abs(
                        float(getattr(state, "current_scale", _NORMAL_SCALE))
                        - _NORMAL_SCALE
                    )
                    <= _NORMAL_SCALE_EPSILON
                ):
                    performance._set_frame_frozen(frame, False)

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
        for effect in tuple(self._effects_by_frame.values()):
            original = getattr(effect, "set_scale", None)
            if not callable(original):
                continue

            performance = self

            def set_scale(effect_self, scale: float, _original=original) -> None:  # noqa: ANN001
                requested = float(scale)
                current = float(getattr(effect_self, "scale", _NORMAL_SCALE))

                if abs(requested - _NORMAL_SCALE) <= _NORMAL_SCALE_EPSILON:
                    # Exact rest is never quantized away; this disables the effect
                    # and releases any frozen outgoing source immediately.
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
        for frame in tuple(self._effects_by_frame):
            self._set_frame_frozen(frame, False)
        self._motion_order.clear()
        self._effects_by_frame.clear()
        self._effect_originals.clear()


def install_card_interaction_performance(
    window: QMainWindow,
    visual: Any,
    card_fx: Any,
) -> CardInteractionPerformanceController:
    controller = CardInteractionPerformanceController(window, visual, card_fx)
    window._card_interaction_performance = controller  # type: ignore[attr-defined]
    return controller
