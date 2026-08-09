from __future__ import annotations

import math

from PySide6.QtCore import QEvent, QObject, QPointF, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QMouseEvent, QPainter
from PySide6.QtWidgets import QApplication, QMainWindow, QWidget


_FRAME_MS = 16
_FOLLOW_FACTOR = 0.35
_CURSOR_RADIUS = 9.0
_ACTIVE_RADIUS = 4.5
_SETTLE_DISTANCE = 0.35
_DIRTY_PAD = 4


def _circle_rect(point: QPointF | None, radius: float = _CURSOR_RADIUS) -> QRect:
    if point is None:
        return QRect()
    pad = int(math.ceil(radius)) + _DIRTY_PAD
    return QRect(
        int(math.floor(point.x())) - pad,
        int(math.floor(point.y())) - pad,
        pad * 2 + 2,
        pad * 2 + 2,
    )


class OptimizedNekroCursorOverlay(QWidget):
    """Source-faithful follower circle without permanent full-window repaint."""

    def __init__(self, window: QMainWindow) -> None:
        central = window.centralWidget()
        super().__init__(central)
        self.window = window
        self.target: QPointF | None = None
        self.current: QPointF | None = None
        self.visible_cursor = False
        self.pressed = False

        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.NoFocus)

        self.timer = QTimer(self)
        self.timer.setInterval(_FRAME_MS)
        self.timer.timeout.connect(self._tick)

        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        window.destroyed.connect(self._cleanup)
        QTimer.singleShot(0, self.sync_geometry)

    def sync_geometry(self) -> None:
        central = self.window.centralWidget()
        if central is None:
            return
        self.setGeometry(central.rect())
        self.raise_()
        self.show()

    def _point_in_central(self, global_point: QPointF) -> QPointF | None:
        central = self.window.centralWidget()
        if central is None or not self.window.isVisible():
            return None
        local = central.mapFromGlobal(global_point.toPoint())
        if not central.rect().contains(local):
            return None
        return QPointF(local)

    def _invalidate_cursor(self, old: QPointF | None, new: QPointF | None) -> None:
        dirty = _circle_rect(old)
        new_rect = _circle_rect(new)
        dirty = dirty.united(new_rect) if not dirty.isNull() else new_rect
        if not dirty.isNull():
            self.update(dirty)

    def set_target(self, point: QPointF) -> None:
        self.target = QPointF(point)
        if self.current is None:
            self.current = QPointF(point)
        self.visible_cursor = True
        self._invalidate_cursor(self.current, self.current)
        if not self.timer.isActive():
            self.timer.start()

    def set_pressed(self, pressed: bool) -> None:
        if self.pressed == pressed:
            return
        self.pressed = pressed
        self._invalidate_cursor(self.current, self.current)

    def hide_cursor(self) -> None:
        if not self.visible_cursor:
            return
        old = QPointF(self.current) if self.current is not None else None
        self.visible_cursor = False
        self.timer.stop()
        self._invalidate_cursor(old, old)

    def _tick(self) -> None:
        if self.target is None or self.current is None or not self.visible_cursor:
            self.timer.stop()
            return

        old = QPointF(self.current)
        dx = self.target.x() - self.current.x()
        dy = self.target.y() - self.current.y()
        distance = math.hypot(dx, dy)

        if distance <= _SETTLE_DISTANCE:
            self.current = QPointF(self.target)
            self._invalidate_cursor(old, self.current)
            self.timer.stop()
            return

        self.current = QPointF(
            self.current.x() + dx * _FOLLOW_FACTOR,
            self.current.y() + dy * _FOLLOW_FACTOR,
        )
        self._invalidate_cursor(old, self.current)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setClipRegion(event.region())

        # WA_NoSystemBackground avoids a full-surface clear. Explicitly clear
        # only the invalidated cursor rectangles so the old follower never
        # leaves trails while retaining the small dirty-region repaint cost.
        painter.setCompositionMode(QPainter.CompositionMode_Source)
        for rect in event.region():
            painter.fillRect(rect, QColor(0, 0, 0, 0))
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)

        if self.visible_cursor and self.current is not None:
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setPen(Qt.NoPen)
            radius = _ACTIVE_RADIUS if self.pressed else _CURSOR_RADIUS
            alpha = 128 if self.pressed else 64
            painter.setBrush(QColor(255, 255, 255, alpha))
            painter.drawEllipse(self.current, radius, radius)
        painter.end()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        event_type = event.type()

        if watched is self.window and event_type in (QEvent.Resize, QEvent.Show):
            QTimer.singleShot(0, self.sync_geometry)

        if isinstance(event, QMouseEvent):
            local = self._point_in_central(event.globalPosition())
            if local is not None:
                if event_type == QEvent.MouseMove:
                    self.set_target(local)
                elif event_type == QEvent.MouseButtonPress:
                    self.set_target(local)
                    self.set_pressed(True)
                elif event_type == QEvent.MouseButtonRelease:
                    self.set_target(local)
                    self.set_pressed(False)
            elif event_type == QEvent.MouseMove:
                self.hide_cursor()

        if watched is self.window and event_type == QEvent.Leave:
            self.hide_cursor()
        return False

    def _cleanup(self) -> None:
        self.timer.stop()
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)


def install_optimized_cursor_fx(window: QMainWindow) -> OptimizedNekroCursorOverlay:
    overlay = OptimizedNekroCursorOverlay(window)
    window._optimized_nekro_cursor = overlay  # type: ignore[attr-defined]
    return overlay
