from __future__ import annotations

import math
import random
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import QMainWindow, QWidget


# Direct motion-model port of the canvas_sakura routine captured from the
# user's current nekro.top production bundle.  The browser build creates 50
# particles; the local test GUI intentionally uses far fewer to keep the effect
# quiet and cheap while preserving the original movement/reset behavior.
#
# Browser source semantics:
#   x   = random * innerWidth
#   y   = random * innerHeight
#   s   = random
#   r   = 6 * random
#   fnx = x + 0.5 * (random - 0.5) - 1.7
#   fny = y + 1.5 + 0.7 * random
#   fnr = r + 0.03 * random
#   out of bounds -> respawn from top (60%) or right edge (40%)
#   requestAnimationFrame -> clear -> update -> draw

SAKURA_COUNT = 12  # nekro.top production bundle: 50
_FRAME_MS = 16


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
        # Exact Ub("fnx"), Ub("fny"), Ub("fnr") behavior from the bundle.
        self.x = self.x + 0.5 * self.fnx_n - 1.7
        self.y = self.y + self.fny_n
        self.r = self.r + self.fnr_n

        if self.x > width or self.x < 0 or self.y > height or self.y < 0:
            self._respawn(width, height)

    def _respawn(self, width: int, height: int) -> None:
        # The production code keeps the motion functions attached to the
        # particle and only re-randomizes x/y/size/rotation on respawn.
        if random.random() > 0.4:
            self.x = random.random() * width
            self.y = 0.0
        else:
            self.x = float(width)
            self.y = random.random() * height
        self.s = random.random()
        self.r = 6.0 * random.random()


class NekroSakuraOverlay(QWidget):
    """Qt canvas equivalent of nekro.top's original canvas_sakura loop."""

    def __init__(self, window: QMainWindow, count: int = SAKURA_COUNT) -> None:
        central = window.centralWidget()
        super().__init__(central)
        self.window = window
        self.count = max(0, int(count))
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.NoFocus)

        width = max(1, central.width() if central else window.width())
        height = max(1, central.height() if central else window.height())
        self.particles = [
            SakuraParticle.random_in_viewport(width, height)
            for _ in range(self.count)
        ]

        self.timer = QTimer(self)
        self.timer.setInterval(_FRAME_MS)
        self.timer.timeout.connect(self._frame)
        self.timer.start()
        QTimer.singleShot(0, self.sync_geometry)

    def sync_geometry(self) -> None:
        central = self.window.centralWidget()
        if central is None:
            return
        self.setGeometry(central.rect())
        self.raise_()
        self.show()

    def _frame(self) -> None:
        width = max(1, self.width())
        height = max(1, self.height())
        for particle in self.particles:
            particle.update(width, height)
        self.update()

    @staticmethod
    def _petal_path(size: float) -> QPainterPath:
        """Small local petal sprite; motion/lifecycle remains the original code."""

        path = QPainterPath()
        # A compact teardrop-like silhouette keeps the visual close to the
        # original sakura bitmap without embedding the production site's image.
        path.moveTo(0.0, -size * 0.52)
        path.cubicTo(
            size * 0.52,
            -size * 0.28,
            size * 0.46,
            size * 0.30,
            0.0,
            size * 0.54,
        )
        path.cubicTo(
            -size * 0.46,
            size * 0.30,
            -size * 0.52,
            -size * 0.28,
            0.0,
            -size * 0.52,
        )
        return path

    def paintEvent(self, event) -> None:  # type: ignore[override]
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)

        for particle in self.particles:
            # Original browser drawImage size is 40*s by 40*s.
            size = 40.0 * particle.s
            if size <= 0.4:
                continue
            painter.save()
            painter.translate(QPointF(particle.x, particle.y))
            painter.rotate(math.degrees(particle.r))
            painter.setBrush(QColor(255, 220, 235, 205))
            painter.drawPath(self._petal_path(size))
            painter.restore()

        painter.end()


def install_nekro_sakura(
    window: QMainWindow,
    *,
    count: int = SAKURA_COUNT,
) -> NekroSakuraOverlay:
    overlay = NekroSakuraOverlay(window, count=count)
    window._nekro_sakura = overlay  # type: ignore[attr-defined]
    return overlay
