"""Continuous inertial wheel scrolling without a QApplication-wide event filter."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import QAbstractItemView, QAbstractScrollArea, QWidget


@dataclass(slots=True)
class _ScrollMotion:
    bar: object
    position: float
    velocity: float


class SmoothScroller(QObject):
    """Short-lived inertial scroll integrator.

    Wheel notches add velocity instead of moving a fixed target. The integrator
    keeps a floating-point position and publishes only the final integer value
    required by QScrollBar, removing the old target/rounding staircase.
    """

    _STEP_MS = 16
    _FRICTION_PER_S = 8.5
    _WHEEL_IMPULSE_PX_S = 560.0
    _MAX_SPEED_PX_S = 2200.0
    _STOP_SPEED_PX_S = 12.0
    _REVERSE_RETENTION = 0.20
    _EXTERNAL_SYNC_TOLERANCE_PX = 3.0

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        # Keep the legacy attribute name because cleanup/tests in older launchers
        # may still introspect it. Entries now hold continuous motion state.
        self._animations: dict[int, _ScrollMotion] = {}
        self._last_tick_s = time.perf_counter()
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(self._STEP_MS)
        self._timer.timeout.connect(self._tick)

    @staticmethod
    def _clamp_position(bar, position: float) -> float:  # noqa: ANN001
        return float(max(bar.minimum(), min(bar.maximum(), position)))

    @staticmethod
    def _clamp_velocity(velocity: float) -> float:
        limit = SmoothScroller._MAX_SPEED_PX_S
        return max(-limit, min(limit, velocity))

    def _ensure_timer(self) -> None:
        if self._timer.isActive():
            return
        self._last_tick_s = time.perf_counter()
        self._timer.start()

    def _stop_if_idle(self) -> None:
        if not self._animations:
            self._timer.stop()

    def cancel(self, bar) -> None:  # noqa: ANN001
        self._animations.pop(id(bar), None)
        self._stop_if_idle()

    def scroll_pixels(self, bar, delta_px: float) -> None:  # noqa: ANN001
        """Apply native high-resolution pixel input without re-quantizing it.

        Precision touchpads already deliver continuous motion and momentum through
        pixelDelta(), so layering synthetic inertia on top would make them floaty.
        Any stale wheel inertia for the same scrollbar is dropped first.
        """

        self.cancel(bar)
        try:
            current = float(bar.value())
            target = self._clamp_position(bar, current + float(delta_px))
            bar.setValue(round(target))
        except RuntimeError:
            return

    def push_impulse(self, bar, notch_delta: float) -> None:  # noqa: ANN001
        """Add mouse-wheel momentum in scrollbar-positive coordinates."""

        if abs(notch_delta) <= 1e-6:
            return
        key = id(bar)
        try:
            actual = float(bar.value())
        except RuntimeError:
            return

        motion = self._animations.get(key)
        if motion is None:
            motion = _ScrollMotion(bar=bar, position=actual, velocity=0.0)
            self._animations[key] = motion
        elif abs(actual - round(motion.position)) > self._EXTERNAL_SYNC_TOLERANCE_PX:
            # Scrollbar drag, keyboard navigation, or another owner moved it.
            motion.position = actual

        impulse = float(notch_delta) * self._WHEEL_IMPULSE_PX_S
        if motion.velocity * impulse < 0.0:
            # Reversal should brake immediately instead of fighting a long tail.
            motion.velocity *= self._REVERSE_RETENTION
        motion.velocity = self._clamp_velocity(motion.velocity + impulse)
        self._ensure_timer()

    def _tick(self) -> None:
        now = time.perf_counter()
        dt = max(0.001, min(0.050, now - self._last_tick_s))
        self._last_tick_s = now

        # Exact exponential integration makes displacement independent of timer
        # jitter while still giving the wheel a short, controllable inertial tail.
        decay = math.exp(-self._FRICTION_PER_S * dt)
        distance_factor = (1.0 - decay) / self._FRICTION_PER_S

        for key, motion in list(self._animations.items()):
            bar = motion.bar
            try:
                actual = float(bar.value())
                minimum = float(bar.minimum())
                maximum = float(bar.maximum())
            except RuntimeError:
                del self._animations[key]
                continue

            expected = round(motion.position)
            if abs(actual - expected) > self._EXTERNAL_SYNC_TOLERANCE_PX:
                motion.position = actual

            old_velocity = motion.velocity
            next_position = motion.position + old_velocity * distance_factor
            next_velocity = old_velocity * decay
            clamped = max(minimum, min(maximum, next_position))
            hit_boundary = clamped != next_position

            motion.position = clamped
            motion.velocity = next_velocity
            bar.setValue(round(clamped))

            if hit_boundary or abs(next_velocity) < self._STOP_SPEED_PX_S:
                del self._animations[key]

        self._stop_if_idle()


class SmoothWheelFilter(QObject):
    """Continuous nested scrolling with native touchpad precision and wheel inertia."""

    _ANGLE_UNITS_PER_NOTCH = 120.0

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._scroller = SmoothScroller(self)
        self._areas: dict[QObject, QAbstractScrollArea] = {}

    def install(self, root: QWidget) -> None:
        for area in root.findChildren(QAbstractScrollArea):
            self._attach(area)

    def _attach(self, area: QAbstractScrollArea) -> None:
        # Item views otherwise quantize their own scrollbar to whole rows, which
        # defeats a continuous wheel integrator even when the outer page is smooth.
        if isinstance(area, QAbstractItemView):
            area.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        for watched in (area, area.viewport()):
            if watched in self._areas:
                continue
            self._areas[watched] = area
            watched.installEventFilter(self)

    @staticmethod
    def _can_move(area: QAbstractScrollArea, scroll_delta: float) -> bool:
        bar = area.verticalScrollBar()
        if not bar.isVisible() or bar.maximum() <= bar.minimum():
            return False
        if scroll_delta > 0.0:
            return bar.value() < bar.maximum()
        if scroll_delta < 0.0:
            return bar.value() > bar.minimum()
        return False

    @staticmethod
    def _parent_scroll_area(area: QAbstractScrollArea) -> QAbstractScrollArea | None:
        parent = area.parentWidget()
        while parent is not None:
            if isinstance(parent, QAbstractScrollArea):
                return parent
            parent = parent.parentWidget()
        return None

    def _scroll_owner(
        self,
        area: QAbstractScrollArea,
        scroll_delta: float,
    ) -> QAbstractScrollArea | None:
        current: QAbstractScrollArea | None = area
        while current is not None:
            if self._can_move(current, scroll_delta):
                return current
            current = self._parent_scroll_area(current)
        return None

    def eventFilter(self, watched: QObject, event) -> bool:  # noqa: ANN001, N802
        if event.type() != QEvent.Type.Wheel:
            return False
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            return False

        area = self._areas.get(watched)
        if area is None:
            return False

        # Precision touchpads / high-resolution devices already provide true pixel
        # motion. Preserve it exactly and let the OS keep ownership of its inertia.
        pixel_y = event.pixelDelta().y()
        if pixel_y:
            scroll_delta = -float(pixel_y)
            owner = self._scroll_owner(area, scroll_delta)
            if owner is None:
                return False
            self._scroller.scroll_pixels(owner.verticalScrollBar(), scroll_delta)
            return True

        angle_y = event.angleDelta().y()
        if angle_y == 0:
            return False

        # Keep fractional high-resolution wheel deltas; never round to 120-unit
        # notches or a fixed pixel target. One notch is an impulse, not a jump.
        notch_delta = -float(angle_y) / self._ANGLE_UNITS_PER_NOTCH
        owner = self._scroll_owner(area, notch_delta)
        if owner is None:
            return False

        self._scroller.push_impulse(owner.verticalScrollBar(), notch_delta)
        return True

    def cleanup(self) -> None:
        self._scroller._timer.stop()
        self._scroller._animations.clear()
        for watched in tuple(self._areas):
            try:
                watched.removeEventFilter(self)
            except RuntimeError:
                pass
        self._areas.clear()
