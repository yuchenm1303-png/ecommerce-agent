from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QTimer
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication, QMainWindow


_ACTIVE_PRESENTATION_TICK_MS = 8
_AMBIENT_PRESENTATION_TICK_MS = 16
_INTERACTION_GRACE_MS = 360
_WIDGET_STARVATION_MS = 40


@dataclass(slots=True)
class _WidgetSample:
    global_pos: QPoint
    left_down: bool
    input_changed: bool
    button_edge: bool


class PresentationClock(QObject):
    """One adaptive input clock with explicit Quick -> QWidget ordering.

    Ambient visual work already has a 16 ms frame budget, so idle presentation
    sampling runs at that cadence instead of waking Python every 8 ms forever.
    Any pointer/button change immediately promotes the shared clock to the original
    8 ms interaction cadence for long enough to cover the complete 300 ms card
    tween.  The final settle sample is retained even if the GUI thread was briefly
    blocked, so frozen card content can never be stranded mid-transition.

    Quick owns the first presentation lane. QWidget card/effect work is released
    after the next QQuickWindow.frameSwapped signal while Quick is animating. When
    Quick is idle, the QWidget lane flushes immediately. A short starvation timer
    remains only as a safety net for an occluded or stalled Quick surface.
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
        self._active_until_s = 0.0
        self._card_settle_pending = False

        self._widget_samples: list[_WidgetSample] = []
        self._widget_flush_posted = False
        self._quick_window = getattr(background, "quick_window", None)

        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.setInterval(_AMBIENT_PRESENTATION_TICK_MS)
        self.timer.timeout.connect(self._tick)

        self._widget_watchdog = QTimer(self)
        self._widget_watchdog.setSingleShot(True)
        self._widget_watchdog.setTimerType(Qt.TimerType.PreciseTimer)
        self._widget_watchdog.setInterval(_WIDGET_STARVATION_MS)
        self._widget_watchdog.timeout.connect(self._flush_widget_lane)

        quick = self._quick_window
        if quick is not None:
            try:
                quick.frameSwapped.connect(self._on_quick_frame_swapped)
            except (AttributeError, RuntimeError, TypeError):
                self._quick_window = None

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

    def _set_tick_interval(self, interval_ms: int) -> None:
        interval_ms = int(interval_ms)
        if self.timer.interval() != interval_ms:
            self.timer.setInterval(interval_ms)

    def _mark_interaction_active(self, now_s: float) -> None:
        self._active_until_s = max(
            self._active_until_s,
            now_s + (_INTERACTION_GRACE_MS / 1000.0),
        )
        self._card_settle_pending = True
        self._set_tick_interval(_ACTIVE_PRESENTATION_TICK_MS)

    def _sync_cadence(self, now_s: float) -> None:
        interval = (
            _ACTIVE_PRESENTATION_TICK_MS
            if now_s < self._active_until_s
            else _AMBIENT_PRESENTATION_TICK_MS
        )
        self._set_tick_interval(interval)

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
                self._set_tick_interval(_AMBIENT_PRESENTATION_TICK_MS)
                self.timer.start()
        else:
            self.timer.stop()
            self._clear_widget_lane()

    def _reset_input_identity(self) -> None:
        self._last_global = None
        self._last_left_down = None
        self._active_until_s = 0.0
        self._card_settle_pending = False
        self._clear_widget_lane()
        try:
            self.background.reset_pointer_identity()
        except (AttributeError, RuntimeError):
            pass

    def _clear_widget_lane(self) -> None:
        self._widget_samples.clear()
        self._widget_flush_posted = False
        self._widget_watchdog.stop()

    def suspend(self, reason: str) -> None:
        token = str(reason or "presentation").strip() or "presentation"
        self._holds.add(token)
        self.timer.stop()
        self._active_until_s = 0.0
        self._card_settle_pending = False
        self._clear_widget_lane()
        try:
            self.background.pause_pointer_animation()
        except (AttributeError, RuntimeError):
            pass

    def resume(self, reason: str) -> None:
        token = str(reason or "presentation").strip() or "presentation"
        self._holds.discard(token)
        self._reset_input_identity()
        self._sync_window_state()

    def _queue_widget_sample(
        self,
        global_pos: QPoint,
        *,
        left_down: bool,
        input_changed: bool,
        button_edge: bool,
    ) -> None:
        sample = _WidgetSample(
            global_pos=QPoint(global_pos),
            left_down=bool(left_down),
            input_changed=bool(input_changed),
            button_edge=bool(button_edge),
        )
        if not self._widget_samples:
            self._widget_samples.append(sample)
            return

        if button_edge or self._widget_samples[-1].button_edge:
            self._widget_samples.append(sample)
            return

        sample.input_changed = bool(
            sample.input_changed or self._widget_samples[-1].input_changed
        )
        self._widget_samples[-1] = sample

    def _quick_lane_active(self) -> bool:
        quick = self._quick_window
        if quick is None:
            return False
        try:
            return bool(
                quick.isVisible()
                and quick.isExposed()
                and not (quick.windowState() & Qt.WindowState.WindowMinimized)
                and quick.property("animationRunning")
            )
        except (AttributeError, RuntimeError, TypeError):
            return False

    def _schedule_widget_lane(self) -> None:
        if not self._widget_samples:
            return
        if self._quick_lane_active():
            if not self._widget_watchdog.isActive():
                self._widget_watchdog.start()
            return
        self._flush_widget_lane()

    def _on_quick_frame_swapped(self) -> None:
        if not self._widget_samples or self._widget_flush_posted:
            return
        self._widget_flush_posted = True
        QTimer.singleShot(0, self._flush_widget_lane_after_swap)

    def _flush_widget_lane_after_swap(self) -> None:
        self._widget_flush_posted = False
        self._flush_widget_lane()

    def _flush_widget_lane(self) -> None:
        if not self._widget_samples:
            self._widget_watchdog.stop()
            return

        samples = self._widget_samples
        self._widget_samples = []
        self._widget_watchdog.stop()
        now_s = time.perf_counter()
        card_active = now_s < self._active_until_s
        card_due = card_active or self._card_settle_pending

        if card_due:
            for sample in samples:
                try:
                    self.card_fx.presentation_tick(
                        sample.global_pos,
                        left_down=sample.left_down,
                        now_s=now_s,
                        input_changed=sample.input_changed,
                    )
                except RuntimeError:
                    pass

            if not card_active:
                # One final call after the interaction window guarantees that a
                # delayed event loop still snaps any 300 ms tween to its endpoint.
                self._card_settle_pending = False

        latest = samples[-1]
        try:
            self.effects.presentation_tick(
                latest.global_pos,
                left_down=latest.left_down,
                now_s=now_s,
            )
        except RuntimeError:
            pass

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

        now_s = time.perf_counter()
        previous_left = self._last_left_down
        input_changed = point != self._last_global or left_down != previous_left
        button_edge = previous_left is not None and left_down != previous_left
        self._last_global = point
        self._last_left_down = left_down

        if input_changed:
            self._mark_interaction_active(now_s)
            # Quick only needs new pointer targets. Its FrameAnimation owns the
            # ongoing GPU parallax tween after this handoff.
            try:
                self.background.presentation_tick(global_pos, input_changed=True)
            except RuntimeError:
                pass
        else:
            self._sync_cadence(now_s)

        # QWidget work uses the same sampled input, but it is committed only after
        # Quick presents its frame (or immediately when Quick has no active frame).
        # Ambient effects keep their existing 16 ms visual budget while card work
        # disappears completely once the interaction tween has settled.
        self._queue_widget_sample(
            global_pos,
            left_down=left_down,
            input_changed=input_changed,
            button_edge=button_edge,
        )
        self._schedule_widget_lane()

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
        self._active_until_s = 0.0
        self._card_settle_pending = False
        self._clear_widget_lane()
        self._holds.clear()
        quick = self._quick_window
        self._quick_window = None
        if quick is not None:
            try:
                quick.frameSwapped.disconnect(self._on_quick_frame_swapped)
            except (AttributeError, RuntimeError, TypeError):
                pass
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
