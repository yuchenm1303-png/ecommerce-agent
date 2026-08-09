from __future__ import annotations

import math

from PySide6.QtCore import QEvent, QObject, QPointF, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPixmap, QRegion
from PySide6.QtWidgets import QApplication, QMainWindow, QWidget

from .nekro_sakura import SakuraParticle, _load_source_sprite


# One lightweight presentation surface for both of the continuously animated
# effects. The browser source uses requestAnimationFrame, so target ~60 fps and
# advance the original sakura closures exactly once per rendered frame.
_FRAME_MS = 16
_FOLLOW_FACTOR = 0.35
_CURSOR_RADIUS = 9.0
_ACTIVE_CURSOR_RADIUS = 4.5
_CURSOR_SETTLE = 0.35
_DIRTY_PAD = 4


def _petal_rect(x: float, y: float, s: float) -> QRect:
    # The 40*s source sprite rotates around its top-left origin. sqrt(2)*size
    # plus a small antialiasing margin covers all orientations.
    extent = max(4, int(math.ceil(57.0 * max(0.05, s))) + 3)
    return QRect(int(x) - extent, int(y) - extent, extent * 2 + 2, extent * 2 + 2)


def _cursor_rect(point: QPointF | None, radius: float = _CURSOR_RADIUS) -> QRect:
    if point is None:
        return QRect()
    pad = int(math.ceil(radius)) + _DIRTY_PAD
    return QRect(
        int(math.floor(point.x())) - pad,
        int(math.floor(point.y())) - pad,
        pad * 2 + 2,
        pad * 2 + 2,
    )


class NekroOverlay60(QWidget):
    """Single 60-fps transparent layer for source sakura + follower cursor.

    Important Qt detail: do NOT use WA_NoSystemBackground and do NOT erase the
    dirty region with CompositionMode_Source. On Windows that can turn a child
    overlay into an opaque black backing surface. Let Qt's backing store
    recompose the parent/siblings underneath the dirty region, then paint only
    the effects on top.
    """

    def __init__(self, window: QMainWindow, *, sakura_count: int = 12) -> None:
        central = window.centralWidget()
        super().__init__(central)
        self.window = window
        self.count = max(0, int(sakura_count))

        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, False)
        self.setAutoFillBackground(False)
        self.setFocusPolicy(Qt.NoFocus)

        width = max(1, central.width() if central else window.width())
        height = max(1, central.height() if central else window.height())
        self.particles = [
            SakuraParticle.random_in_viewport(width, height)
            for _ in range(self.count)
        ]

        # The production bundle's sprite is already losslessly reduced to the
        # source renderer's maximum 40x40 draw size. Cache each integer display
        # size once so a 60-fps frame only rotates/blits pixmaps; it does not
        # resample twelve images every frame.
        source = _load_source_sprite()
        self._sprite_cache: dict[int, QPixmap] = {0: QPixmap()}
        for size in range(1, 41):
            self._sprite_cache[size] = source.scaled(
                size,
                size,
                Qt.IgnoreAspectRatio,
                Qt.SmoothTransformation,
            )

        self.cursor_target: QPointF | None = None
        self.cursor_current: QPointF | None = None
        self.cursor_visible = False
        self.cursor_pressed = False

        window.setMouseTracking(True)
        for widget in window.findChildren(QWidget):
            widget.setMouseTracking(True)

        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.setInterval(_FRAME_MS)
        self.timer.timeout.connect(self._frame)
        self.timer.start()

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

    def _set_cursor_target(self, point: QPointF) -> None:
        self.cursor_target = QPointF(point)
        if self.cursor_current is None:
            self.cursor_current = QPointF(point)
        self.cursor_visible = True

    def _frame(self) -> None:
        if not self.isVisible() or self.window.isMinimized():
            if self.timer.isActive():
                self.timer.stop()
            return

        width = max(1, self.width())
        height = max(1, self.height())
        dirty = QRegion()

        # requestAnimationFrame semantics from the captured production code:
        # exactly one original motion update per rendered frame. No catch-up
        # multiplier, which prevents visible position jumps after a delayed tick.
        for particle in self.particles:
            dirty = dirty.united(_petal_rect(particle.x, particle.y, particle.s))
            particle.update(width, height, 1.0)
            dirty = dirty.united(_petal_rect(particle.x, particle.y, particle.s))

        old_cursor = QPointF(self.cursor_current) if self.cursor_current is not None else None
        if (
            self.cursor_visible
            and self.cursor_target is not None
            and self.cursor_current is not None
        ):
            dx = self.cursor_target.x() - self.cursor_current.x()
            dy = self.cursor_target.y() - self.cursor_current.y()
            if math.hypot(dx, dy) <= _CURSOR_SETTLE:
                self.cursor_current = QPointF(self.cursor_target)
            else:
                self.cursor_current = QPointF(
                    self.cursor_current.x() + dx * _FOLLOW_FACTOR,
                    self.cursor_current.y() + dy * _FOLLOW_FACTOR,
                )

        if old_cursor is not None:
            dirty = dirty.united(_cursor_rect(old_cursor))
        if self.cursor_visible and self.cursor_current is not None:
            dirty = dirty.united(_cursor_rect(self.cursor_current))

        if not dirty.isEmpty():
            self.update(dirty)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setClipRegion(event.region())
        painter.setRenderHint(QPainter.SmoothPixmapTransform, False)

        for particle in self.particles:
            size = max(1, min(40, int(round(40.0 * particle.s))))
            sprite = self._sprite_cache[size]
            painter.save()
            painter.translate(QPointF(particle.x, particle.y))
            painter.rotate(math.degrees(particle.r))
            painter.drawPixmap(0, 0, sprite)
            painter.restore()

        if self.cursor_visible and self.cursor_current is not None:
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setPen(Qt.NoPen)
            radius = _ACTIVE_CURSOR_RADIUS if self.cursor_pressed else _CURSOR_RADIUS
            alpha = 128 if self.cursor_pressed else 64
            painter.setBrush(QColor(255, 255, 255, alpha))
            painter.drawEllipse(self.cursor_current, radius, radius)

        painter.end()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        event_type = event.type()

        if watched is self.window:
            if event_type in (QEvent.Resize, QEvent.Show):
                QTimer.singleShot(0, self.sync_geometry)
                if not self.timer.isActive():
                    self.timer.start()
            elif event_type == QEvent.Hide:
                self.timer.stop()
            elif event_type == QEvent.WindowStateChange:
                if self.window.isMinimized():
                    self.timer.stop()
                elif not self.timer.isActive():
                    self.timer.start()
            elif event_type == QEvent.Leave:
                self.cursor_visible = False

        if isinstance(event, QMouseEvent):
            local = self._point_in_central(event.globalPosition())
            if local is not None:
                if event_type == QEvent.MouseMove:
                    self._set_cursor_target(local)
                elif event_type == QEvent.MouseButtonPress:
                    self._set_cursor_target(local)
                    self.cursor_pressed = True
                elif event_type == QEvent.MouseButtonRelease:
                    self._set_cursor_target(local)
                    self.cursor_pressed = False
            elif event_type == QEvent.MouseMove:
                self.cursor_visible = False

        return False

    def _cleanup(self) -> None:
        self.timer.stop()
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)


def install_nekro_overlay60(
    window: QMainWindow,
    *,
    sakura_count: int = 12,
) -> NekroOverlay60:
    overlay = NekroOverlay60(window, sakura_count=sakura_count)
    window._nekro_overlay60 = overlay  # type: ignore[attr-defined]
    return overlay
