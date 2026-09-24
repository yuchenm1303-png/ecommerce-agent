from __future__ import annotations

import base64
import math
import random
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPixmap, QRegion
from PySide6.QtWidgets import QMainWindow, QWidget


_FRAME_MS = 16
_FOLLOW_FACTOR = 0.35
_CURSOR_RADIUS = 9.0
_ACTIVE_CURSOR_RADIUS = 4.5
_CURSOR_SETTLE = 0.35
_DIRTY_PAD = 4
_SAKURA_COUNT = 12

_SAKURA_PNG_B64 = (
    "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"
)


def _load_sprite() -> QPixmap:
    pixmap = QPixmap()
    if not pixmap.loadFromData(base64.b64decode(_SAKURA_PNG_B64), "PNG"):
        raise RuntimeError("Failed to load embedded nekro sakura sprite")
    return pixmap


def _petal_rect(x: float, y: float, s: float) -> QRect:
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


@dataclass(slots=True)
class SakuraParticle:
    x: float
    y: float
    s: float
    r: float
    fnx_n: float
    fny_n: float
    fnr_n: float

    @classmethod
    def random_in_viewport(cls, width: int, height: int) -> "SakuraParticle":
        return cls(
            x=random.random() * width,
            y=random.random() * height,
            s=random.random(),
            r=6.0 * random.random(),
            fnx_n=random.random() - 0.5,
            fny_n=1.5 + 0.7 * random.random(),
            fnr_n=0.03 * random.random(),
        )

    def update(self, width: int, height: int) -> None:
        self.x += 0.5 * self.fnx_n - 1.7
        self.y += self.fny_n
        self.r += self.fnr_n
        if self.x > width or self.x < 0 or self.y > height or self.y < 0:
            self._respawn(width, height)

    def _respawn(self, width: int, height: int) -> None:
        if random.random() > 0.4:
            self.x = random.random() * width
            self.y = 0.0
        else:
            self.x = float(width)
            self.y = random.random() * height
        self.s = random.random()
        self.r = 6.0 * random.random()


class NekroEffects(QWidget):
    """Dirty-region sakura/cursor layer driven by the shared presentation clock."""

    def __init__(self, window: QMainWindow, *, sakura_count: int = _SAKURA_COUNT) -> None:
        central = window.centralWidget()
        super().__init__(central)
        self.window = window
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        width = max(1, central.width() if central else window.width())
        height = max(1, central.height() if central else window.height())
        self.particles = [
            SakuraParticle.random_in_viewport(width, height)
            for _ in range(max(0, int(sakura_count)))
        ]

        source = _load_sprite()
        self._sprite_cache: dict[int, QPixmap] = {0: QPixmap()}
        for size in range(1, 41):
            self._sprite_cache[size] = source.scaled(
                size,
                size,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )

        self.cursor_target: QPointF | None = None
        self.cursor_current: QPointF | None = None
        self.cursor_visible = False
        self.cursor_pressed = False
        self._next_frame_s = 0.0

        window.installEventFilter(self)
        window.destroyed.connect(self._cleanup)
        QTimer.singleShot(0, self.sync_geometry)

    def sync_geometry(self) -> None:
        central = self.window.centralWidget()
        if central is None:
            return
        self.setGeometry(central.rect())
        self.raise_()
        self.show()

    def _point_in_central(self, global_point: QPoint) -> QPointF | None:
        central = self.window.centralWidget()
        if central is None or not self.window.isVisible():
            return None
        local = central.mapFromGlobal(global_point)
        if not central.rect().contains(local):
            return None
        return QPointF(local)

    def _sample_pointer(self, global_pos: QPoint, *, left_down: bool) -> None:
        local = self._point_in_central(global_pos)
        if local is None:
            self.cursor_visible = False
            self.cursor_pressed = False
            return
        self.cursor_target = local
        if self.cursor_current is None:
            self.cursor_current = QPointF(local)
        self.cursor_visible = True
        self.cursor_pressed = bool(left_down)

    def presentation_tick(self, global_pos: QPoint, *, left_down: bool, now_s: float) -> None:
        if not self.isVisible() or self.window.isMinimized():
            return
        if now_s < self._next_frame_s:
            return
        self._next_frame_s = now_s + (_FRAME_MS / 1000.0)
        self._sample_pointer(global_pos, left_down=left_down)

        width = max(1, self.width())
        height = max(1, self.height())
        dirty = QRegion()
        for particle in self.particles:
            dirty = dirty.united(_petal_rect(particle.x, particle.y, particle.s))
            particle.update(width, height)
            dirty = dirty.united(_petal_rect(particle.x, particle.y, particle.s))

        old_cursor = QPointF(self.cursor_current) if self.cursor_current is not None else None
        if self.cursor_visible and self.cursor_target is not None and self.cursor_current is not None:
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
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)

        for particle in self.particles:
            size = max(1, min(40, int(round(40.0 * particle.s))))
            sprite = self._sprite_cache[size]
            painter.save()
            painter.translate(QPointF(particle.x, particle.y))
            painter.rotate(math.degrees(particle.r))
            painter.drawPixmap(0, 0, sprite)
            painter.restore()

        if self.cursor_visible and self.cursor_current is not None:
            painter.setPen(Qt.PenStyle.NoPen)
            radius = _ACTIVE_CURSOR_RADIUS if self.cursor_pressed else _CURSOR_RADIUS
            alpha = 128 if self.cursor_pressed else 64
            painter.setBrush(QColor(255, 255, 255, alpha))
            painter.drawEllipse(self.cursor_current, radius, radius)
        painter.end()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is not self.window:
            return False
        event_type = event.type()
        if event_type in (QEvent.Type.Resize, QEvent.Type.Show):
            QTimer.singleShot(0, self.sync_geometry)
        elif event_type == QEvent.Type.Leave:
            self.cursor_visible = False
        return False

    def _cleanup(self) -> None:
        try:
            self.window.removeEventFilter(self)
        except RuntimeError:
            pass


def install_nekro_effects(
    window: QMainWindow,
    *,
    sakura_count: int = _SAKURA_COUNT,
) -> NekroEffects:
    effects = NekroEffects(window, sakura_count=sakura_count)
    window._nekro_effects = effects  # type: ignore[attr-defined]
    return effects


__all__ = ["NekroEffects", "install_nekro_effects"]
