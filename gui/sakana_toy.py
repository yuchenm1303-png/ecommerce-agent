from __future__ import annotations

import math
import time
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen, QPixmap, QTransform
from PySide6.QtWidgets import QApplication, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from gui.sakana_physics import SakanaSpringState, advance_spring, move_spring


# Sakana Widget upstream defaults. The custom character artwork, left-bottom
# placement, plain white controller and top-right visibility toggle are the only
# intentional product-level differences kept by this desktop port.
_TOY_SIZE = 200.0
_IMAGE_SIZE = _TOY_SIZE / 1.25
_CANVAS_SIZE = _TOY_SIZE * 1.5
_LEFT_MARGIN = 24
_BOTTOM_MARGIN = 18
_CHARACTER_IMAGE = Path(__file__).resolve().parent / "assets" / "sakana_takina.png"

# Upstream controller geometry: 4 * 28px, 24px high, 6px radius. Symbols are
# intentionally omitted per the desktop UI requirement, but geometry is kept.
_BASE_WIDTH = 112.0
_BASE_HEIGHT = 24.0
_BASE_RADIUS = 6.0
_DEFAULT_REFRESH_HZ = 60.0


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

        # The reference page uses Takina, so preserve the upstream Takina state:
        # i=.08, s=.1, d=.988, r=12, y=2, t=0, w=0.
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
        # Upstream CSS: rotate(r) translateX(r) translateY(y), with transform
        # origin at 50% / size px (bottom-center axis of the 200px widget).
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

    def _draw_base(self, painter: QPainter) -> None:
        rect = self.base_rect()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 18))
        painter.drawRoundedRect(rect.translated(0.0, 5.0), _BASE_RADIUS, _BASE_RADIUS)
        painter.setBrush(QColor("#ffffff"))
        painter.drawRoundedRect(rect, _BASE_RADIUS, _BASE_RADIUS)

    def paintEvent(self, event: QEvent) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        anchor = self.anchor
        offset = self._center_offset()
        end = QPointF(anchor.x() + offset.x(), anchor.y() - offset.y())

        # Upstream default rod: #b4b4b4, 10px, round caps.
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
    """Own Sakana physics, frame scheduling, drag, visibility and lifecycle."""

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

        # Upstream uses Date.now() plus requestAnimationFrame. Keep exactly one
        # physics step per display frame and the same elapsed-time input to the
        # Sakana equations instead of imposing a fixed 16ms cadence.
        self._last_tick = time.monotonic()
        self._next_frame_deadline = 0.0
        self._running = True
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setSingleShot(True)
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

    def _display_refresh_hz(self) -> float:
        screen = self.window.screen()
        if screen is None:
            return _DEFAULT_REFRESH_HZ
        refresh = float(screen.refreshRate())
        if not math.isfinite(refresh) or refresh <= 1.0:
            return _DEFAULT_REFRESH_HZ
        return refresh

    def _schedule_next_frame(self, *, reset_deadline: bool = False) -> None:
        if not self._running or not self.toy.isVisible():
            self._timer.stop()
            return

        now = time.monotonic()
        frame_period = 1.0 / self._display_refresh_hz()
        if (
            reset_deadline
            or self._next_frame_deadline <= 0.0
            or self._next_frame_deadline < now - frame_period
        ):
            self._next_frame_deadline = now

        self._next_frame_deadline += frame_period
        delay_ms = max(0, round((self._next_frame_deadline - now) * 1000.0))
        self._timer.start(delay_ms)

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
            # Upstream `_onMouseDown`: stop RAF, remember press pageY, zero both
            # velocities. Horizontal displacement remains relative to main center.
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
            # Upstream mouseup does not reset `_lastRunUnix`; it only requests the
            # next animation frame. Preserve that exact first-release-step timing.
            self._running = True
            self._schedule_next_frame(reset_deadline=True)
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
            # Upstream stops before `_draw()` once all four values are below cut.
            self._running = False
            self._timer.stop()
            return

        self.toy.update()
        self._schedule_next_frame()

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
        self._schedule_next_frame(reset_deadline=True)

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
