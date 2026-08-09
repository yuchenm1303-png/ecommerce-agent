from __future__ import annotations

import time
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject, QRect, QTimer
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QFrame, QMainWindow, QWidget


# Direct behavioral port of the captured nekro.top / imsyy-home .cards states:
#   scale(1) -> hover scale(1.01) -> active scale(.98), transition .3s.
#
# Performance note: unlike the previous implementation, there is no permanent
# 16 ms timer and no full scan of every card on each mouse move. The timer runs
# only while one of the short 0.3 s transitions is actually active.

_GLASS_NAMES = {"glassCard", "heroCard", "statusCard", "microCard"}
_NORMAL_SCALE = 1.0
_HOVER_SCALE = 1.01
_ACTIVE_SCALE = 0.98
_TRANSITION_SECONDS = 0.300
_FRAME_MS = 16


def _css_ease(progress: float) -> float:
    """Approximate CSS default ease cubic-bezier(.25,.1,.25,1)."""

    p = min(1.0, max(0.0, progress))
    x1, y1, x2, y2 = 0.25, 0.10, 0.25, 1.00

    def cubic(t: float, a: float, b: float) -> float:
        omt = 1.0 - t
        return 3.0 * omt * omt * t * a + 3.0 * omt * t * t * b + t * t * t

    lo, hi = 0.0, 1.0
    for _ in range(10):
        mid = (lo + hi) * 0.5
        if cubic(mid, x1, x2) < p:
            lo = mid
        else:
            hi = mid
    return cubic((lo + hi) * 0.5, y1, y2)


def _scaled_rect(rect: QRect, scale: float) -> QRect:
    if abs(scale - 1.0) < 0.0001:
        return QRect(rect)
    center = rect.center()
    width = max(1, round(rect.width() * scale))
    height = max(1, round(rect.height() * scale))
    result = QRect(0, 0, width, height)
    result.moveCenter(center)
    return result


@dataclass(slots=True)
class _CardState:
    frame: QFrame
    base_geometry: QRect
    current_scale: float = 1.0
    start_scale: float = 1.0
    target_scale: float = 1.0
    started_at: float = 0.0
    animating: bool = False

    def begin(self, target: float) -> bool:
        if abs(target - self.target_scale) < 0.0001 and self.animating:
            return False
        if abs(target - self.current_scale) < 0.0001:
            self.current_scale = target
            self.target_scale = target
            self.animating = False
            return False
        self.start_scale = self.current_scale
        self.target_scale = target
        self.started_at = time.monotonic()
        self.animating = True
        return True


class NekroCardInteractionController(QObject):
    """Source card interactions with idle-zero animation work."""

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self.window = window
        self.states: dict[QFrame, _CardState] = {}
        self.hovered: QFrame | None = None
        self.pressed: QFrame | None = None
        self._capture_scheduled = False

        for frame in window.findChildren(QFrame):
            if frame.objectName() in _GLASS_NAMES:
                self.states[frame] = _CardState(frame=frame, base_geometry=QRect(frame.geometry()))

        window.setMouseTracking(True)
        for widget in window.findChildren(QWidget):
            widget.setMouseTracking(True)

        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

        self.timer = QTimer(self)
        self.timer.setInterval(_FRAME_MS)
        self.timer.timeout.connect(self._tick)
        # Deliberately not started here.

        window.destroyed.connect(self._cleanup)
        self._schedule_capture()

    def _schedule_capture(self) -> None:
        if self._capture_scheduled:
            return
        self._capture_scheduled = True
        QTimer.singleShot(0, self._capture_layout_geometries)

    def _capture_layout_geometries(self) -> None:
        self._capture_scheduled = False
        if self.hovered is not None or self.pressed is not None or self.timer.isActive():
            return
        for frame, state in self.states.items():
            if not frame.isVisible():
                continue
            state.base_geometry = QRect(frame.geometry())
            state.current_scale = 1.0
            state.start_scale = 1.0
            state.target_scale = 1.0
            state.animating = False

    def _card_from_widget(self, watched: QObject) -> QFrame | None:
        widget = watched if isinstance(watched, QWidget) else None
        while widget is not None:
            if isinstance(widget, QFrame) and widget in self.states:
                return widget
            widget = widget.parentWidget()
        return None

    def _ensure_timer(self) -> None:
        if any(state.animating for state in self.states.values()) and not self.timer.isActive():
            self.timer.start()

    def _set_hovered(self, frame: QFrame | None) -> None:
        if frame is self.hovered:
            return
        previous = self.hovered
        self.hovered = frame

        if previous is not None and previous is not self.pressed:
            self.states[previous].begin(_NORMAL_SCALE)

        if frame is not None:
            state = self.states[frame]
            if abs(state.current_scale - 1.0) < 0.0001 and not state.animating:
                state.base_geometry = QRect(frame.geometry())
            if frame is not self.pressed:
                state.begin(_HOVER_SCALE)
        self._ensure_timer()

    def _press(self, frame: QFrame | None) -> None:
        self.pressed = frame
        if frame is not None:
            self.states[frame].begin(_ACTIVE_SCALE)
            self._ensure_timer()

    def _release(self, frame_under_pointer: QFrame | None) -> None:
        pressed = self.pressed
        self.pressed = None
        self._set_hovered(frame_under_pointer)
        if pressed is not None:
            self.states[pressed].begin(
                _HOVER_SCALE if pressed is frame_under_pointer else _NORMAL_SCALE
            )
            self._ensure_timer()

    def _tick(self) -> None:
        now = time.monotonic()
        still_animating = False

        for state in self.states.values():
            if not state.animating:
                continue

            raw = (now - state.started_at) / _TRANSITION_SECONDS
            if raw >= 1.0:
                state.current_scale = state.target_scale
                state.animating = False
            else:
                eased = _css_ease(raw)
                state.current_scale = state.start_scale + (
                    state.target_scale - state.start_scale
                ) * eased
                still_animating = True

            desired = _scaled_rect(state.base_geometry, state.current_scale)
            if state.frame.geometry() != desired:
                state.frame.setGeometry(desired)

        if not still_animating and not any(state.animating for state in self.states.values()):
            self.timer.stop()
            if self.hovered is None and self.pressed is None:
                self._schedule_capture()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        event_type = event.type()

        if watched is self.window and event_type in (QEvent.Resize, QEvent.Show):
            if self.hovered is None and self.pressed is None and not self.timer.isActive():
                self._schedule_capture()

        if isinstance(event, QMouseEvent):
            frame = self._card_from_widget(watched)
            if event_type == QEvent.MouseMove:
                self._set_hovered(frame)
            elif event_type == QEvent.MouseButtonPress:
                self._set_hovered(frame)
                self._press(frame)
            elif event_type == QEvent.MouseButtonRelease:
                self._release(frame)

        if watched is self.window and event_type == QEvent.Leave:
            self.pressed = None
            self._set_hovered(None)

        return False

    def _cleanup(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        self.timer.stop()


def install_nekro_card_fx(window: QMainWindow) -> NekroCardInteractionController:
    controller = NekroCardInteractionController(window)
    window._nekro_card_fx = controller  # type: ignore[attr-defined]
    return controller
