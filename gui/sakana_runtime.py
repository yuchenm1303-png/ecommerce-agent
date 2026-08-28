from __future__ import annotations

import math
import time
from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer, Qt

from gui.sakana_physics import SakanaSpringState, advance_spring


_DEFAULT_REFRESH_HZ = 60.0
_MIN_REFRESH_HZ = 30.0
_MAX_REFRESH_HZ = 360.0


class SakanaSpringRuntime(QObject):
    """Run Sakana physics on its own clock, independent of the app render loop.

    Browser Sakana advances once for each requestAnimationFrame callback. This
    runtime preserves that one-callback/one-physics-step contract with a private
    precise timer. It never subscribes to QQuickWindow animation, frame-swapped,
    hover, card animation, or scene-graph timing signals.
    """

    def __init__(
        self,
        on_frame: Callable[[], None],
        *,
        refresh_hz: float = _DEFAULT_REFRESH_HZ,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.state = SakanaSpringState()
        self._on_frame = on_frame

        try:
            hz = float(refresh_hz)
        except (TypeError, ValueError):
            hz = _DEFAULT_REFRESH_HZ
        if not math.isfinite(hz) or hz < _MIN_REFRESH_HZ or hz > _MAX_REFRESH_HZ:
            hz = _DEFAULT_REFRESH_HZ
        self._frame_seconds = 1.0 / hz

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._tick)

        now = time.monotonic()
        self._last_tick = now
        self._next_deadline = now
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    def start(self, *, reset_clock: bool) -> None:
        now = time.monotonic()
        if reset_clock:
            self._last_tick = now
        self._running = True
        self._next_deadline = now + self._frame_seconds
        self._schedule(now)

    def pause(self) -> None:
        self._running = False
        self._timer.stop()

    def resume_after_drag(self) -> None:
        # Upstream mouseup does not reset _lastRunUnix. Preserve that exact
        # elapsed-time behavior while scheduling only one future physics step.
        self.start(reset_clock=False)

    def shutdown(self) -> None:
        self.pause()
        try:
            self._timer.timeout.disconnect(self._tick)
        except (RuntimeError, TypeError):
            pass

    def _schedule(self, now: float | None = None) -> None:
        if not self._running:
            return
        if now is None:
            now = time.monotonic()
        delay_seconds = max(0.0, self._next_deadline - now)
        delay_ms = max(1, int(round(delay_seconds * 1000.0)))
        self._timer.start(delay_ms)

    def _tick(self) -> None:
        if not self._running:
            return

        now = time.monotonic()
        elapsed_ms = max(0.0, (now - self._last_tick) * 1000.0)
        self._last_tick = now

        if not advance_spring(self.state, elapsed_ms):
            self._running = False
            return

        self._on_frame()

        # requestAnimationFrame never catches up missed frames by firing a burst
        # of callbacks. If the GUI thread was busy, skip missed presentation slots
        # and schedule one future step instead of accumulating spring energy.
        self._next_deadline += self._frame_seconds
        if self._next_deadline <= now:
            self._next_deadline = now + self._frame_seconds
        self._schedule(now)
