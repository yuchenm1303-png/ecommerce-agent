from __future__ import annotations

import math
import time
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen, QPixmap, QTransform
from PySide6.QtQuick import QQuickPaintedItem, QQuickWindow
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from gui.sakana_physics import SakanaSpringState, advance_spring, move_spring


# Exact Sakana Widget geometry. The 200 px app/main is the positioned widget;
# its 300 px canvas is centered around that root and overflows by 50 px per side.
_TOY_SIZE = 200.0
_IMAGE_SIZE = _TOY_SIZE / 1.25
_CANVAS_SIZE = _TOY_SIZE * 1.5
_CANVAS_INSET = (_CANVAS_SIZE - _TOY_SIZE) / 2.0
_MAIN_CENTER_X = _CANVAS_INSET + _TOY_SIZE / 2.0
_LEFT_MARGIN = 24.0
_BOTTOM_MARGIN = 18.0
_CHARACTER_IMAGE = Path(__file__).resolve().parent / "assets" / "sakana_character.png"

# Keep the upstream controller geometry, with only the user-requested visual
# difference: a plain white controller without symbols.
_BASE_WIDTH = 112.0
_BASE_HEIGHT = 24.0
_BASE_RADIUS = 6.0


class _SakanaToyItem(QQuickPaintedItem):
    """Paint the upstream Sakana canvas inside the application's QQuickWindow."""

    def __init__(self, quick: QQuickWindow, controller: "SakanaToyController") -> None:
        super().__init__(quick.contentItem())
        self.quick = quick
        self.controller = controller
        self.setWidth(_CANVAS_SIZE)
        self.setHeight(_CANVAS_SIZE)
        self.setZ(32000.0)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
        self.setAcceptHoverEvents(False)
        self.setOpaquePainting(False)

        # Exact upstream Takina state: i=.08, s=.1, d=.988, r=12, y=2, t=0, w=0.
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
        self._position_press_root = QPointF()

    @property
    def anchor(self) -> QPointF:
        # Upstream canvas draw origin after translating the centered 1.5x canvas.
        return QPointF(_CANVAS_SIZE / 2.0, _TOY_SIZE + _CANVAS_INSET)

    @property
    def image_rect(self) -> QRectF:
        # The upstream character element is always a 160 x 160 square.
        return QRectF(-_IMAGE_SIZE / 2.0, -_TOY_SIZE, _IMAGE_SIZE, _IMAGE_SIZE)

    def image_source_rect(self) -> QRectF:
        # CSS uses background-size: cover and background-position: 50% 50%.
        # Mirror that at draw time without changing, resizing or recompressing the
        # transparent source asset itself.
        width = float(self.pixmap.width())
        height = float(self.pixmap.height())
        side = min(width, height)
        return QRectF((width - side) / 2.0, (height - side) / 2.0, side, side)

    def base_rect(self) -> QRectF:
        anchor = self.anchor
        return QRectF(
            anchor.x() - _BASE_WIDTH / 2.0,
            anchor.y() - _BASE_HEIGHT,
            _BASE_WIDTH,
            _BASE_HEIGHT,
        )

    def _image_transform(self) -> QTransform:
        # CSS: transform-origin: 50% size; transform: rotate(r) translateX(r) translateY(y).
        # Build the affine matrix explicitly so the character center is mathematically
        # identical to the rod endpoint instead of relying on Qt transform call order.
        angle = math.radians(self.state.r)
        cos_a = math.cos(angle)
        sin_a = math.sin(angle)
        x = self.state.r
        y = self.state.y
        anchor = self.anchor
        dx = anchor.x() + cos_a * x - sin_a * y
        dy = anchor.y() + sin_a * x + cos_a * y
        return QTransform(cos_a, sin_a, -sin_a, cos_a, dx, dy)

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

        # Upstream z-order: canvas rod (10), controller (30), character (40).
        pen = QPen(QColor("#b4b4b4"))
        pen.setWidthF(10.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawLine(QPointF(anchor.x(), anchor.y() - 10.0), end)

        rect = self.base_rect()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 18))
        painter.drawRoundedRect(rect.translated(0.0, 5.0), _BASE_RADIUS, _BASE_RADIUS)
        painter.setBrush(QColor("#ffffff"))
        painter.drawRoundedRect(rect, _BASE_RADIUS, _BASE_RADIUS)

        painter.save()
        painter.setTransform(self._image_transform(), combine=False)
        painter.drawPixmap(self.image_rect, self.pixmap, self.image_source_rect())
        painter.restore()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or not self.isVisible():
            event.ignore()
            return

        local = event.position()
        if self.base_hit_test(local):
            self._interaction = "position"
            self._position_press_global = event.globalPosition()
            self._position_press_root = self.controller.root_position
            self.controller._user_positioned = True
        elif self.character_hit_test(local):
            # Upstream _onMouseDown stops RAF, records pageY, and zeros both speeds.
            self._interaction = "spring"
            self._spring_press_y = event.position().y()
            self.controller._pause_spring()
            self.state.w = 0.0
            self.state.t = 0.0
        else:
            event.ignore()
            return

        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._interaction == "position":
            delta = event.globalPosition() - self._position_press_global
            self.controller._move_root(
                self._position_press_root.x() + delta.x(),
                self._position_press_root.y() + delta.y(),
            )
        elif self._interaction == "spring":
            # Upstream uses pageX - main.getBoundingClientRect().centerX and
            # pageY - mouseDownPageY. The 200 px main center is canvas-local 150.
            self.move_spring(
                event.position().x() - _MAIN_CENTER_X,
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
            # Upstream mouseup requests the next RAF without resetting _lastRunUnix.
            self.controller._resume_spring_after_drag()
        event.accept()

    def cancel_interaction(self) -> None:
        self._interaction = None


class SakanaToyController(QObject):
    """Own exact Sakana geometry, display-frame stepping, drag and visibility."""

    def __init__(self, window: QWidget, quick: QQuickWindow) -> None:
        super().__init__(window)
        self.window = window
        self.quick = quick
        self._user_positioned = False
        self._root_x = 0.0
        self._root_y = 0.0

        # Browser requestAnimationFrame runs on the presentation animation phase.
        # QQuickWindow.afterAnimating is the GUI-thread equivalent: once per Quick
        # animation frame, before scene-graph synchronization and painting.
        self._last_tick = time.monotonic()
        self._running = True

        self.toy = _SakanaToyItem(quick, self)
        self.toggle = self._install_toggle()

        quick.widthChanged.connect(self._on_window_geometry_changed)
        quick.heightChanged.connect(self._on_window_geometry_changed)
        quick.afterAnimating.connect(self._on_animation_frame)
        window.destroyed.connect(self.cleanup)

        self._place_default()
        self.set_enabled(True)

    @property
    def root_position(self) -> QPointF:
        return QPointF(self._root_x, self._root_y)

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

    def _pause_spring(self) -> None:
        self._running = False

    def _resume_spring_after_drag(self) -> None:
        self._running = True
        self.quick.requestUpdate()

    def _on_animation_frame(self) -> None:
        if not self._running or not self.toy.isVisible():
            return

        now = time.monotonic()
        elapsed_ms = max(0.0, (now - self._last_tick) * 1000.0)
        self._last_tick = now

        if not advance_spring(self.toy.state, elapsed_ms):
            self._running = False
            return

        self.toy.update()
        # requestAnimationFrame schedules the following frame while _running.
        self.quick.requestUpdate()

    def _place_default(self) -> None:
        # Position the 200 px Sakana app/main. Its 300 px canvas overflows around it.
        self._move_root(
            _LEFT_MARGIN,
            max(0.0, float(self.quick.height()) - _TOY_SIZE - _BOTTOM_MARGIN),
        )

    def _move_root(self, x: float, y: float) -> None:
        max_x = max(0.0, float(self.quick.width()) - _TOY_SIZE)
        max_y = max(0.0, float(self.quick.height()) - _TOY_SIZE)
        self._root_x = max(0.0, min(max_x, float(x)))
        self._root_y = max(0.0, min(max_y, float(y)))

        # The painted item represents only the centered overflow canvas.
        self.toy.setX(self._root_x - _CANVAS_INSET)
        self.toy.setY(self._root_y - _CANVAS_INSET)

    def _on_window_geometry_changed(self, *_args: object) -> None:
        if self._user_positioned:
            self._move_root(self._root_x, self._root_y)
        else:
            self._place_default()

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
            self.quick.afterAnimating.disconnect(self._on_animation_frame)
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
