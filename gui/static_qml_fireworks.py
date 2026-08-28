from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtQuick import QQuickPaintedItem

from .click_fireworks import (
    _DARK_PARTICLE_COLORS,
    _DARK_RING_COLOR,
    _DIFFUSE_RADIUS,
    _ORBIT_RADIUS,
    _PARTICLE_COUNT,
    _PARTICLE_DURATION_MS,
    _PARTICLE_RADIUS,
    _RING_ALPHA_DURATION_MS,
    _RING_DURATION_MS,
    _anime_random,
    _clamp01,
    _ease_out_expo,
)


_FRAME_MS = 16


@dataclass(slots=True)
class _Particle:
    end_x: float
    end_y: float
    radius: float
    angle_deg: float
    color: tuple[int, int, int]
    alpha: float


@dataclass(slots=True)
class _Burst:
    center: QPointF
    started_s: float
    particle_duration_ms: float
    ring_duration_ms: float
    ring_alpha_duration_ms: float
    ring_end_radius: float
    particles: tuple[_Particle, ...]

    @property
    def total_duration_ms(self) -> float:
        return max(self.particle_duration_ms, self.ring_duration_ms)


class StaticQuickFireworks(QQuickPaintedItem):
    """Quick-scene renderer for the exact established click-fireworks choreography."""

    def __init__(self, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self._bursts: list[_Burst] = []
        self._paint_time_s = time.perf_counter()
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setAntialiasing(True)
        self.setMipmap(False)
        self.setVisible(False)

        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(_FRAME_MS)
        self._timer.timeout.connect(self._tick)

    def spawn(self, center: QPointF) -> None:
        particles: list[_Particle] = []
        for _ in range(_PARTICLE_COUNT):
            angle_deg = _anime_random(0, 360)
            angle_rad = angle_deg * math.pi / 180.0
            diffuse = _anime_random(*_DIFFUSE_RADIUS)
            signed_diffuse = (-1.0, 1.0)[int(_anime_random(0, 1))] * diffuse
            color = _DARK_PARTICLE_COLORS[
                int(_anime_random(0, len(_DARK_PARTICLE_COLORS) - 1))
            ]
            particles.append(
                _Particle(
                    end_x=center.x() + signed_diffuse * math.cos(angle_rad),
                    end_y=center.y() + signed_diffuse * math.sin(angle_rad),
                    radius=_anime_random(*_PARTICLE_RADIUS),
                    angle_deg=_anime_random(0, 360),
                    color=color,
                    alpha=_anime_random(0.2, 0.8),
                )
            )

        now_s = time.perf_counter()
        self._bursts.append(
            _Burst(
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
        self.setVisible(True)
        self.update()
        if not self._timer.isActive():
            self._timer.start()

    def clear(self) -> None:
        self._timer.stop()
        self._bursts.clear()
        self.setVisible(False)
        self.update()

    def _tick(self) -> None:
        now_s = time.perf_counter()
        self._paint_time_s = now_s
        self._bursts = [
            burst
            for burst in self._bursts
            if (now_s - burst.started_s) * 1000.0 < burst.total_duration_ms
        ]
        if not self._bursts:
            self._timer.stop()
            self.setVisible(False)
        self.update()

    def _draw_particles(self, painter: QPainter, burst: _Burst, elapsed_ms: float) -> None:
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
            red, green, blue = particle.color
            alpha = int(round(255.0 * _clamp01(particle.alpha)))
            painter.save()
            painter.translate(QPointF(x, y))
            painter.rotate(particle.angle_deg)
            painter.setBrush(QColor(red, green, blue, alpha))
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

    def _draw_ring(self, painter: QPainter, burst: _Burst, elapsed_ms: float) -> None:
        progress = elapsed_ms / max(1.0, burst.ring_duration_ms)
        if progress >= 1.0:
            return
        eased = _ease_out_expo(progress)
        radius = 0.1 + (burst.ring_end_radius - 0.1) * eased
        line_width = 6.0 * (1.0 - eased)
        alpha_progress = min(
            1.0,
            elapsed_ms / max(1.0, burst.ring_alpha_duration_ms),
        )
        alpha = 0.5 * (1.0 - alpha_progress)
        if alpha <= 0.0 or line_width <= 0.01:
            return
        red, green, blue = _DARK_RING_COLOR
        pen = QPen(QColor(red, green, blue, int(round(255.0 * alpha))))
        pen.setWidthF(line_width)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(burst.center, radius, radius)

    def paint(self, painter: QPainter) -> None:  # type: ignore[override]
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        now_s = self._paint_time_s
        clip = QRectF(0.0, 0.0, float(self.width()), float(self.height()))
        painter.setClipRect(clip)
        for burst in self._bursts:
            elapsed_ms = max(0.0, (now_s - burst.started_s) * 1000.0)
            self._draw_particles(painter, burst, elapsed_ms)
            self._draw_ring(painter, burst, elapsed_ms)


__all__ = ["StaticQuickFireworks"]
