from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QApplication, QMainWindow, QWidget


_FRAME_MS = 16
_PARTICLE_COUNT = 20
_ORBIT_RADIUS = (50, 100)
_PARTICLE_RADIUS = (10, 20)
_DIFFUSE_RADIUS = (50, 100)
_PARTICLE_DURATION_MS = (900, 1500)
_RING_DURATION_MS = (1200, 1800)
_RING_ALPHA_DURATION_MS = (600, 800)
_EFFECT_PAD = 126
_FALLBACK_DUPLICATE_WINDOW_S = 0.035
_DARK_PARTICLE_COLORS = (
    (252, 146, 174),
    (202, 180, 190),
    (207, 198, 255),
)
_DARK_RING_COLOR = (233, 179, 237)


def _anime_random(minimum: float, maximum: float) -> float:
    """Match anime.js 3.2.1 random(), including its inclusive integer semantics."""
    return math.floor(random.random() * (maximum - minimum + 1.0)) + minimum


def _ease_out_expo(progress: float) -> float:
    progress = min(1.0, max(0.0, progress))
    if progress >= 1.0:
        return 1.0
    return 1.0 - math.pow(2.0, -10.0 * progress)


def _clamp01(value: float) -> float:
    return min(1.0, max(0.0, value))


def _effect_rect(center: QPointF) -> QRect:
    x = int(math.floor(center.x())) - _EFFECT_PAD
    y = int(math.floor(center.y())) - _EFFECT_PAD
    size = _EFFECT_PAD * 2 + 2
    return QRect(x, y, size, size)


@dataclass(slots=True)
class FireworkParticle:
    end_x: float
    end_y: float
    radius: float
    angle_deg: float
    color: tuple[int, int, int]
    alpha: float


@dataclass(slots=True)
class FireworkBurst:
    center: QPointF
    started_s: float
    particle_duration_ms: float
    ring_duration_ms: float
    ring_alpha_duration_ms: float
    ring_end_radius: float
    particles: tuple[FireworkParticle, ...]

    @property
    def total_duration_ms(self) -> float:
        return max(self.particle_duration_ms, self.ring_duration_ms)


class ClickFireworksLayer(QWidget):
    """Mouse-transparent native Qt rendering of the reference click fireworks."""

    def __init__(self, window: QMainWindow) -> None:
        central = window.centralWidget()
        super().__init__(central)
        self.window = window
        self.bursts: list[FireworkBurst] = []
        self._paint_time_s = time.perf_counter()
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.sync_geometry()

    def sync_geometry(self) -> None:
        central = self.window.centralWidget()
        if central is None:
            return
        self.setGeometry(central.rect())
        self.raise_()
        self.show()

    def map_global_click(self, global_pos: QPoint) -> QPointF | None:
        central = self.window.centralWidget()
        if central is None or not self.window.isVisible() or self.window.isMinimized():
            return None
        local = central.mapFromGlobal(global_pos)
        if not central.rect().contains(local):
            return None
        return QPointF(local)

    def spawn(self, global_pos: QPoint, *, now_s: float) -> bool:
        center = self.map_global_click(global_pos)
        if center is None:
            return False

        particles: list[FireworkParticle] = []
        for _ in range(_PARTICLE_COUNT):
            angle_deg = _anime_random(0, 360)
            angle_rad = angle_deg * math.pi / 180.0
            diffuse = _anime_random(*_DIFFUSE_RADIUS)
            signed_diffuse = (-1.0, 1.0)[int(_anime_random(0, 1))] * diffuse
            color = _DARK_PARTICLE_COLORS[
                int(_anime_random(0, len(_DARK_PARTICLE_COLORS) - 1))
            ]
            particles.append(
                FireworkParticle(
                    end_x=center.x() + signed_diffuse * math.cos(angle_rad),
                    end_y=center.y() + signed_diffuse * math.sin(angle_rad),
                    radius=_anime_random(*_PARTICLE_RADIUS),
                    angle_deg=_anime_random(0, 360),
                    color=color,
                    alpha=_anime_random(0.2, 0.8),
                )
            )

        self.bursts.append(
            FireworkBurst(
                center=QPointF(center),
                started_s=now_s,
                particle_duration_ms=_anime_random(*_PARTICLE_DURATION_MS),
                ring_duration_ms=_anime_random(*_RING_DURATION_MS),
                ring_alpha_duration_ms=_anime_random(*_RING_ALPHA_DURATION_MS),
                ring_end_radius=_anime_random(*_ORBIT_RADIUS),
                particles=tuple(particles),
            )
        )
        self._paint_time_s = now_s
        self.show()
        self.raise_()
        self.update(_effect_rect(center))
        return True

    def advance(self, now_s: float) -> bool:
        self._paint_time_s = now_s
        if not self.bursts:
            return False
        dirty = QRect()
        active: list[FireworkBurst] = []
        for burst in self.bursts:
            dirty = dirty.united(_effect_rect(burst.center))
            elapsed_ms = max(0.0, (now_s - burst.started_s) * 1000.0)
            if elapsed_ms < burst.total_duration_ms:
                active.append(burst)
        self.bursts = active
        if not dirty.isNull():
            self.update(dirty)
        return bool(active)

    def clear(self) -> None:
        self.bursts.clear()
        self.hide()
        self.update()

    def _draw_particles(self, painter: QPainter, burst: FireworkBurst, elapsed_ms: float) -> None:
        progress = elapsed_ms / max(1.0, burst.particle_duration_ms)
        if progress >= 1.0:
            return
        eased = _ease_out_expo(progress)
        start_x = burst.center.x()
        start_y = burst.center.y()
        painter.setPen(Qt.PenStyle.NoPen)
        for particle in burst.particles:
            x = start_x + (particle.end_x - start_x) * eased
            y = start_y + (particle.end_y - start_y) * eased
            radius = particle.radius * (1.0 - eased)
            if radius <= 0.01:
                continue
            r, g, b = particle.color
            alpha = int(round(255.0 * _clamp01(particle.alpha)))
            painter.save()
            painter.translate(QPointF(x, y))
            painter.rotate(particle.angle_deg)
            painter.setBrush(QColor(r, g, b, alpha))
            painter.drawPolygon(
                QPolygonF(
                    (
                        QPointF(0.0, -radius),
                        QPointF(radius * math.sin(math.pi / 3.0), radius * math.cos(math.pi / 3.0)),
                        QPointF(-radius * math.sin(math.pi / 3.0), radius * math.cos(math.pi / 3.0)),
                    )
                )
            )
            painter.restore()

    def _draw_ring(self, painter: QPainter, burst: FireworkBurst, elapsed_ms: float) -> None:
        progress = elapsed_ms / max(1.0, burst.ring_duration_ms)
        if progress >= 1.0:
            return
        eased = _ease_out_expo(progress)
        radius = 0.1 + (burst.ring_end_radius - 0.1) * eased
        line_width = 6.0 * (1.0 - eased)
        alpha_progress = min(1.0, elapsed_ms / max(1.0, burst.ring_alpha_duration_ms))
        alpha = 0.5 * (1.0 - alpha_progress)
        if alpha <= 0.0 or line_width <= 0.01:
            return
        r, g, b = _DARK_RING_COLOR
        pen = QPen(QColor(r, g, b, int(round(255.0 * alpha))))
        pen.setWidthF(line_width)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(burst.center, radius, radius)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setClipRegion(event.region())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        now_s = self._paint_time_s
        for burst in self.bursts:
            elapsed_ms = max(0.0, (now_s - burst.started_s) * 1000.0)
            self._draw_particles(painter, burst, elapsed_ms)
            self._draw_ring(painter, burst, elapsed_ms)
        painter.end()


class ClickFireworks(QObject):
    """Application-wide physical-click trigger with one burst per mouse press."""

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self.window = window
        self.layer = ClickFireworksLayer(window)
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.setInterval(_FRAME_MS)
        self.timer.timeout.connect(self._tick)
        self._enabled = True
        self._last_press_identity: tuple[int, int, int, int] | None = None
        self._last_press_seen_s = 0.0
        app = QApplication.instance()
        if app is None:
            raise RuntimeError("Click fireworks require an active QApplication")
        self.app = app
        self.app.installEventFilter(self)
        window.destroyed.connect(self.cleanup)
        QTimer.singleShot(0, self.layer.sync_geometry)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._enabled:
            return
        self._enabled = enabled
        self._last_press_identity = None
        self._last_press_seen_s = 0.0
        if not enabled:
            self.timer.stop()
            self.layer.clear()
        else:
            self.layer.sync_geometry()

    def _belongs_to_window(self, widget: QWidget) -> bool:
        current: QWidget | None = widget
        while current is not None:
            if current is self.window:
                return True
            current = current.parentWidget()
        return False

    def _is_duplicate_press(self, event: QMouseEvent, *, now_s: float) -> bool:
        """Collapse Qt parent propagation without throttling genuine rapid clicks."""

        global_pos = event.globalPosition().toPoint()
        timestamp = int(event.timestamp())
        button = int(event.button().value)
        identity = (timestamp, button, int(global_pos.x()), int(global_pos.y()))

        if timestamp > 0:
            duplicate = identity == self._last_press_identity
        else:
            previous = self._last_press_identity
            duplicate = bool(
                previous is not None
                and previous[1:] == identity[1:]
                and now_s - self._last_press_seen_s <= _FALLBACK_DUPLICATE_WINDOW_S
            )

        if not duplicate:
            self._last_press_identity = identity
            self._last_press_seen_s = now_s
        return duplicate

    def _tick(self) -> None:
        if not self._enabled:
            self.timer.stop()
            return
        try:
            active = self.layer.advance(time.perf_counter())
        except RuntimeError:
            active = False
        if not active:
            self.timer.stop()

    def _trigger(self, global_pos: QPoint, *, now_s: float) -> None:
        if self.layer.spawn(global_pos, now_s=now_s) and not self.timer.isActive():
            self.timer.start()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if not self._enabled:
            return False

        event_type = event.type()
        central = self.window.centralWidget()
        if event_type in (QEvent.Type.Resize, QEvent.Type.Show):
            if watched is self.window or watched is central:
                QTimer.singleShot(0, self.layer.sync_geometry)
            return False

        if event_type not in {
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseButtonDblClick,
        }:
            return False
        if not isinstance(watched, QWidget) or not self._belongs_to_window(watched):
            return False
        if not isinstance(event, QMouseEvent):
            return False

        now_s = time.perf_counter()
        if self._is_duplicate_press(event, now_s=now_s):
            return False
        self._trigger(event.globalPosition().toPoint(), now_s=now_s)
        return False

    def cleanup(self) -> None:
        self.timer.stop()
        try:
            self.app.removeEventFilter(self)
        except RuntimeError:
            pass


def install_click_fireworks(window: QMainWindow) -> ClickFireworks:
    existing = getattr(window, "_click_fireworks", None)
    if isinstance(existing, ClickFireworks):
        return existing
    controller = ClickFireworks(window)
    window._click_fireworks = controller  # type: ignore[attr-defined]
    return controller


__all__ = ["ClickFireworks", "ClickFireworksLayer", "install_click_fireworks"]
