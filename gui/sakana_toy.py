from __future__ import annotations

import math
import time
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen, QPixmap, QTransform
from PySide6.QtQuick import QQuickPaintedItem, QQuickWindow
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from gui.sakana_physics import SakanaSpringState, advance_spring, move_spring


_TOY_SIZE = 200.0
_IMAGE_SIZE = _TOY_SIZE / 1.25
_CANVAS_SIZE = _TOY_SIZE * 1.5
_LEFT_MARGIN = 24.0
_BOTTOM_MARGIN = 18.0
_CHARACTER_IMAGE = Path(__file__).resolve().parent / "assets" / "sakana_character.jpg"

_BASE_WIDTH = 112.0
_BASE_HEIGHT = 24.0
_BASE_RADIUS = 6.0


class _SakanaToyItem(QQuickPaintedItem):
    """Sakana toy rendered by the application's single QQuickWindow."""

    def __init__(self, quick: QQuickWindow, controller: "SakanaToyController") -> None:
        super().__init__(quick.contentItem())
        self.quick = quick
        self.controller = controller
        self.setWidth(_CANVAS_SIZE)
        self.setHeight(_CANVAS_SIZE)
        self.setZ(32000.0)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
        self.setAcceptHoverEvents(False)

        self.state = SakanaSpringState()
        self.max_rotation = max(30.0, min(60.0, _TOY_SIZE / 5.0))
        self.max_y = _TOY_SIZE / 4.0
        self.min_y = -self.max_y

        pixmap = QPixmap(str(_CHARACTER_IMAGE))
        if pixmap.isNull():
            raise RuntimeError(f"Unable to load Sakana character image: {_CHARACTER_IMAGE}")
        self.pixmap = pixmap

        self._interaction: str | None = None
        self._spring_press_y = 0.0
        self._position_press_global = QPointF()
        self._position_press_xy = QPointF()

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
        return bool(invertible and self.image_rect.contains(inverse.map(point)))

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

    def paint(self, painter: QPainter) -> None:  # type: ignore[override]
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        anchor = self.anchor
        offset = self._center_offset()
        end = QPointF(anchor.x() + offset.x(), anchor.y() - offset.y())

        pen = QPen(QColor("#b4b4b4"))
        pen.setWidthF(10.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawLine(QPointF(anchor.x(), anchor.y() - 10.0), end)

        painter.save()
        painter.setTransform(self._image_transform(), combine=False)
        painter.drawPixmap(self.image_rect, self.pixmap, QRectF(self.pixmap.rect()))
        painter.restore()

        rect = self.base_rect()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 18))
        painter.drawRoundedRect(rect.translated(0.0, 5.0), _BASE_RADIUS, _BASE_RADIUS)
        painter.setBrush(QColor("#ffffff"))
        painter.drawRoundedRect(rect, _BASE_RADIUS, _BASE_RADIUS)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or not self.isVisible():
            event.ignore()
            return

        local = event.position()
        if self.base_hit_test(local):
            self._interaction = "position"
            self._position_press_global = event.globalPosition()
            self._position_press_xy = QPointF(self.x(), self.y())
            self.controller._user_positioned = True
        elif self.character_hit_test(local):
            self._interaction = "spring"
            self._spring_press_y = event.position().y()
            self.controller._running = False
            self.state.w = 0.0
            self.state.t = 0.0
        else:
            event.ignore()
            return

        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._interaction == "position":
            delta = event.globalPosition() - self._position_press_global
            self.controller._move_item(
                self._position_press_xy.x() + delta.x(),
                self._position_press_xy.y() + delta.y(),
            )
        elif self._interaction == "spring":
            self.move_spring(
                event.position().x() - self.width() / 2.0,
                event.position().y() - self._spring_press_y,
            )
        else:
            event.ignore()
            return
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or self._interaction is None:
            event.ignore()
            return
        spring = self._interaction == "spring"
        self._interaction = None
        if spring and self.isVisible():
            self.controller._running = True
            self.quick.requestUpdate()
        event.accept()

    def cancel_interaction(self) -> None:
        self._interaction = None


class SakanaToyController(QObject):
    """Own Sakana rendering, physics, drag, visibility and lifecycle."""

    def __init__(self, window: QWidget, quick: QQuickWindow) -> None:
        super().__init__(window)
        self.window = window
        self.quick = quick
        self._user_positioned = False
        self._last_tick = time.monotonic()
        self._running = True

        self.toy = _SakanaToyItem(quick, self)
        self.toggle = self._install_toggle()

        quick.widthChanged.connect(self._on_window_geometry_changed)
        quick.heightChanged.connect(self._on_window_geometry_changed)
        quick.frameSwapped.connect(self._on_frame_swapped)
        window.destroyed.connect(self.cleanup)

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
        self._move_item(
            _LEFT_MARGIN,
            max(0.0, float(self.quick.height()) - _CANVAS_SIZE - _BOTTOM_MARGIN),
        )

    def _move_item(self, x: float, y: float) -> None:
        max_x = max(0.0, float(self.quick.width()) - _CANVAS_SIZE)
        max_y = max(0.0, float(self.quick.height()) - _CANVAS_SIZE)
        self.toy.setX(max(0.0, min(max_x, float(x))))
        self.toy.setY(max(0.0, min(max_y, float(y))))

    def _on_window_geometry_changed(self, *_args: object) -> None:
        if self._user_positioned:
            self._move_item(self.toy.x(), self.toy.y())
        else:
            self._place_default()

    def _on_frame_swapped(self) -> None:
        if not self._running or not self.toy.isVisible():
            return
        now = time.monotonic()
        elapsed_ms = max(0.0, (now - self._last_tick) * 1000.0)
        self._last_tick = now
        if not advance_spring(self.toy.state, elapsed_ms):
            self._running = False
            return
        self.toy.update()
        self.quick.requestUpdate()

    def set_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if self.toggle.isChecked() != enabled:
            self.toggle.blockSignals(True)
            self.toggle.setChecked(enabled)
            self.toggle.blockSignals(False)
        self.toggle.setText("玩具 · ON" if enabled else "玩具 · OFF")

        if not enabled:
            self.toy.cancel_interaction()
            self._running = False
            self.toy.setVisible(False)
            return

        self.toy.setVisible(True)
        self.toy.setZ(32000.0)
        self._running = True
        self._last_tick = time.monotonic()
        self.toy.update()
        self.quick.requestUpdate()

    def raise_overlay(self) -> None:
        if self.toy.isVisible():
            self.toy.setZ(32000.0)
            self.quick.requestUpdate()

    def cleanup(self) -> None:
        self._running = False
        self.toy.cancel_interaction()
        try:
            self.quick.frameSwapped.disconnect(self._on_frame_swapped)
        except (RuntimeError, TypeError):
            pass
        try:
            self.quick.widthChanged.disconnect(self._on_window_geometry_changed)
            self.quick.heightChanged.disconnect(self._on_window_geometry_changed)
        except (RuntimeError, TypeError):
            pass
        try:
            self.toy.setParentItem(None)
            self.toy.deleteLater()
        except RuntimeError:
            pass


def install_sakana_toy(window: QWidget) -> SakanaToyController:
    existing = getattr(window, "_sakana_toy_controller", None)
    if isinstance(existing, SakanaToyController):
        return existing

    visual = getattr(window, "_visual_style", None)
    background = getattr(visual, "background", None)
    quick = getattr(background, "quick_window", None)
    if not isinstance(quick, QQuickWindow):
        raise RuntimeError("Sakana toy requires the existing unified QQuickWindow")

    controller = SakanaToyController(window, quick)
    window._sakana_toy_controller = controller  # type: ignore[attr-defined]
    return controller
