from __future__ import annotations

import time
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject, QRectF, Qt, QTimer
from PySide6.QtGui import QMouseEvent, QPainter
from PySide6.QtWidgets import QApplication, QFrame, QGraphicsEffect, QMainWindow, QWidget


# Direct behavioral port of the captured nekro.top / imsyy-home card states:
#   scale(1) -> hover scale(1.01) -> active scale(.98), transition .3s.
#
# QWidget geometry is deliberately never animated. Mutating geometry inside a
# layout makes Qt relayout the same card while the animation is trying to resize
# it, which causes visible stepping and unnecessary work. A lightweight graphics
# effect scales only the final card rendering while layout/hit-testing stays
# stable. One precise 60-fps timer runs only while a transition is active.

_GLASS_NAMES = {"glassCard", "heroCard", "statusCard", "microCard"}
_NORMAL_SCALE = 1.0
_HOVER_SCALE = 1.01
_ACTIVE_SCALE = 0.98
_TRANSITION_SECONDS = 0.300
_FRAME_MS = 16


def _css_ease(progress: float) -> float:
    """CSS default ease cubic-bezier(.25,.1,.25,1)."""

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


class CardScaleEffect(QGraphicsEffect):
    """Scale the rendered card around its center without changing geometry."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._scale = 1.0

    @property
    def scale(self) -> float:
        return self._scale

    def set_scale(self, value: float) -> None:
        value = float(value)
        if abs(value - self._scale) < 0.0001:
            return
        self._scale = value
        self.updateBoundingRect()
        self.update()

    def boundingRectFor(self, source_rect: QRectF) -> QRectF:  # noqa: N802
        if self._scale <= 1.0:
            return QRectF(source_rect)
        extra_x = source_rect.width() * (self._scale - 1.0) * 0.5
        extra_y = source_rect.height() * (self._scale - 1.0) * 0.5
        return source_rect.adjusted(-extra_x, -extra_y, extra_x, extra_y)

    def draw(self, painter: QPainter) -> None:
        if abs(self._scale - 1.0) < 0.0001:
            self.drawSource(painter)
            return

        rect = self.sourceBoundingRect(Qt.LogicalCoordinates)
        center = rect.center()
        painter.save()
        painter.translate(center)
        painter.scale(self._scale, self._scale)
        painter.translate(-center)
        self.drawSource(painter)
        painter.restore()


@dataclass(slots=True)
class _CardState:
    frame: QFrame
    effect: CardScaleEffect
    current_scale: float = 1.0
    start_scale: float = 1.0
    target_scale: float = 1.0
    started_at: float = 0.0
    animating: bool = False

    def begin(self, target: float) -> bool:
        target = float(target)
        if abs(target - self.target_scale) < 0.0001 and self.animating:
            return False
        if abs(target - self.current_scale) < 0.0001:
            self.current_scale = target
            self.start_scale = target
            self.target_scale = target
            self.animating = False
            self.effect.set_scale(target)
            return False

        # Every state change starts from the exact scale currently on screen.
        # Pressing/releasing midway through another transition therefore remains
        # continuous instead of snapping to the previous target.
        self.start_scale = self.current_scale
        self.target_scale = target
        self.started_at = time.monotonic()
        self.animating = True
        return True


class NekroCardInteractionController(QObject):
    """Original card hover/press semantics with stable layout and 60-fps paint."""

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self.window = window
        self.states: dict[QFrame, _CardState] = {}
        self.hovered: QFrame | None = None
        self.pressed: QFrame | None = None

        for frame in window.findChildren(QFrame):
            if frame.objectName() not in _GLASS_NAMES:
                continue
            effect = CardScaleEffect(frame)
            frame.setGraphicsEffect(effect)
            self.states[frame] = _CardState(frame=frame, effect=effect)

        window.setMouseTracking(True)
        for widget in window.findChildren(QWidget):
            widget.setMouseTracking(True)

        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.setInterval(_FRAME_MS)
        self.timer.timeout.connect(self._tick)
        # Idle means zero animation timer work.

        window.destroyed.connect(self._cleanup)

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

        if frame is not None and frame is not self.pressed:
            self.states[frame].begin(_HOVER_SCALE)

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
        any_animating = False

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
                any_animating = True

            state.effect.set_scale(state.current_scale)

        if not any_animating and not any(state.animating for state in self.states.values()):
            self.timer.stop()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        event_type = event.type()

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
            pressed = self.pressed
            self.pressed = None
            self._set_hovered(None)
            if pressed is not None:
                self.states[pressed].begin(_NORMAL_SCALE)
                self._ensure_timer()

        return False

    def _cleanup(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        self.timer.stop()
        for state in self.states.values():
            state.effect.set_scale(1.0)


def install_nekro_card_fx(window: QMainWindow) -> NekroCardInteractionController:
    controller = NekroCardInteractionController(window)
    window._nekro_card_fx = controller  # type: ignore[attr-defined]
    return controller
