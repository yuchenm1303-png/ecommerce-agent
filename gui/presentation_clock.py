from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QTimer
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication, QMainWindow


_PRESENTATION_TICK_MS = 8


class PresentationClock(QObject):
    """One cursor read and one presentation heartbeat for the whole runtime UI.

    Background parallax, card hit-testing/motion and the lightweight particle /
    cursor overlay used to own separate precise timers and separate QCursor reads.
    That multiplied main-thread wakeups and coordinate work while the user moved
    the mouse. This clock is the only high-frequency Python presentation source.

    Consumers remain responsible for their own visual cadence: the background only
    publishes pointer targets when input changes, card motion caps itself to the
    display refresh rate, and decorative effects keep their established 60 Hz
    budget. Business runners and QWidget input handling are completely separate.
    """

    def __init__(
        self,
        window: QMainWindow,
        *,
        background: Any,
        card_fx: Any,
        effects: Any,
    ) -> None:
        super().__init__(window)
        self.window = window
        self.background = background
        self.card_fx = card_fx
        self.effects = effects
        self._holds: set[str] = set()
        self._window_paused = False
        self._last_global: tuple[int, int] | None = None
        self._last_left_down: bool | None = None

        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.setInterval(_PRESENTATION_TICK_MS)
        self.timer.timeout.connect(self._tick)

        window.installEventFilter(self)
        window.destroyed.connect(self.cleanup)
        self._sync_window_state()

    @property
    def running(self) -> bool:
        return bool(self.timer.isActive() and not self._holds and not self._window_paused)

    def _can_run(self) -> bool:
        if self._holds or self._window_paused:
            return False
        try:
            return bool(self.window.isVisible() and not self.window.isMinimized())
        except RuntimeError:
            return False

    def _sync_window_state(self) -> None:
        try:
            self._window_paused = bool(
                not self.window.isVisible() or self.window.isMinimized()
            )
        except RuntimeError:
            self._window_paused = True

        if self._can_run():
            if not self.timer.isActive():
                self._reset_input_identity()
                self.timer.start()
        else:
            self.timer.stop()

    def _reset_input_identity(self) -> None:
        self._last_global = None
        self._last_left_down = None
        try:
            self.background.reset_pointer_identity()
        except (AttributeError, RuntimeError):
            pass

    def suspend(self, reason: str) -> None:
        token = str(reason or "presentation").strip() or "presentation"
        self._holds.add(token)
        self.timer.stop()
        try:
            self.background.pause_pointer_animation()
        except (AttributeError, RuntimeError):
            pass

    def resume(self, reason: str) -> None:
        token = str(reason or "presentation").strip() or "presentation"
        self._holds.discard(token)
        self._reset_input_identity()
        self._sync_window_state()

    def _tick(self) -> None:
        if not self._can_run():
            self._sync_window_state()
            return

        try:
            global_pos = QCursor.pos()
            point = (int(global_pos.x()), int(global_pos.y()))
            left_down = bool(QApplication.mouseButtons() & Qt.MouseButton.LeftButton)
        except RuntimeError:
            return

        input_changed = point != self._last_global or left_down != self._last_left_down
        self._last_global = point
        self._last_left_down = left_down
        now_s = time.perf_counter()

        # These calls intentionally receive the same QPoint/button sample. No
        # consumer is allowed to perform another high-frequency QCursor read.
        try:
            self.background.presentation_tick(global_pos, input_changed=input_changed)
        except RuntimeError:
            pass
        try:
            self.card_fx.presentation_tick(
                global_pos,
                left_down=left_down,
                now_s=now_s,
                input_changed=input_changed,
            )
        except RuntimeError:
            pass
        try:
            self.effects.presentation_tick(
                global_pos,
                left_down=left_down,
                now_s=now_s,
            )
        except RuntimeError:
            pass

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is not self.window:
            return False
        if event.type() in {
            QEvent.Type.Show,
            QEvent.Type.Hide,
            QEvent.Type.WindowStateChange,
        }:
            QTimer.singleShot(0, self._sync_window_state)
        return False

    def cleanup(self) -> None:
        self.timer.stop()
        self._holds.clear()
        try:
            self.window.removeEventFilter(self)
        except RuntimeError:
            pass


def install_presentation_clock(
    window: QMainWindow,
    *,
    background: Any,
    card_fx: Any,
    effects: Any,
) -> PresentationClock:
    existing = getattr(window, "_presentation_clock", None)
    if isinstance(existing, PresentationClock):
        return existing
    clock = PresentationClock(
        window,
        background=background,
        card_fx=card_fx,
        effects=effects,
    )
    window._presentation_clock = clock  # type: ignore[attr-defined]
    return clock


__all__ = ["PresentationClock", "install_presentation_clock"]
