from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QTimer
from PySide6.QtWidgets import QApplication, QMainWindow, QWidget


_IDLE_FRAME_MS = 16


class PresentationClock(QObject):
    """One event-driven input path plus one adaptive presentation heartbeat.

    Mouse position/button state is delivered directly from Qt mouse events instead
    of polling QCursor at 125 Hz.  The remaining timer exists only because the
    established visuals contain continuous sakura/cursor easing and short card
    tweens.  It idles at the existing 60 Hz decorative cadence and temporarily
    follows the card controller's display-aware motion cadence while a card is
    actually animating.
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
        self._last_global = QPoint(-100_000, -100_000)
        self._last_left_down = False
        self._have_input = False
        self._pending_outside_resample = False

        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.setInterval(_IDLE_FRAME_MS)
        self.timer.timeout.connect(self._frame_tick)

        self._install_mouse_tracking(window)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        window.destroyed.connect(self.cleanup)
        self._sync_window_state()

    @property
    def running(self) -> bool:
        return bool(self.timer.isActive() and not self._holds and not self._window_paused)

    def _belongs_to_window(self, widget: QWidget | None) -> bool:
        current = widget
        while current is not None:
            if current is self.window:
                return True
            current = current.parentWidget()
        return False

    def _install_mouse_tracking(self, root: QWidget) -> None:
        try:
            root.setMouseTracking(True)
        except RuntimeError:
            return
        for child in root.findChildren(QWidget):
            try:
                child.setMouseTracking(True)
            except RuntimeError:
                continue

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
                self.timer.start()
        else:
            self.timer.stop()

    def _reset_input_identity(self) -> None:
        self._have_input = False
        self._pending_outside_resample = False
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

    @staticmethod
    def _global_point(event: object) -> QPoint | None:
        getter = getattr(event, "globalPosition", None)
        if callable(getter):
            try:
                position = getter()
                return QPoint(round(float(position.x())), round(float(position.y())))
            except (AttributeError, RuntimeError, TypeError, ValueError):
                pass
        getter = getattr(event, "globalPos", None)
        if callable(getter):
            try:
                return QPoint(getter())
            except (AttributeError, RuntimeError, TypeError):
                pass
        return None

    def _left_state_from_event(self, event: object, event_type: QEvent.Type) -> bool:
        if event_type == QEvent.Type.MouseButtonPress:
            try:
                if event.button() == Qt.MouseButton.LeftButton:  # type: ignore[attr-defined]
                    return True
            except (AttributeError, RuntimeError):
                return self._last_left_down
        elif event_type == QEvent.Type.MouseButtonRelease:
            try:
                if event.button() == Qt.MouseButton.LeftButton:  # type: ignore[attr-defined]
                    return False
            except (AttributeError, RuntimeError):
                return self._last_left_down
        buttons = getattr(event, "buttons", None)
        if callable(buttons):
            try:
                return bool(buttons() & Qt.MouseButton.LeftButton)
            except (RuntimeError, TypeError):
                pass
        return self._last_left_down

    def _deliver_input(self, global_pos: QPoint, *, left_down: bool, force: bool = False) -> None:
        if not self._can_run():
            return
        same = (
            self._have_input
            and global_pos == self._last_global
            and bool(left_down) == self._last_left_down
        )
        if same and not force:
            return

        self._last_global = QPoint(global_pos)
        self._last_left_down = bool(left_down)
        self._have_input = True
        now_s = time.perf_counter()

        try:
            self.background.presentation_tick(global_pos, input_changed=True)
        except RuntimeError:
            pass
        try:
            self.card_fx.presentation_tick(
                global_pos,
                left_down=left_down,
                now_s=now_s,
                input_changed=True,
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
        self._sync_frame_interval()

    def _card_motion_interval_ms(self) -> int:
        try:
            moving = bool(getattr(self.card_fx, "_moving_frames", ()))
            interval_s = float(getattr(self.card_fx, "_motion_interval_s", 0.016))
        except (RuntimeError, TypeError, ValueError):
            return _IDLE_FRAME_MS
        if not moving:
            return _IDLE_FRAME_MS
        return max(4, min(_IDLE_FRAME_MS, int(round(interval_s * 1000.0))))

    def _sync_frame_interval(self) -> None:
        target = self._card_motion_interval_ms()
        if self.timer.interval() != target:
            self.timer.setInterval(target)

    def _frame_tick(self) -> None:
        if not self._can_run():
            self._sync_window_state()
            return

        now_s = time.perf_counter()
        if self._pending_outside_resample:
            # Polling used to naturally produce a second outside sample. Preserve
            # the one-sample traversal grace without bringing polling back.
            self._pending_outside_resample = False
            try:
                self.card_fx.presentation_tick(
                    self._last_global,
                    left_down=self._last_left_down,
                    now_s=now_s,
                    input_changed=True,
                )
            except RuntimeError:
                pass
        else:
            try:
                self.card_fx.presentation_tick(
                    self._last_global,
                    left_down=self._last_left_down,
                    now_s=now_s,
                    input_changed=False,
                )
            except RuntimeError:
                pass

        try:
            self.effects.presentation_tick(
                self._last_global,
                left_down=self._last_left_down,
                now_s=now_s,
            )
        except RuntimeError:
            pass
        self._sync_frame_interval()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        event_type = event.type()

        if event_type == QEvent.Type.ChildAdded:
            child_getter = getattr(event, "child", None)
            child = child_getter() if callable(child_getter) else None
            if isinstance(child, QWidget):
                parent = child.parentWidget()
                if self._belongs_to_window(parent):
                    self._install_mouse_tracking(child)
            return False

        widget = watched if isinstance(watched, QWidget) else None
        if not self._belongs_to_window(widget):
            return False

        if event_type in {
            QEvent.Type.Show,
            QEvent.Type.Hide,
            QEvent.Type.WindowStateChange,
        } and watched is self.window:
            QTimer.singleShot(0, self._sync_window_state)
            return False

        if event_type in {
            QEvent.Type.MouseMove,
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseButtonRelease,
            QEvent.Type.Enter,
        }:
            point = self._global_point(event)
            if point is not None:
                self._deliver_input(
                    point,
                    left_down=self._left_state_from_event(event, event_type),
                )
            return False

        if event_type == QEvent.Type.Leave and watched is self.window:
            outside = QPoint(-100_000, -100_000)
            self._deliver_input(outside, left_down=self._last_left_down, force=True)
            self._pending_outside_resample = True
        return False

    def cleanup(self) -> None:
        self.timer.stop()
        self._holds.clear()
        app = QApplication.instance()
        if app is not None:
            try:
                app.removeEventFilter(self)
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
