from __future__ import annotations

import math
import time
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen, QPixmap, QTransform
from PySide6.QtWidgets import QApplication, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from gui.sakana_physics import SakanaSpringState, advance_spring, move_spring


_TOY_SIZE = 180.0
_IMAGE_SIZE = _TOY_SIZE / 1.25
_CANVAS_SIZE = _TOY_SIZE * 1.5
_LEFT_MARGIN = 24
_BOTTOM_MARGIN = 18
_CHARACTER_IMAGE = Path(__file__).resolve().parent / "assets" / "sakana_takina.png"

# Match Sakana Widget's four-cell controller proportions, scaled for this GUI.
_BASE_WIDTH = 156.0
_BASE_HEIGHT = 34.0
_BASE_RADIUS = 8.0
_BASE_ITEM_WIDTH = _BASE_WIDTH / 4.0


class _SakanaToyWidget(QWidget):
    """Transparent Qt rendering of the Sakana spring toy and its control base."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("sakanaToyOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        side = math.ceil(_CANVAS_SIZE)
        self.setFixedSize(side, side)

        self.state = SakanaSpringState()
        self.max_rotation = max(30.0, min(60.0, _TOY_SIZE / 5.0))
        self.max_y = _TOY_SIZE / 4.0
        self.min_y = -self.max_y

        pixmap = QPixmap(str(_CHARACTER_IMAGE))
        if pixmap.isNull():
            raise RuntimeError(f"Unable to load Sakana character image: {_CHARACTER_IMAGE}")
        self.pixmap = pixmap

    @property
    def anchor(self) -> QPointF:
        inset = (_CANVAS_SIZE - _TOY_SIZE) / 2.0
        return QPointF(_CANVAS_SIZE / 2.0, _TOY_SIZE + inset)

    @property
    def image_rect(self) -> QRectF:
        return QRectF(-_IMAGE_SIZE / 2.0, -_TOY_SIZE, _IMAGE_SIZE, _IMAGE_SIZE)

    def base_rect(self) -> QRectF:
        anchor = self.anchor
        return QRectF(
            anchor.x() - _BASE_WIDTH / 2.0,
            anchor.y() - _BASE_HEIGHT,
            _BASE_WIDTH,
            _BASE_HEIGHT,
        )

    def _image_transform(self) -> QTransform:
        transform = QTransform()
        anchor = self.anchor
        transform.translate(anchor.x(), anchor.y())
        transform.rotate(self.state.r)
        # Sakana CSS: rotate(r) translateX(r) translateY(y), around the bottom
        # center transform-origin. Qt's painter transform below uses the same order.
        transform.translate(self.state.r, self.state.y)
        return transform

    def _center_offset(self) -> QPointF:
        angle = math.radians(self.state.r)
        cos_a = math.cos(angle)
        sin_a = math.sin(angle)
        radius = _TOY_SIZE - _IMAGE_SIZE / 2.0
        x = self.state.r
        y = self.state.y
        return QPointF(
            sin_a * radius + cos_a * x - sin_a * y,
            cos_a * radius - cos_a * y - sin_a * x,
        )

    def character_hit_test(self, point: QPointF) -> bool:
        inverse, invertible = self._image_transform().inverted()
        if not invertible:
            return False
        return self.image_rect.contains(inverse.map(point))

    def base_hit_test(self, point: QPointF) -> bool:
        return self.base_rect().contains(point)

    def move_spring(self, dx: float, dy: float) -> None:
        move_spring(
            self.state,
            dx,
            dy,
            max_rotation=self.max_rotation,
            max_y=self.max_y,
            min_y=self.min_y,
        )
        self.update()

    @staticmethod
    def _draw_person_icon(painter: QPainter, center: QPointF) -> None:
        painter.drawEllipse(QRectF(center.x() - 8.0, center.y() - 8.0, 16.0, 16.0))
        painter.drawEllipse(QRectF(center.x() - 2.7, center.y() - 4.8, 5.4, 5.4))
        painter.drawArc(
            QRectF(center.x() - 5.2, center.y() + 0.2, 10.4, 7.0),
            10 * 16,
            160 * 16,
        )

    @staticmethod
    def _draw_sync_icon(painter: QPainter, center: QPointF) -> None:
        arc = QRectF(center.x() - 7.5, center.y() - 7.5, 15.0, 15.0)
        painter.drawArc(arc, 35 * 16, 135 * 16)
        painter.drawArc(arc, 215 * 16, 135 * 16)
        painter.drawLine(
            QPointF(center.x() + 6.5, center.y() - 4.8),
            QPointF(center.x() + 7.4, center.y() - 0.8),
        )
        painter.drawLine(
            QPointF(center.x() + 6.5, center.y() - 4.8),
            QPointF(center.x() + 2.8, center.y() - 5.6),
        )
        painter.drawLine(
            QPointF(center.x() - 6.5, center.y() + 4.8),
            QPointF(center.x() - 7.4, center.y() + 0.8),
        )
        painter.drawLine(
            QPointF(center.x() - 6.5, center.y() + 4.8),
            QPointF(center.x() - 2.8, center.y() + 5.6),
        )

    @staticmethod
    def _draw_github_icon(painter: QPainter, center: QPointF) -> None:
        painter.save()
        painter.setBrush(QColor("#555555"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QRectF(center.x() - 7.0, center.y() - 5.2, 14.0, 12.5))
        painter.drawEllipse(QRectF(center.x() - 3.4, center.y() + 3.4, 6.8, 6.0))
        painter.restore()

    @staticmethod
    def _draw_close_icon(painter: QPainter, center: QPointF) -> None:
        painter.drawEllipse(QRectF(center.x() - 8.0, center.y() - 8.0, 16.0, 16.0))
        painter.drawLine(
            QPointF(center.x() - 3.6, center.y() - 3.6),
            QPointF(center.x() + 3.6, center.y() + 3.6),
        )
        painter.drawLine(
            QPointF(center.x() + 3.6, center.y() - 3.6),
            QPointF(center.x() - 3.6, center.y() + 3.6),
        )

    def _draw_base(self, painter: QPainter) -> None:
        rect = self.base_rect()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 18))
        painter.drawRoundedRect(rect.translated(0.0, 5.0), _BASE_RADIUS, _BASE_RADIUS)
        painter.setBrush(QColor("#dddddd"))
        painter.drawRoundedRect(rect, _BASE_RADIUS, _BASE_RADIUS)

        divider_pen = QPen(QColor(255, 255, 255, 52))
        divider_pen.setWidthF(1.0)
        painter.setPen(divider_pen)
        for index in range(1, 4):
            x = rect.left() + _BASE_ITEM_WIDTH * index
            painter.drawLine(QPointF(x, rect.top() + 5.0), QPointF(x, rect.bottom() - 5.0))

        icon_pen = QPen(QColor("#555555"))
        icon_pen.setWidthF(1.8)
        icon_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        icon_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(icon_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        centers = [
            QPointF(rect.left() + _BASE_ITEM_WIDTH * (index + 0.5), rect.center().y())
            for index in range(4)
        ]
        self._draw_person_icon(painter, centers[0])
        self._draw_sync_icon(painter, centers[1])
        self._draw_github_icon(painter, centers[2])
        self._draw_close_icon(painter, centers[3])

    def paintEvent(self, event: QEvent) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        anchor = self.anchor
        offset = self._center_offset()
        end = QPointF(anchor.x() + offset.x(), anchor.y() - offset.y())

        # Upstream default stroke is #b4b4b4, width 10, round caps.
        pen = QPen(QColor("#b4b4b4"))
        pen.setWidthF(10.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawLine(QPointF(anchor.x(), anchor.y() - 10.0), end)

        painter.save()
        painter.setTransform(self._image_transform(), combine=False)
        painter.drawPixmap(self.image_rect, self.pixmap, QRectF(self.pixmap.rect()))
        painter.restore()

        self._draw_base(painter)
        painter.end()


class SakanaToyController(QObject):
    """Own Sakana physics, base drag, visibility toggle and lifecycle."""

    def __init__(self, window: QWidget) -> None:
        super().__init__(window)
        self.window = window
        self.toy = _SakanaToyWidget(window)
        self.toy.hide()

        self._interaction: str | None = None
        self._spring_press_y = 0.0
        self._position_press_global = QPointF()
        self._position_press_top_left = QPoint()
        self._user_positioned = False
        self._last_tick = time.monotonic()
        self._running = True

        # requestAnimationFrame on the source site is one spring step per display
        # frame. A precise 16 ms Qt timer provides that same single-owner cadence.
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._step_physics)

        self.toggle = self._install_toggle()
        app = QApplication.instance()
        if app is None:
            raise RuntimeError("Sakana toy requires a QApplication")
        self._app = app
        app.installEventFilter(self)
        window.installEventFilter(self)

        self._place_default()
        self.set_enabled(True)

    def _install_toggle(self) -> QPushButton:
        root = self.window.centralWidget()
        outer = root.layout() if root is not None else None
        if not isinstance(outer, QVBoxLayout) or outer.count() < 1:
            raise RuntimeError("Sakana toy expected the preserved application root layout")
        header = outer.itemAt(0).layout()
        if not isinstance(header, QHBoxLayout):
            raise RuntimeError("Sakana toy expected the common application header")

        button = QPushButton("玩具 · ON")
        button.setObjectName("quietButton")
        button.setCheckable(True)
        button.setChecked(True)
        button.setMinimumWidth(98)
        button.setToolTip("显示或隐藏左下角弹簧玩具")
        button.setStyleSheet(
            "QPushButton:checked {"
            "background-color: rgba(190, 113, 157, 150);"
            "border-color: rgba(255, 220, 239, 90);"
            "font-weight: 700;"
            "}"
        )
        button.toggled.connect(self.set_enabled)
        header.addWidget(button, 0, Qt.AlignmentFlag.AlignBottom)
        return button

    def _place_default(self) -> None:
        self.toy.move(
            _LEFT_MARGIN,
            max(0, self.window.height() - self.toy.height() - _BOTTOM_MARGIN),
        )

    def _clamp_position(self, point: QPoint) -> QPoint:
        max_x = max(0, self.window.width() - self.toy.width())
        max_y = max(0, self.window.height() - self.toy.height())
        return QPoint(
            max(0, min(max_x, point.x())),
            max(0, min(max_y, point.y())),
        )

    def _local_from_global(self, global_pos: QPointF) -> QPointF:
        return QPointF(self.toy.mapFromGlobal(global_pos.toPoint()))

    def _begin_interaction(self, event: QMouseEvent) -> bool:
        if event.button() != Qt.MouseButton.LeftButton or not self.toy.isVisible():
            return False
        local = self._local_from_global(event.globalPosition())
        if self.toy.base_hit_test(local):
            self._interaction = "position"
            self._position_press_global = event.globalPosition()
            self._position_press_top_left = self.toy.pos()
            self._user_positioned = True
        elif self.toy.character_hit_test(local):
            # Match Sakana `_onMouseDown`: stop RAF, remember only the press Y,
            # and zero both velocities. The horizontal drag remains relative to
            # the widget center rather than the click point.
            self._interaction = "spring"
            self._spring_press_y = event.globalPosition().y()
            self._running = False
            self._timer.stop()
            self.toy.state.w = 0.0
            self.toy.state.t = 0.0
        else:
            return False

        self.window.grabMouse()
        event.accept()
        return True

    def _move_interaction(self, event: QMouseEvent) -> bool:
        if self._interaction is None:
            return False
        if self._interaction == "position":
            delta = event.globalPosition() - self._position_press_global
            target = self._position_press_top_left + QPoint(round(delta.x()), round(delta.y()))
            self.toy.move(self._clamp_position(target))
        else:
            center_global_x = self.toy.mapToGlobal(self.toy.rect().center()).x()
            self.toy.move_spring(
                event.globalPosition().x() - center_global_x,
                event.globalPosition().y() - self._spring_press_y,
            )
        event.accept()
        return True

    def _end_interaction(self, event: QMouseEvent | None = None) -> bool:
        if self._interaction is None:
            return False
        spring = self._interaction == "spring"
        self._interaction = None
        if QWidget.mouseGrabber() is self.window:
            self.window.releaseMouse()
        if spring and self.toy.isVisible():
            # The website does NOT reset `_lastRunUnix` on mouseup. Keeping the
            # previous tick makes the first released frame use full inertia after
            # a normal drag, exactly like requestAnimationFrame(this._run).
            self._running = True
            self._timer.start()
        if event is not None:
            event.accept()
        return True

    def _step_physics(self) -> None:
        if not self._running or not self.toy.isVisible():
            self._timer.stop()
            return

        now = time.monotonic()
        elapsed_ms = max(0.0, (now - self._last_tick) * 1000.0)
        self._last_tick = now

        if not advance_spring(self.toy.state, elapsed_ms):
            # Upstream stops before `_draw()` when all four state values are below
            # threshold, so do not force one extra final repaint here.
            self._running = False
            self._timer.stop()
            return

        self.toy.update()

    def set_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if self.toggle.isChecked() != enabled:
            self.toggle.blockSignals(True)
            self.toggle.setChecked(enabled)
            self.toggle.blockSignals(False)
        self.toggle.setText("玩具 · ON" if enabled else "玩具 · OFF")

        if not enabled:
            self._end_interaction()
            self._running = False
            self._timer.stop()
            self.toy.hide()
            return

        self.toy.show()
        self.toy.raise_()
        self._running = True
        self._last_tick = time.monotonic()
        self._timer.start()

    def raise_overlay(self) -> None:
        if self.toy.isVisible():
            self.toy.raise_()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        event_type = event.type()
        if watched is self.window and event_type == QEvent.Type.Resize:
            if self._user_positioned:
                self.toy.move(self._clamp_position(self.toy.pos()))
            else:
                self._place_default()
            return False

        if isinstance(event, QMouseEvent):
            if event_type == QEvent.Type.MouseButtonPress:
                return self._begin_interaction(event)
            if event_type == QEvent.Type.MouseMove:
                return self._move_interaction(event)
            if event_type == QEvent.Type.MouseButtonRelease:
                return self._end_interaction(event)
        return False

    def cleanup(self) -> None:
        self._timer.stop()
        self._end_interaction()
        try:
            self._app.removeEventFilter(self)
        except RuntimeError:
            pass


def install_sakana_toy(window: QWidget) -> SakanaToyController:
    existing = getattr(window, "_sakana_toy_controller", None)
    if isinstance(existing, SakanaToyController):
        return existing
    controller = SakanaToyController(window)
    window._sakana_toy_controller = controller  # type: ignore[attr-defined]
    window.destroyed.connect(controller.cleanup)
    return controller
