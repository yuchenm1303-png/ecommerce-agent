from __future__ import annotations

import base64
import math
import random
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMainWindow, QWidget


# Production-faithful port of nekro.top's captured canvas_sakura implementation.
# The ONLY intentional behavioral difference is particle count: production uses
# 50; the local ecommerce-agent test GUI uses 12.
#
# The production bundle does NOT draw a geometric petal. It creates `Fb = new
# Image`, assigns a PNG data URI, then calls:
#
#   drawImage(Fb, 0, 0, 40 * this.s, 40 * this.s)
#
# `_SAKURA_PNG_B64` below is pixel-derived from that exact production PNG,
# losslessly pre-scaled to its maximum on-screen draw size (40x40). There is no
# locally designed petal silhouette, color, opacity or gradient.

SAKURA_COUNT = 12  # nekro.top production bundle: 50
_FRAME_MS = 16      # Qt equivalent of requestAnimationFrame cadence

# Exact visual content from the production Fb PNG, pre-scaled to the source
# renderer's maximum 40x40 draw size. This keeps the GUI source small while
# preserving the original petal artwork instead of redrawing it.
_SAKURA_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAACgAAAAoCAYAAACM/rhtAAAGUElEQVR42u1YTWxcVxk957v3vpn3ZsZxEid2GpJUoSoLShsUi0hVIApsKIsKsaA7pApWLPlZsLKyYgO7iJ8dFUKAUYiIaEBRC0JtA4tCIWSDQkKIqNM4NnYce2bez70fi3nPM3Zby+NJA0K90l08vZ977jnfd77vXeD9Mdrge/VhhRLaW4Gg/td3qgB1ZkZ0dtbojMr/jASqSp1RATeKcfXUl5t3vnZ2auUr35rQEZTiKIxBFWRfvrmvnj0SHz38UXHuU5r5p7XduV0sLb9YLC2fO/C9r88rQAJDyW13yhpJBYnlb35nd7HrwCdd0nxOotrpuNGcMNYi5AXuLyxes1beCEwfATD/UBiswN05O9us15NTMj7+RUmSZ5KxZj3LC+RZFuA1IATxK2tdv7xyCXcWzt2fv/3ike9+Y2lYFrkTcCuzF/fBND4njcaXXKM5rUIE773mhSAoy2dV2jnzheVb/vb8z/PV1XP763OXceaMDgPQDguuff53h4Kxz7HR+IJNko8EqGpQADAUAaiAECSpQcF67SCS+JjptK8vdvbcnAD+NQyLMgy45R+9sjvQfJqNxrOSJE8EQtetDugtawxoLRhZoObUNOpGGskxNlsntDk2rd+ejQnodjN7WwySVP3+626t3j7BRuMTTOJj4iy9L5QBVB96SxoBhYAQEIHUHH3i1O3dNQ5nj0va+edyzJsA/rxd5WQ77AFA+kj+qOxqfozNeNrVa60QvFJRcoierELACCDSu3YGUo8gYwns/l2HzNSex6Lx1gd19vK2WbTbYe/W7OXYOzzJyD4FYx4NPvQSTHV9GbIHqko9sic844isObCIYtPtTmCtvXe1e78FoNN7lzsHWMXePsdJb6PH6NxR61zde68YiDuykpX9a1asGkABEZqQ2ySAiXH12gORmKSqghrpQbH2A2LNFMsy0qcK68AgVQxWbFYTKsaA1sSMbMzEjQ5Q0Yu9+Z/9tgHnDsPZI0rZU4rLfsErmTOD4AbAk1BW8UkLEZvnakdnsGRpzGEiCA7DyCFjbRSqrKlspZysWOQm/2e5ihFQjKORiCZ32y0TspW8ABBcfVKsmxJr91HYR17JXGUvNxWnkr11wknAoKCzmRtLsg3fGBZglf568WINhlO0dkqF41otWoXWIDAdWJH95ClnhTQVsNspQjaaxJW3FftbcGZKRaYoEocQBqrGRjvZHBvkgLwEtGdJmdeQRWZUgOVYCd29FDMJayeNMQJAwU3SaDV1XeK32Y4QQUOGoHepfj4x6b3+LnYAsGcvShe7SVhzQKzZy8o6uFnWQTfhAGsERaBCFWtB6CJ8uKF5+AdPn14tn9ahAVbx99YPLyVeeQDWTEE4Fqhgb2xIDIUCoQKq/eQoNyQi8N4rCn8j5Om1oOmNwRI6PIOlVK161BJigiITENZ0gyQDFKq+PR1LWZVQYw01+MWQ+7/64P/SvHxpoScSR2u3TNM1aGUMxrbEGKoO+osCoZz9G30/LMGJtSxCSFn417TIX2rdzf7IM2fCMD8l7wqw8HmslAREzKoJ0D7GDXirwBgwaQqhCB55/vvQzS7kqr/h55/uqOpQLb+8Q4aUIloDIwJjSBno81A2SpWygwkT1jehQiGy/ArT9EIo2r8e//iTS+s/WyMxWLJkk1qX1nRo2F0vab3ghqqqhqB9a+m/G0JQYwx9ml33a51fhrXOxcbJ43M7AbelxKGTdxF8Bz60AxSUvmkJSWMMOWAnVdxFtYg+Td8Mq51fSJ6dq5986m87BbelxPf+fX1Bu9lN+OKGFn5FREASqiGEwue+KNoB9GKkahiCiyLmWX5H293zJkt/HJ144soo4N6xYe1VJSXJtXu/evWqCeFxDf5xMjquISwizV7SNL2tAfskiT8DkXEQIarXTJZn85p2fuq77Reap6b/9CCOVrbsy8ZSzHWy4paJwltFmqWa529oXpwP3XRZarVnSGnbyO0mafJu91pIuy8w7/ykeWr6+qjMbQlw/cOfPbnKl1//Q4hcRO9fRlFci/Pi1baaD0FYg2HsfbEa8uyCZukPlu5mrx18drr9oMDt+PCo88qVo6y5D7NWbxVZ9vfk6twVPn+6u+Hc5qEer/Wm6IzKu/0q6syMbLe+vucMlkCqqQD0obL2/vh/Gv8BYqISQp5SZ3IAAAAASUVORK5CYII="
)


def _load_source_sprite() -> QPixmap:
    pixmap = QPixmap()
    if not pixmap.loadFromData(base64.b64decode(_SAKURA_PNG_B64), "PNG"):
        raise RuntimeError("Failed to load embedded nekro.top sakura sprite")
    return pixmap


@dataclass(slots=True)
class SakuraParticle:
    # Direct Qt naming equivalent of production Wb: x, y, s, r, fn{x,y,r}.
    x: float
    y: float
    s: float
    r: float
    fnx_n: float
    fny_n: float
    fnr_n: float

    @classmethod
    def random_in_viewport(cls, width: int, height: int) -> "SakuraParticle":
        # Ub("x"), Ub("y"), Ub("s"), Ub("r"), and one independently captured
        # random constant for each movement closure.
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
        # Production Ub("fnx"), Ub("fny"), Ub("fnr") closures.
        self.x = self.x + 0.5 * self.fnx_n - 1.7
        self.y = self.y + self.fny_n
        self.r = self.r + self.fnr_n

        # Production Wb.update boundary test.
        if self.x > width or self.x < 0 or self.y > height or self.y < 0:
            self._respawn(width, height)

    def _respawn(self, width: int, height: int) -> None:
        # Production Hb entries are initialized to -1, therefore this is the
        # active respawn path: 60% from the top, 40% from the right edge.
        if random.random() > 0.4:
            self.x = random.random() * width
            self.y = 0.0
        else:
            self.x = float(width)
            self.y = random.random() * height
        self.s = random.random()
        self.r = 6.0 * random.random()


class NekroSakuraOverlay(QWidget):
    """Qt canvas equivalent of the captured production canvas_sakura loop."""

    def __init__(self, window: QMainWindow, count: int = SAKURA_COUNT) -> None:
        central = window.centralWidget()
        super().__init__(central)
        self.window = window
        self.count = max(0, int(count))
        self.sprite = _load_source_sprite()

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

        # Browser: requestAnimationFrame(clear -> update -> draw -> raf).
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
        if watched is self.window and event.type() in (QEvent.Resize, QEvent.Show):
            QTimer.singleShot(0, self.sync_geometry)
        return False

    def _frame(self) -> None:
        width = max(1, self.width())
        height = max(1, self.height())
        for particle in self.particles:
            particle.update(width, height)
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        for particle in self.particles:
            # Production Wb.draw:
            # save -> translate(x,y) -> rotate(r) ->
            # drawImage(Fb, 0, 0, 40*s, 40*s) -> restore.
            draw_size = 40.0 * particle.s
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
