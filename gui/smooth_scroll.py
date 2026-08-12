"""Continuous universal wheel scrolling without a QApplication-wide event filter."""

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
    target: float
    velocity: float


class SmoothScroller(QObject):
    """Short-lived continuous target follower for discrete mouse wheels.

    A classic wheel still emits discrete notches, but those notches only move one
    persistent target. The visible scrollbar follows that target through a
    critically damped continuous motion, so successive notches extend the same
    glide instead of starting separate inertial bursts.

    Precision touchpads already emit pixelDelta() at high frequency. Those remain
    native/direct so the universal smoothing layer never adds latency to devices
    that are already continuous.
    """

    _STEP_MS = 16
    _WHEEL_TRAVEL_PX = 92.0
    _SPRING_OMEGA = 13.0
    _STOP_DISTANCE_PX = 0.45
    _STOP_SPEED_PX_S = 5.0
    _REVERSE_VELOCITY_RETENTION = 0.30
    _EXTERNAL_SYNC_TOLERANCE_PX = 3.0

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        # Keep the legacy attribute name because older launchers/tests may still
        # introspect it. Entries now hold one continuous spring state per bar.
        self._animations: dict[int, _ScrollMotion] = {}
        self._last_tick_s = time.perf_counter()
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(self._STEP_MS)
        self._timer.timeout.connect(self._tick)

    @staticmethod
    def _clamp_position(bar, position: float) -> float:  # noqa: ANN001
        return float(max(bar.minimum(), min(bar.maximum(), position)))

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
        """Apply native high-resolution pixel input without re-quantizing it."""

        self.cancel(bar)
        try:
            current = float(bar.value())
            target = self._clamp_position(bar, current + float(delta_px))
            bar.setValue(round(target))
        except RuntimeError:
            return

    def add_wheel_delta(self, bar, notch_delta: float) -> None:  # noqa: ANN001
        """Extend one persistent smooth-scroll target by a fractional wheel notch."""

        if abs(notch_delta) <= 1e-6:
            return
        key = id(bar)
        try:
            actual = float(bar.value())
        except RuntimeError:
            return

        motion = self._animations.get(key)
        if motion is None:
            motion = _ScrollMotion(
                bar=bar,
                position=actual,
                target=actual,
                velocity=0.0,
            )
            self._animations[key] = motion
        elif abs(actual - round(motion.position)) > self._EXTERNAL_SYNC_TOLERANCE_PX:
            # Scrollbar drag, keyboard navigation, or another controller moved it.
            motion.position = actual
            motion.target = actual
            motion.velocity = 0.0

        delta_px = float(notch_delta) * self._WHEEL_TRAVEL_PX
        remaining = motion.target - motion.position
        if remaining * delta_px < 0.0:
            # Reversing direction should feel like braking, not like waiting for a
            # queued distance in the old direction to drain first.
            motion.target = motion.position
            motion.velocity *= self._REVERSE_VELOCITY_RETENTION

        motion.target = self._clamp_position(bar, motion.target + delta_px)
        self._ensure_timer()

    def _tick(self) -> None:
        now = time.perf_counter()
        dt = max(0.001, min(0.050, now - self._last_tick_s))
        self._last_tick_s = now
        omega = self._SPRING_OMEGA
        decay = math.exp(-omega * dt)

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
                motion.target = max(minimum, min(maximum, motion.target))

            # Exact critically-damped spring integration for a fixed target over
            # this frame. Unlike target easing, this preserves velocity when new
            # wheel events extend the target and is insensitive to timer jitter.
            offset = motion.position - motion.target
            c2 = motion.velocity + omega * offset
            next_offset = (offset + c2 * dt) * decay
            next_velocity = (motion.velocity - omega * c2 * dt) * decay
            next_position = motion.target + next_offset
            clamped = max(minimum, min(maximum, next_position))
            hit_boundary = clamped != next_position

            motion.position = clamped
            motion.velocity = next_velocity
            bar.setValue(round(clamped))

            distance = abs(motion.target - motion.position)
            if hit_boundary or (
                distance <= self._STOP_DISTANCE_PX
                and abs(motion.velocity) <= self._STOP_SPEED_PX_S
            ):
                final = max(minimum, min(maximum, motion.target))
                bar.setValue(round(final))
                del self._animations[key]

        self._stop_if_idle()


class SmoothWheelFilter(QObject):
    """Universal nested scrolling: smooth mouse notches, native touchpad pixels."""

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
        # defeats continuous scrolling even when the outer page is smooth.
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

        # Precision touchpads / free-spin high-resolution devices can provide true
        # pixel motion. Preserve it directly rather than smoothing already-smooth
        # system input a second time.
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

        # Mechanical wheels commonly emit +/-120. Fractional angle deltas are kept
        # intact as well, but all of them extend one continuous damped target.
        notch_delta = -float(angle_y) / self._ANGLE_UNITS_PER_NOTCH
        owner = self._scroll_owner(area, notch_delta)
        if owner is None:
            return False

        self._scroller.add_wheel_delta(owner.verticalScrollBar(), notch_delta)
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
