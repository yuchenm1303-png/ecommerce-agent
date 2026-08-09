from __future__ import annotations

import math
import time
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QApplication, QFrame, QGraphicsDropShadowEffect, QMainWindow, QWidget


_GLASS_NAMES = {"glassCard", "heroCard", "statusCard", "microCard"}


@dataclass(slots=True)
class _TrailPoint:
    point: QPointF
    born: float


@dataclass(slots=True)
class _Ripple:
    point: QPointF
    born: float


class CursorAura(QWidget):
    """Mouse-following ambience inspired by the nekro.top landing-page feel.

    This is intentionally presentation-only.  The overlay is transparent to
    mouse input and never participates in runner / browser / resolver logic.
    """

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self.window = window
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.hide()

        self.target = QPointF(-500.0, -500.0)
        self.current = QPointF(-500.0, -500.0)
        self.visible_aura = False
        self.trail: list[_TrailPoint] = []
        self.ripples: list[_Ripple] = []
        self._last_trail_at = 0.0

        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

    def sync_geometry(self) -> None:
        central = self.window.centralWidget()
        if central is None:
            return
        self.setGeometry(central.geometry())
        self.raise_()
        self.show()

    def set_target(self, point: QPointF) -> None:
        self.target = point
        if self.current.x() < -100:
            self.current = QPointF(point)
        self.visible_aura = True
        now = time.monotonic()
        if now - self._last_trail_at >= 0.035:
            self._last_trail_at = now
            self.trail.append(_TrailPoint(QPointF(point), now))
            if len(self.trail) > 12:
                self.trail = self.trail[-12:]

    def fade_out(self) -> None:
        self.visible_aura = False

    def click(self, point: QPointF) -> None:
        now = time.monotonic()
        self.ripples.append(_Ripple(QPointF(point), now))
        if len(self.ripples) > 4:
            self.ripples = self.ripples[-4:]

    def _tick(self) -> None:
        dx = self.target.x() - self.current.x()
        dy = self.target.y() - self.current.y()
        self.current = QPointF(self.current.x() + dx * 0.24, self.current.y() + dy * 0.24)
        now = time.monotonic()
        self.trail = [item for item in self.trail if now - item.born < 0.55]
        self.ripples = [item for item in self.ripples if now - item.born < 0.75]
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        del event
        if self.current.x() < -100:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        now = time.monotonic()

        # Large soft cursor illumination.  It is deliberately subtle: the goal
        # is the same "light moving through glass" impression, not a game HUD.
        if self.visible_aura:
            radius = 150.0
            glow = QRadialGradient(self.current, radius)
            glow.setColorAt(0.0, QColor(255, 237, 247, 54))
            glow.setColorAt(0.20, QColor(246, 188, 220, 35))
            glow.setColorAt(0.58, QColor(211, 139, 193, 14))
            glow.setColorAt(1.0, QColor(255, 255, 255, 0))
            painter.setPen(Qt.NoPen)
            painter.setBrush(glow)
            painter.drawEllipse(self.current, radius, radius)

        # A tiny sakura/sparkle trail gives motion feedback without obscuring
        # text or making the debugging UI feel noisy.
        painter.setPen(Qt.NoPen)
        for index, item in enumerate(self.trail):
            age = now - item.born
            life = max(0.0, 1.0 - age / 0.55)
            if life <= 0:
                continue
            drift_x = math.sin((index + 1) * 1.7 + age * 5.0) * 6.0
            drift_y = age * 13.0
            size = 3.0 + 2.6 * life
            alpha = int(125 * life)
            painter.save()
            painter.translate(item.point.x() + drift_x, item.point.y() + drift_y)
            painter.rotate(-28 + index * 17 + age * 46)
            painter.setBrush(QColor(255, 226, 239, alpha))
            painter.drawEllipse(QRectF(-size, -size * 0.34, size * 2.0, size * 0.68))
            painter.restore()

        # Click ripple: soft pink ring + white center glint.
        for ripple in self.ripples:
            age = now - ripple.born
            t = min(1.0, age / 0.75)
            radius = 10.0 + 42.0 * t
            alpha = int(120 * (1.0 - t))
            pen = QPen(QColor(255, 217, 238, alpha), 1.5)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(ripple.point, radius, radius)

        # Small cross/star next to the native cursor.  Keeping the native
        # cursor visible is important for a development tool.
        if self.visible_aura:
            painter.setPen(QPen(QColor(255, 247, 252, 185), 1.2))
            p = QPointF(self.current.x() + 13.0, self.current.y() + 11.0)
            painter.drawLine(QPointF(p.x() - 4, p.y()), QPointF(p.x() + 4, p.y()))
            painter.drawLine(QPointF(p.x(), p.y() - 4), QPointF(p.x(), p.y() + 4))

        painter.end()


class GlassInteractionController(QObject):
    """Global event filter for cursor ambience and low-cost glass-card depth."""

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self.window = window
        self.aura = CursorAura(window)
        self._glass_effects: dict[QWidget, QGraphicsDropShadowEffect] = {}
        self._install_card_depth()
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        QTimer.singleShot(0, self.aura.sync_geometry)

    def _install_card_depth(self) -> None:
        for frame in self.window.findChildren(QFrame):
            if frame.objectName() not in _GLASS_NAMES:
                continue
            effect = QGraphicsDropShadowEffect(frame)
            effect.setBlurRadius(26.0 if frame.objectName() == "heroCard" else 20.0)
            effect.setOffset(0.0, 7.0 if frame.objectName() == "heroCard" else 5.0)
            effect.setColor(QColor(37, 19, 42, 84))
            frame.setGraphicsEffect(effect)
            frame.setMouseTracking(True)
            self._glass_effects[frame] = effect

    def _point_in_central(self, global_point: QPointF) -> QPointF | None:
        central = self.window.centralWidget()
        if central is None or not self.window.isVisible():
            return None
        local = central.mapFromGlobal(global_point.toPoint())
        if not central.rect().contains(local):
            return None
        return QPointF(local)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        event_type = event.type()

        if event_type == QEvent.Resize and watched is self.window:
            QTimer.singleShot(0, self.aura.sync_geometry)

        if isinstance(event, QMouseEvent):
            local = self._point_in_central(event.globalPosition())
            if local is not None:
                if event_type == QEvent.MouseMove:
                    self.aura.set_target(local)
                elif event_type == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                    self.aura.set_target(local)
                    self.aura.click(local)
            elif event_type == QEvent.MouseMove:
                self.aura.fade_out()

        if isinstance(watched, QFrame) and watched in self._glass_effects:
            effect = self._glass_effects[watched]
            if event_type == QEvent.Enter:
                effect.setBlurRadius(32.0 if watched.objectName() == "heroCard" else 26.0)
                effect.setOffset(0.0, 8.0 if watched.objectName() == "heroCard" else 6.0)
                effect.setColor(QColor(255, 179, 220, 62))
            elif event_type == QEvent.Leave:
                effect.setBlurRadius(26.0 if watched.objectName() == "heroCard" else 20.0)
                effect.setOffset(0.0, 7.0 if watched.objectName() == "heroCard" else 5.0)
                effect.setColor(QColor(37, 19, 42, 84))

        if event_type == QEvent.Leave and watched is self.window:
            self.aura.fade_out()

        return False


def install_nekro_visual_fx(window: QMainWindow) -> GlassInteractionController:
    """Attach presentation-only effects and retain the controller on the window."""

    controller = GlassInteractionController(window)
    window._nekro_visual_fx = controller  # type: ignore[attr-defined]
    return controller
