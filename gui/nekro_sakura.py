from __future__ import annotations

import base64
import math
import random
import time
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject, QPointF, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QPainter, QPixmap, QRegion
from PySide6.QtWidgets import QMainWindow, QWidget


# Production-faithful port of nekro.top's captured canvas_sakura implementation.
# Visual content, motion closures and respawn behavior stay the same. The local
# GUI intentionally uses 12 particles instead of production's 50.
#
# Performance adaptation for Qt: paint at ~30 fps instead of repainting a full
# transparent 1920x1080 surface at 60 fps. Motion is time-scaled to keep the
# same on-screen speed, and only old/new petal rectangles are invalidated.

SAKURA_COUNT = 12
_FRAME_MS = 33
_BASE_FRAME_SECONDS = 1.0 / 60.0
_MAX_DT_SECONDS = 0.050

_SAKURA_PNG_B64 = (
    "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"
)


def _load_source_sprite() -> QPixmap:
    pixmap = QPixmap()
    if not pixmap.loadFromData(base64.b64decode(_SAKURA_PNG_B64), "PNG"):
        raise RuntimeError("Failed to load embedded nekro.top sakura sprite")
    return pixmap


def _petal_dirty_rect(x: float, y: float, s: float) -> QRect:
    # Rotation occurs around the image's top-left corner, so sqrt(2)*40*s plus
    # a small antialiasing margin safely covers every orientation.
    extent = max(4, int(math.ceil(57.0 * max(0.05, s))) + 3)
    return QRect(int(x) - extent, int(y) - extent, extent * 2 + 2, extent * 2 + 2)


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

    def update(self, width: int, height: int, frame_scale: float = 1.0) -> None:
        # Same closure velocities as production; frame_scale only compensates
        # for drawing fewer Qt frames so visible speed remains unchanged.
        self.x += (0.5 * self.fnx_n - 1.7) * frame_scale
        self.y += self.fny_n * frame_scale
        self.r += self.fnr_n * frame_scale

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


class NekroSakuraOverlay(QWidget):
    """Original sakura artwork/motion with Qt-friendly dirty repainting."""

    def __init__(self, window: QMainWindow, count: int = SAKURA_COUNT) -> None:
        central = window.centralWidget()
        super().__init__(central)
        self.window = window
        self.count = max(0, int(count))
        self.sprite = _load_source_sprite()
        self._last_frame = time.monotonic()

        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.NoFocus)

        width = max(1, central.width() if central else window.width())
        height = max(1, central.height() if central else window.height())
        self.particles = [SakuraParticle.random_in_viewport(width, height) for _ in range(self.count)]

        self.timer = QTimer(self)
        self.timer.setInterval(_FRAME_MS)
        self.timer.timeout.connect(self._frame)
        self.timer.start()

        window.installEventFilter(self)
        QTimer.singleShot(0, self.sync_geometry)

    def sync_geometry(self) -> None:
        central = self.window.centralWidget()
        if central is None:
            return
        self.setGeometry(central.rect())
        self.raise_()
        self.show()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self.window:
            event_type = event.type()
            if event_type in (QEvent.Resize, QEvent.Show):
                QTimer.singleShot(0, self.sync_geometry)
                if not self.timer.isActive():
                    self._last_frame = time.monotonic()
                    self.timer.start()
            elif event_type == QEvent.Hide:
                self.timer.stop()
            elif event_type == QEvent.WindowStateChange:
                if self.window.isMinimized():
                    self.timer.stop()
                elif not self.timer.isActive():
                    self._last_frame = time.monotonic()
                    self.timer.start()
        return False

    def _frame(self) -> None:
        if not self.isVisible() or self.window.isMinimized():
            self.timer.stop()
            return

        now = time.monotonic()
        dt = min(_MAX_DT_SECONDS, max(_BASE_FRAME_SECONDS, now - self._last_frame))
        self._last_frame = now
        frame_scale = dt / _BASE_FRAME_SECONDS

        width = max(1, self.width())
        height = max(1, self.height())
        dirty = QRegion()
        for particle in self.particles:
            dirty = dirty.united(_petal_dirty_rect(particle.x, particle.y, particle.s))
            particle.update(width, height, frame_scale)
            dirty = dirty.united(_petal_dirty_rect(particle.x, particle.y, particle.s))

        if not dirty.isEmpty():
            self.update(dirty)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setClipRegion(event.region())
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        for particle in self.particles:
            draw_size = 40.0 * particle.s
            if draw_size <= 0.1:
                continue
            painter.save()
            painter.translate(QPointF(particle.x, particle.y))
            painter.rotate(math.degrees(particle.r))
            painter.drawPixmap(
                QRectF(0.0, 0.0, draw_size, draw_size),
                self.sprite,
                QRectF(self.sprite.rect()),
            )
            painter.restore()

        painter.end()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.window.removeEventFilter(self)
        self.timer.stop()
        super().closeEvent(event)


def install_nekro_sakura(
    window: QMainWindow,
    *,
    count: int = SAKURA_COUNT,
) -> NekroSakuraOverlay:
    overlay = NekroSakuraOverlay(window, count=count)
    window._nekro_sakura = overlay  # type: ignore[attr-defined]
    return overlay
