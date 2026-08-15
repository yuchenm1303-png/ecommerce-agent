from __future__ import annotations

from enum import Enum

from PySide6.QtCore import (
    QEasingCurve,
    QParallelAnimationGroup,
    QPoint,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QKeyEvent,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import QVBoxLayout, QWidget


class AnchoredQuickPanelPlacement(str, Enum):
    Above = "above"
    Below = "below"


class AnchoredQuickPanel(QWidget):
    """Reusable anchored floating panel for compact detail workflows.

    The panel owns only presentation and placement. Its content widget owns the
    business interaction. Geometry is resolved in screen coordinates from the
    live anchor on every open, so rows may scroll, move between monitors, or be
    used in a resized window without any hard-coded desktop coordinates.
    """

    dismissed = Signal()

    def __init__(
        self,
        parent: QWidget,
        content: QWidget,
        *,
        desired_width: int = 420,
        desired_height: int = 340,
        min_height: int = 240,
        preferred_placement: AnchoredQuickPanelPlacement = AnchoredQuickPanelPlacement.Above,
        horizontal_bias: float = 0.76,
        corner_radius: int = 24,
        tail_height: int = 12,
        tail_half_width: int = 15,
        safe_margin: int = 10,
        anchor_gap: int = 7,
        body_padding: int = 14,
    ) -> None:
        flags = (
            Qt.WindowType.Popup
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        super().__init__(parent, flags)
        self.setObjectName("anchoredQuickPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._content = content
        self._content.setParent(self)
        self._desired_size = QSize(
            max(260, int(desired_width)),
            max(180, int(desired_height)),
        )
        self._min_height = max(140, int(min_height))
        self._preferred = preferred_placement
        self._horizontal_bias = max(0.16, min(0.84, float(horizontal_bias)))
        self._corner_radius = max(10, int(corner_radius))
        self._tail_height = max(0, int(tail_height))
        self._tail_half_width = max(6, int(tail_half_width))
        self._safe_margin = max(0, int(safe_margin))
        self._anchor_gap = max(0, int(anchor_gap))
        self._body_padding = max(0, int(body_padding))

        self._placement = self._preferred
        self._tail_fraction = 0.5
        self._anchor: QWidget | None = None
        self._animation: QParallelAnimationGroup | None = None
        self._visible_session = False

        self._layout = QVBoxLayout(self)
        self._layout.setSpacing(0)
        self._layout.addWidget(self._content)
        self._sync_content_margins()
        self.hide()

    @property
    def placement(self) -> AnchoredQuickPanelPlacement:
        return self._placement

    def show_anchored(self, anchor: QWidget, *, animate: bool = True) -> None:
        if anchor is None or not anchor.isVisible():
            return

        self._anchor = anchor
        final_rect = self._resolve_geometry(anchor)
        if final_rect.width() <= 0 or final_rect.height() <= 0:
            return

        self._stop_animation()
        self._sync_content_margins()
        self._visible_session = True

        if not animate:
            self.setGeometry(final_rect)
            self.setWindowOpacity(1.0)
            self.show()
            self.raise_()
            self.activateWindow()
            return

        start_rect = self._collapsed_rect(final_rect)
        self.setGeometry(start_rect)
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()
        self.activateWindow()

        group = QParallelAnimationGroup(self)
        geometry = QPropertyAnimation(self, b"geometry", group)
        geometry.setDuration(210)
        geometry.setStartValue(start_rect)
        geometry.setEndValue(final_rect)
        geometry.setEasingCurve(QEasingCurve.Type.OutCubic)

        opacity = QPropertyAnimation(self, b"windowOpacity", group)
        opacity.setDuration(118)
        opacity.setStartValue(0.0)
        opacity.setEndValue(1.0)
        opacity.setEasingCurve(QEasingCurve.Type.OutCubic)

        group.finished.connect(lambda: self.setWindowOpacity(1.0))
        self._animation = group
        group.start()

    def dismiss(self, *, animate: bool = True) -> None:
        if not self.isVisible():
            return

        self._stop_animation()
        if not animate:
            self.hide()
            return

        final_rect = self.geometry()
        collapsed = self._collapsed_rect(final_rect, x_scale=0.88, y_scale=0.72)
        group = QParallelAnimationGroup(self)

        geometry = QPropertyAnimation(self, b"geometry", group)
        geometry.setDuration(105)
        geometry.setStartValue(final_rect)
        geometry.setEndValue(collapsed)
        geometry.setEasingCurve(QEasingCurve.Type.InCubic)

        opacity = QPropertyAnimation(self, b"windowOpacity", group)
        opacity.setDuration(92)
        opacity.setStartValue(max(0.0, float(self.windowOpacity())))
        opacity.setEndValue(0.0)
        opacity.setEasingCurve(QEasingCurve.Type.InCubic)

        group.finished.connect(self.hide)
        self._animation = group
        group.start()

    def _stop_animation(self) -> None:
        animation = self._animation
        self._animation = None
        if animation is not None:
            animation.stop()
            animation.deleteLater()

    def _resolve_geometry(self, anchor: QWidget) -> QRect:
        top_left = anchor.mapToGlobal(QPoint(0, 0))
        anchor_rect = QRect(top_left, anchor.size())
        screen = (
            QGuiApplication.screenAt(anchor_rect.center())
            or anchor.screen()
            or QGuiApplication.primaryScreen()
        )
        if screen is None:
            available = QRect(
                anchor_rect.left() - 600,
                anchor_rect.top() - 500,
                1200,
                1000,
            )
        else:
            available = screen.availableGeometry()

        safe = self._safe_margin
        gap = self._anchor_gap
        max_width = max(1, available.width() - safe * 2)
        width = min(self._desired_size.width(), max_width)

        available_above = max(
            1,
            anchor_rect.top() - gap - safe - available.top(),
        )
        available_below = max(
            1,
            available.bottom() - safe - gap - anchor_rect.bottom(),
        )
        preferred_space = (
            available_above
            if self._preferred is AnchoredQuickPanelPlacement.Above
            else available_below
        )
        alternate_space = (
            available_below
            if self._preferred is AnchoredQuickPanelPlacement.Above
            else available_above
        )

        placement = self._preferred
        if preferred_space < self._min_height and alternate_space > preferred_space:
            placement = (
                AnchoredQuickPanelPlacement.Below
                if self._preferred is AnchoredQuickPanelPlacement.Above
                else AnchoredQuickPanelPlacement.Above
            )
        selected_space = (
            available_above
            if placement is AnchoredQuickPanelPlacement.Above
            else available_below
        )
        height = min(self._desired_size.height(), max(1, selected_space))
        self._placement = placement

        center_x = anchor_rect.center().x()
        desired_x = int(round(center_x - width * self._horizontal_bias))
        min_x = available.left() + safe
        max_x = max(min_x, available.right() - safe - width + 1)
        x = max(min_x, min(max_x, desired_x))
        if placement is AnchoredQuickPanelPlacement.Above:
            y = anchor_rect.top() - gap - height
        else:
            y = anchor_rect.bottom() + gap + 1

        self._tail_fraction = max(
            0.12,
            min(0.88, (center_x - x) / max(1.0, float(width))),
        )
        return QRect(x, y, width, height)

    def _collapsed_rect(
        self,
        final_rect: QRect,
        *,
        x_scale: float = 0.42,
        y_scale: float = 0.12,
    ) -> QRect:
        width = max(1, int(round(final_rect.width() * x_scale)))
        height = max(1, int(round(final_rect.height() * y_scale)))
        pivot_x = final_rect.left() + int(
            round(final_rect.width() * self._tail_fraction)
        )
        pivot_y = (
            final_rect.bottom() + 1
            if self._placement is AnchoredQuickPanelPlacement.Above
            else final_rect.top()
        )
        left = pivot_x - int(round(width * self._tail_fraction))
        top = (
            pivot_y - height
            if self._placement is AnchoredQuickPanelPlacement.Above
            else pivot_y
        )
        return QRect(left, top, width, height)

    def _sync_content_margins(self) -> None:
        tail_top = (
            self._tail_height
            if self._placement is AnchoredQuickPanelPlacement.Below
            else 0
        )
        tail_bottom = (
            self._tail_height
            if self._placement is AnchoredQuickPanelPlacement.Above
            else 0
        )
        pad = self._body_padding
        self._layout.setContentsMargins(
            pad,
            pad + tail_top,
            pad,
            pad + tail_bottom,
        )

    def _shape_path(self) -> QPainterPath:
        width = float(max(1, self.width()))
        height = float(max(1, self.height()))
        tail = float(min(self._tail_height, max(0, self.height() // 4)))
        if self._placement is AnchoredQuickPanelPlacement.Above:
            body = QRectF(
                0.8,
                0.8,
                max(1.0, width - 1.6),
                max(1.0, height - tail - 1.6),
            )
            edge_y = body.bottom()
            tip_y = height - 0.8
        else:
            body = QRectF(
                0.8,
                tail + 0.8,
                max(1.0, width - 1.6),
                max(1.0, height - tail - 1.6),
            )
            edge_y = body.top()
            tip_y = 0.8

        radius = min(
            float(self._corner_radius),
            body.width() * 0.22,
            body.height() * 0.32,
        )
        path = QPainterPath()
        path.addRoundedRect(body, radius, radius)

        center_x = max(
            radius + self._tail_half_width,
            min(
                width - radius - self._tail_half_width,
                width * self._tail_fraction,
            ),
        )
        tail_path = QPainterPath()
        tail_path.moveTo(center_x - self._tail_half_width, edge_y)
        delta_y = tip_y - edge_y
        midpoint_y = edge_y + delta_y * 0.58
        tail_path.cubicTo(
            center_x - self._tail_half_width * 0.48,
            edge_y + delta_y * 0.10,
            center_x - self._tail_half_width * 0.20,
            midpoint_y,
            center_x,
            tip_y,
        )
        tail_path.cubicTo(
            center_x + self._tail_half_width * 0.20,
            midpoint_y,
            center_x + self._tail_half_width * 0.48,
            edge_y + delta_y * 0.10,
            center_x + self._tail_half_width,
            edge_y,
        )
        tail_path.closeSubpath()
        return path.united(tail_path)

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        path = self._shape_path()

        gradient = QLinearGradient(
            0.0,
            0.0,
            float(self.width()),
            float(self.height()),
        )
        gradient.setColorAt(0.0, QColor(12, 29, 48, 244))
        gradient.setColorAt(0.46, QColor(13, 37, 61, 238))
        gradient.setColorAt(0.76, QColor(19, 36, 72, 240))
        gradient.setColorAt(1.0, QColor(28, 31, 70, 242))
        painter.fillPath(path, gradient)

        painter.setPen(QPen(QColor(141, 255, 244, 32), 2.2))
        painter.drawPath(path)
        painter.setPen(QPen(QColor(255, 255, 255, 49), 0.9))
        painter.drawPath(path)
        painter.end()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        if event.key() == Qt.Key.Key_Escape:
            event.accept()
            self.dismiss()
            return
        super().keyPressEvent(event)

    def hideEvent(self, event) -> None:  # type: ignore[override]
        self._stop_animation()
        self.setWindowOpacity(1.0)
        super().hideEvent(event)
        if self._visible_session:
            self._visible_session = False
            self.dismissed.emit()


__all__ = ["AnchoredQuickPanel", "AnchoredQuickPanelPlacement"]
