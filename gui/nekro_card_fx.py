from __future__ import annotations

import time
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, QTimer
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QFrame, QMainWindow, QWidget


# Direct behavioral port of the captured nekro.top / imsyy-home .cards states:
#
# .cards {
#   transform: scale(1);
#   transition: backdrop-filter .3s, transform .3s;
# }
# .cards:hover  { transform: scale(1.01); }
# .cards:active { transform: scale(.98); }
#
# Qt Style Sheets do not implement CSS transforms, so this controller applies
# the same scale states to the actual QWidget geometry without changing any
# ecommerce-agent business or result logic.

_GLASS_NAMES = {"glassCard", "heroCard", "statusCard", "microCard"}
_NORMAL_SCALE = 1.0
_HOVER_SCALE = 1.01
_ACTIVE_SCALE = 0.98
_TRANSITION_SECONDS = 0.300


def _css_ease(progress: float) -> float:
    """Approximate CSS's default `ease` cubic-bezier(.25,.1,.25,1)."""

    # Solve x(t)=progress for the CSS cubic Bezier, then return y(t).
    p = min(1.0, max(0.0, progress))
    x1, y1, x2, y2 = 0.25, 0.10, 0.25, 1.00

    def cubic(t: float, a: float, b: float) -> float:
        omt = 1.0 - t
        return 3.0 * omt * omt * t * a + 3.0 * omt * t * t * b + t * t * t

    lo, hi = 0.0, 1.0
    for _ in range(12):
        mid = (lo + hi) * 0.5
        if cubic(mid, x1, x2) < p:
            lo = mid
        else:
            hi = mid
    return cubic((lo + hi) * 0.5, y1, y2)


def _scaled_rect(rect: QRect, scale: float) -> QRect:
    if scale == 1.0:
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

    def begin(self, target: float) -> None:
        if abs(target - self.current_scale) < 0.0001:
            self.target_scale = target
            self.animating = False
            return
        self.start_scale = self.current_scale
        self.target_scale = target
        self.started_at = time.monotonic()
        self.animating = True


class NekroCardInteractionController(QObject):
    """Apply the source site's exact card hover/active scale semantics."""

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self.window = window
        self.states: dict[QFrame, _CardState] = {}
        self.hovered: QFrame | None = None
        self.pressed: QFrame | None = None
        self._internal_geometry_write = False

        for frame in window.findChildren(QFrame):
            if frame.objectName() in _GLASS_NAMES:
                self.states[frame] = _CardState(frame=frame, base_geometry=QRect(frame.geometry()))

        # MouseMove is disabled by default on many QWidget subclasses. The web
        # source receives pointer movement continuously, so enable equivalent
        # tracking throughout the GUI presentation tree.
        window.setMouseTracking(True)
        for widget in window.findChildren(QWidget):
            widget.setMouseTracking(True)

        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

        window.destroyed.connect(self._cleanup)
        QTimer.singleShot(0, self._capture_layout_geometries)

    def _capture_layout_geometries(self) -> None:
        if self.hovered is not None or self.pressed is not None:
            return
        for frame, state in self.states.items():
            if frame.isVisible():
                state.base_geometry = QRect(frame.geometry())
                state.current_scale = 1.0
                state.start_scale = 1.0
                state.target_scale = 1.0
                state.animating = False

    def _global_rect(self, frame: QFrame) -> QRect:
        top_left = frame.mapToGlobal(QPoint(0, 0))
        return QRect(top_left, frame.size())

    def _card_at(self, global_point: QPoint) -> QFrame | None:
        matches: list[QFrame] = []
        for frame in self.states:
            if frame.isVisible() and self._global_rect(frame).contains(global_point):
                matches.append(frame)
        if not matches:
            return None
        # If cards ever nest, the smallest visible surface is the user's target.
        return min(matches, key=lambda frame: frame.width() * frame.height())

    def _set_hovered(self, frame: QFrame | None) -> None:
        if frame is self.hovered:
            return

        previous = self.hovered
        self.hovered = frame

        if previous is not None and previous in self.states and previous is not self.pressed:
            self.states[previous].begin(_NORMAL_SCALE)

        if frame is not None and frame in self.states:
            state = self.states[frame]
            # Capture the layout-owned rectangle only when entering from normal
            # state. This prevents the 1.01 geometry from becoming the new base.
            if abs(state.current_scale - 1.0) < 0.0001 and not state.animating:
                state.base_geometry = QRect(frame.geometry())
            if frame is not self.pressed:
                state.begin(_HOVER_SCALE)

    def _press(self, frame: QFrame | None) -> None:
        self.pressed = frame
        if frame is not None and frame in self.states:
            self.states[frame].begin(_ACTIVE_SCALE)

    def _release(self, global_point: QPoint) -> None:
        pressed = self.pressed
        self.pressed = None
        under_pointer = self._card_at(global_point)
        self._set_hovered(under_pointer)
        if pressed is not None and pressed in self.states:
            self.states[pressed].begin(_HOVER_SCALE if pressed is under_pointer else _NORMAL_SCALE)

    def _tick(self) -> None:
        now = time.monotonic()
        any_animating = False
        self._internal_geometry_write = True
        try:
            for state in self.states.values():
                if state.animating:
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

                desired = _scaled_rect(state.base_geometry, state.current_scale)
                if state.frame.geometry() != desired:
                    state.frame.setGeometry(desired)
        finally:
            self._internal_geometry_write = False

        # Keep exact base rectangles after everything returns to scale(1).
        if not any_animating and self.hovered is None and self.pressed is None:
            QTimer.singleShot(0, self._capture_layout_geometries)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        event_type = event.type()

        if watched is self.window and event_type in (QEvent.Resize, QEvent.Show):
            if self.hovered is None and self.pressed is None:
                QTimer.singleShot(0, self._capture_layout_geometries)

        if isinstance(event, QMouseEvent):
            global_point = event.globalPosition().toPoint()
            if event_type == QEvent.MouseMove:
                self._set_hovered(self._card_at(global_point))
            elif event_type == QEvent.MouseButtonPress:
                frame = self._card_at(global_point)
                self._set_hovered(frame)
                self._press(frame)
            elif event_type == QEvent.MouseButtonRelease:
                self._release(global_point)

        if watched is self.window and event_type == QEvent.Leave:
            self.pressed = None
            self._set_hovered(None)

        return False

    def _cleanup(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        self.timer.stop()
        self._internal_geometry_write = True
        try:
            for state in self.states.values():
                if state.frame:
                    state.frame.setGeometry(state.base_geometry)
        finally:
            self._internal_geometry_write = False


def install_nekro_card_fx(window: QMainWindow) -> NekroCardInteractionController:
    controller = NekroCardInteractionController(window)
    window._nekro_card_fx = controller  # type: ignore[attr-defined]
    return controller
