"""Continuous smooth wheel scrolling without a QApplication-wide event filter."""

from __future__ import annotations

import math
import time

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import QAbstractScrollArea, QWidget


class SmoothScroller(QObject):
    _STEP_MS = 16
    _EASE = 0.18
    _REFERENCE_DT_S = _STEP_MS / 1000.0

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._animations: dict[int, list] = {}
        self._last_tick_s = time.perf_counter()
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(self._STEP_MS)
        self._timer.timeout.connect(self._tick)

    @staticmethod
    def _clamp_target(bar, target: float) -> float:  # noqa: ANN001
        return float(max(bar.minimum(), min(bar.maximum(), target)))

    def push(self, bar, delta: int) -> None:  # noqa: ANN001
        key = id(bar)
        entry = self._animations.get(key)
        if entry is None:
            entry = self._animations[key] = [bar, float(bar.value())]
        entry[1] = self._clamp_target(bar, float(entry[1]) + delta)
        if not self._timer.isActive():
            self._last_tick_s = time.perf_counter()
            self._timer.start()

    def _tick(self) -> None:
        now = time.perf_counter()
        dt = max(0.001, min(0.050, now - self._last_tick_s))
        self._last_tick_s = now
        # Exactly the old 0.18 easing at 16 ms, but frame-rate independent when
        # the GUI misses a frame. Delayed ticks catch up instead of slowing down.
        alpha = 1.0 - math.pow(1.0 - self._EASE, dt / self._REFERENCE_DT_S)

        for key, entry in list(self._animations.items()):
            bar, raw_target = entry
            try:
                target = self._clamp_target(bar, float(raw_target))
                entry[1] = target
                current = int(bar.value())
            except RuntimeError:
                del self._animations[key]
                continue

            diff = target - current
            if abs(diff) <= 1.0:
                bar.setValue(round(target))
                del self._animations[key]
                continue

            step = round(diff * alpha)
            if step == 0:
                step = 1 if diff > 0.0 else -1
            next_value = current + step
            if diff > 0.0:
                next_value = min(next_value, round(target))
            else:
                next_value = max(next_value, round(target))

            bar.setValue(next_value)
            # A changing scrollbar range or a platform clamp must never leave a
            # 16 ms timer spinning forever at an unreachable target.
            if bar.value() == current:
                del self._animations[key]

        if not self._animations:
            self._timer.stop()


class SmoothWheelFilter(QObject):
    """Filter Wheel only on real scroll areas/viewports, never on QApplication."""

    PIXELS_PER_NOTCH = 36

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._scroller = SmoothScroller(self)
        self._areas: dict[QObject, QAbstractScrollArea] = {}

    def install(self, root: QWidget) -> None:
        for area in root.findChildren(QAbstractScrollArea):
            self._attach(area)

    def _attach(self, area: QAbstractScrollArea) -> None:
        for watched in (area, area.viewport()):
            if watched in self._areas:
                continue
            self._areas[watched] = area
            watched.installEventFilter(self)

    def eventFilter(self, watched: QObject, event) -> bool:  # noqa: ANN001, N802
        if event.type() != QEvent.Type.Wheel:
            return False
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            return False

        area = self._areas.get(watched)
        if area is None:
            return False
        delta = event.angleDelta().y()
        if delta == 0:
            return False

        bar = area.verticalScrollBar()
        if not bar.isVisible() or bar.maximum() <= bar.minimum():
            return False

        self._scroller.push(
            bar,
            -round(delta / 120 * self.PIXELS_PER_NOTCH),
        )
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
