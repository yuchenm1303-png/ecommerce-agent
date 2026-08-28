from __future__ import annotations

import math
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QHBoxLayout, QPushButton, QVBoxLayout, QWidget


_TOY_SIZE = 180.0
_IMAGE_SIZE = _TOY_SIZE / 1.25
_CANVAS_SIZE = _TOY_SIZE * 1.5
_FRAME_SECONDS = 1.0 / 60.0
_LEFT_MARGIN = 24
_BOTTOM_MARGIN = 18
_ANCHOR_HIT_RADIUS = 14.0
_CHARACTER_IMAGE = Path(__file__).resolve().parent / "assets" / "sakana_takina.png"


@dataclass
class _SpringState:
    # Takina defaults from Sakana Widget.
    i: float = 0.08
    s: float = 0.10
    d: float = 0.988
    r: float = 12.0
    y: float = 2.0
    t: float = 0.0
    w: float = 0.0


class _SakanaToyWidget(QWidget):
    """Transparent Qt rendering of the Sakana spring toy."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("sakanaToyOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        side = math.ceil(_CANVAS_SIZE)
        self.setFixedSize(side, side)

        self.state = _SpringState()
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

    def _center_offset(self) -> QPointF:
        angle = math.radians(self.state.r)
        cos_a = math.cos(angle)
        sin_a = math.sin(angle)
        e = _TOY_SIZE - _IMAGE_SIZE / 2.0
        x_translate = self.state.r
        y_translate = self.state.y
        return QPointF(
            sin_a * e + cos_a * x_translate - sin_a * y_translate,
            cos_a * e - cos_a * y_translate - sin_a * x_translate,
        )

    def character_center(self) -> QPointF:
        offset = self._center_offset()
        anchor = self.anchor
        return QPointF(anchor.x() + offset.x(), anchor.y() - offset.y())

    def character_hit_test(self, point: QPointF) -> bool:
        center = self.character_center()
        half = _IMAGE_SIZE * 0.48
        return (
            abs(point.x() - center.x()) <= half
            and abs(point.y() - center.y()) <= half
        )

    def anchor_hit_test(self, point: QPointF) -> bool:
        anchor = self.anchor
        dx = point.x() - anchor.x()
        dy = point.y() - anchor.y()
        return dx * dx + dy * dy <= _ANCHOR_HIT_RADIUS * _ANCHOR_HIT_RADIUS

    def move_spring(self, dx: float, dy: float) -> None:
        self.state.r = max(-self.max_rotation, min(self.max_rotation, dx * self.state.s))
        self.state.y = max(self.min_y, min(self.max_y, dy * self.state.s * 2.0))
        self.state.w = 0.0
        self.state.t = 0.0
        self.update()

    def paintEvent(self, event: QEvent) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        anchor = self.anchor
        offset = self._center_offset()
        end = QPointF(anchor.x() + offset.x(), anchor.y() - offset.y())

        pen = QPen(QColor("#b4b4b4"))
        pen.setWidthF(8.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawLine(QPointF(anchor.x(), anchor.y() - 10.0), end)

        painter.save()
        painter.translate(anchor)
        painter.rotate(self.state.r)
        painter.translate(self.state.r, self.state.y)
        target = QRectF(-_IMAGE_SIZE / 2.0, -_TOY_SIZE, _IMAGE_SIZE, _IMAGE_SIZE)
        painter.drawPixmap(target, self.pixmap, QRectF(self.pixmap.rect()))
        painter.restore()

        # Small visible grab point: dragging it moves the whole toy; dragging
        # the character itself keeps the original Sakana spring interaction.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 92))
        painter.drawEllipse(anchor, 4.0, 4.0)
        painter.end()


class SakanaToyController(QObject):
    """Own Sakana physics, position drag, visibility toggle and lifecycle."""

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
        if self.toy.anchor_hit_test(local):
            self._interaction = "position"
            self._position_press_global = event.globalPosition()
            self._position_press_top_left = self.toy.pos()
            self._user_positioned = True
        elif self.toy.character_hit_test(local):
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
            center_global = self.toy.mapToGlobal(self.toy.rect().center()).x()
            self.toy.move_spring(
                event.globalPosition().x() - center_global,
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
            self._running = True
            self._last_tick = time.monotonic()
            self._timer.start()
        if event is not None:
            event.accept()
        return True

    def _step_physics(self) -> None:
        if not self._running or not self.toy.isVisible():
            self._timer.stop()
            return

        state = self.toy.state
        now = time.monotonic()
        elapsed = max(0.0, now - self._last_tick)
        self._last_tick = now
        step = state.i
        if elapsed < _FRAME_SECONDS:
            step = state.i / _FRAME_SECONDS * elapsed

        previous = (state.w, state.r, state.t, state.y)
        w = state.w - 2.0 * state.r - state.t
        state.r += w * step * 1.2
        state.w = w * state.d
        t = state.t - 2.0 * state.y
        state.y += t * step * 2.0
        state.t = t * state.d

        delta = max(
            abs(previous[0] - state.w),
            abs(previous[1] - state.r),
            abs(previous[2] - state.t),
            abs(previous[3] - state.y),
        )
        self.toy.update()
        if delta < 0.1:
            self._running = False
            self._timer.stop()

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
