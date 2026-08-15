from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from time import monotonic

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, QTimer, Signal
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


@dataclass
class _SpringChannel:
    value: float
    target: float
    omega: float
    damping: float
    velocity: float = 0.0

    def retarget(self, target: float) -> None:
        self.target = float(target)

    def snap(self, value: float) -> None:
        self.value = float(value)
        self.target = float(value)
        self.velocity = 0.0

    def step(self, dt: float) -> None:
        acceleration = (
            self.omega * self.omega * (self.target - self.value)
            - 2.0 * self.damping * self.omega * self.velocity
        )
        self.velocity += acceleration * dt
        self.value += self.velocity * dt

    def settled(self, value_epsilon: float = 0.18, velocity_epsilon: float = 0.28) -> bool:
        return (
            abs(self.target - self.value) <= value_epsilon
            and abs(self.velocity) <= velocity_epsilon
        )


class AnchoredQuickPanel(QWidget):
    """Source-faithful anchored morph panel with a lightweight static surface.

    The motion model is a direct Qt port of the original GUI Plus floating
    component: the trigger capsule is the opening geometry, the panel keeps the
    capsule edge as its anchor, width/height/radii move on independent springs,
    and content appears only after the shell has opened.  The expensive live
    backdrop blur from the web prototype is deliberately not reproduced here.

    This class remains presentation-only.  The content widget continues to own
    all business interaction and state.
    """

    dismissed = Signal()

    _MORPH_DURATION_MS = 360
    _PANEL_TOP_RADIUS_OFFSET = 4
    _PANEL_BOTTOM_RADIUS_OFFSET = 14
    _PRESS_DELAY_MS = 79
    _SETTLE_DELAY_MS = 201
    _CONTENT_DELAY_MS = 230
    _FRAME_INTERVAL_MS = 16
    _MAX_TRANSITION_MS = 820

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
        self._safe_margin = max(0, int(safe_margin))
        self._body_padding = max(0, min(18, int(body_padding)))

        # Compatibility arguments from the previous shell are intentionally
        # accepted so callers keep a stable API.  The original component has no
        # speech-bubble tail or detached gap, so those values no longer affect
        # rendering or placement.
        self._legacy_horizontal_bias = float(horizontal_bias)
        self._legacy_tail_height = int(tail_height)
        self._legacy_tail_half_width = int(tail_half_width)
        self._legacy_anchor_gap = int(anchor_gap)

        base_radius = max(10, int(corner_radius))
        # Current material panel passes 25 -> original 29 px / 39 px silhouette.
        self._panel_top_radius = float(base_radius + self._PANEL_TOP_RADIUS_OFFSET)
        self._panel_bottom_radius = float(base_radius + self._PANEL_BOTTOM_RADIUS_OFFSET)

        self._placement = self._preferred
        self._anchor: QWidget | None = None
        self._anchor_rect = QRect()
        self._final_rect = QRect()
        self._visible_session = False
        self._transition_token = 0
        self._phase = "idle"
        self._transition_started_at = 0.0
        self._last_frame_at = 0.0

        self._x = _SpringChannel(0.0, 0.0, 13.8, 0.78)
        self._y = _SpringChannel(0.0, 0.0, 13.0, 0.86)
        self._width = _SpringChannel(1.0, 1.0, 13.8, 0.78)
        self._height = _SpringChannel(1.0, 1.0, 13.0, 0.86)
        self._top_radius = _SpringChannel(1.0, 1.0, 15.8, 0.82)
        self._bottom_radius = _SpringChannel(1.0, 1.0, 11.8, 0.91)

        self._frame_timer = QTimer(self)
        self._frame_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._frame_timer.setInterval(self._FRAME_INTERVAL_MS)
        self._frame_timer.timeout.connect(self._advance_springs)

        self._layout = QVBoxLayout(self)
        self._layout.setSpacing(0)
        self._layout.setContentsMargins(
            self._body_padding,
            self._body_padding,
            self._body_padding,
            self._body_padding,
        )
        self._layout.addWidget(self._content)
        self.hide()

    @property
    def placement(self) -> AnchoredQuickPanelPlacement:
        return self._placement

    def show_anchored(self, anchor: QWidget, *, animate: bool = True) -> None:
        if anchor is None or not anchor.isVisible():
            return

        self._transition_token += 1
        token = self._transition_token
        self._frame_timer.stop()
        self._anchor = anchor
        self._anchor_rect, self._final_rect = self._resolve_geometry(anchor)
        if self._final_rect.width() <= 0 or self._final_rect.height() <= 0:
            return

        self._visible_session = True

        if not animate:
            self.setWindowOpacity(1.0)
            self._snap_to_rect(self._final_rect)
            self._top_radius.snap(self._panel_top_radius)
            self._bottom_radius.snap(self._panel_bottom_radius)
            self._content.setVisible(True)
            self._phase = "idle"
            self.show()
            self.raise_()
            self.activateWindow()
            return

        start = QRect(self._anchor_rect)
        if self.isVisible() and self.width() > 1 and self.height() > 1:
            start = QRect(self.geometry())
        else:
            self._snap_to_rect(start)
            capsule_radius = max(1.0, min(start.width(), start.height()) * 0.5)
            self._top_radius.snap(capsule_radius)
            self._bottom_radius.snap(capsule_radius)

        self._content.setVisible(False)

        # A native Qt.Popup can be re-realized/repositioned by Qt/Windows during
        # show().  Prime that native surface fully transparent, then restore the
        # exact trigger geometry on the next event-loop turn before exposing any
        # pixels.  The user therefore sees the spring morph itself, never the
        # platform's transient first-frame placement.
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()
        self.activateWindow()
        QTimer.singleShot(
            0,
            lambda current=token, origin=QRect(start): self._start_open_transition(
                current,
                origin,
            ),
        )

    def _start_open_transition(self, token: int, start: QRect) -> None:
        if token != self._transition_token or not self.isVisible():
            return

        self._snap_to_rect(start)
        capsule_radius = max(1.0, min(start.width(), start.height()) * 0.5)
        self._top_radius.snap(capsule_radius)
        self._bottom_radius.snap(capsule_radius)

        self._phase = "panel-press"
        self._transition_started_at = monotonic()
        self._last_frame_at = self._transition_started_at
        self.update()
        self.setWindowOpacity(1.0)

        # Original panel-press: very small down/compact anticipation before the
        # spring expansion begins.
        press = self._phase_rect(
            start,
            width_scale=0.993,
            height_scale=0.930,
            edge_offset=6.0,
        )
        self._retarget_rect(press)
        press_radius = max(1.0, min(press.width(), press.height()) * 0.5)
        self._top_radius.retarget(press_radius)
        self._bottom_radius.retarget(press_radius)
        self._frame_timer.start()

        QTimer.singleShot(
            self._PRESS_DELAY_MS,
            lambda current=token: self._begin_panel_lift(current),
        )
        QTimer.singleShot(
            self._SETTLE_DELAY_MS,
            lambda current=token: self._settle_panel(current),
        )
        QTimer.singleShot(
            self._CONTENT_DELAY_MS,
            lambda current=token: self._reveal_content(current),
        )

    def dismiss(self, *, animate: bool = True) -> None:
        if not self.isVisible():
            return

        self._transition_token += 1
        token = self._transition_token
        self._content.setVisible(False)
        if not animate or self._anchor is None or not self._anchor.isVisible():
            self._frame_timer.stop()
            self.hide()
            return

        self._anchor_rect, _unused = self._resolve_geometry(self._anchor)
        self._phase = "closing-anticipation"
        self._transition_started_at = monotonic()
        self._last_frame_at = self._transition_started_at

        current = self.geometry()
        anticipation = self._phase_rect(
            current,
            width_scale=0.985,
            height_scale=1.018,
            edge_offset=-3.0,
        )
        self._retarget_rect(anticipation)
        self._frame_timer.start()
        QTimer.singleShot(54, lambda current_token=token: self._begin_close(current_token))

    def _begin_panel_lift(self, token: int) -> None:
        if token != self._transition_token or not self.isVisible():
            return
        self._phase = "panel-lift"
        lifted = self._phase_rect(
            self._final_rect,
            width_scale=1.004,
            height_scale=1.018,
            edge_offset=-9.0,
        )
        self._retarget_rect(lifted)
        self._top_radius.retarget(self._panel_top_radius * 1.01)
        self._bottom_radius.retarget(self._panel_bottom_radius * 1.01)

    def _settle_panel(self, token: int) -> None:
        if token != self._transition_token or not self.isVisible():
            return
        self._phase = "settling"
        self._retarget_rect(self._final_rect)
        self._top_radius.retarget(self._panel_top_radius)
        self._bottom_radius.retarget(self._panel_bottom_radius)

    def _reveal_content(self, token: int) -> None:
        if token != self._transition_token or not self.isVisible():
            return
        self._content.setVisible(True)
        self._content.raise_()

    def _begin_close(self, token: int) -> None:
        if token != self._transition_token or not self.isVisible():
            return
        self._phase = "closing"
        target = self._anchor_rect
        self._retarget_rect(target)
        capsule_radius = max(1.0, min(target.width(), target.height()) * 0.5)
        self._top_radius.retarget(capsule_radius)
        self._bottom_radius.retarget(capsule_radius)

    def _resolve_geometry(self, anchor: QWidget) -> tuple[QRect, QRect]:
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
        max_width = max(1, available.width() - safe * 2)
        width = min(self._desired_size.width(), max_width)

        # The original form-2 shell grows from the capsule while preserving one
        # capsule edge.  Above therefore ends exactly at the trigger's bottom;
        # below mirrors that rule at the trigger's top.
        available_above = max(1, anchor_rect.bottom() - available.top() - safe + 1)
        available_below = max(1, available.bottom() - anchor_rect.top() - safe + 1)
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
        self._placement = placement
        selected_space = (
            available_above
            if placement is AnchoredQuickPanelPlacement.Above
            else available_below
        )
        height = min(self._desired_size.height(), max(1, selected_space))

        desired_x = anchor_rect.center().x() - width // 2
        min_x = available.left() + safe
        max_x = max(min_x, available.right() - safe - width + 1)
        x = max(min_x, min(max_x, desired_x))
        if placement is AnchoredQuickPanelPlacement.Above:
            y = anchor_rect.bottom() - height + 1
        else:
            y = anchor_rect.top()

        return anchor_rect, QRect(x, y, width, height)

    def _phase_rect(
        self,
        base: QRect,
        *,
        width_scale: float,
        height_scale: float,
        edge_offset: float,
    ) -> QRect:
        width = max(1, int(round(base.width() * width_scale)))
        height = max(1, int(round(base.height() * height_scale)))
        center_x = base.center().x()
        x = center_x - width // 2
        if self._placement is AnchoredQuickPanelPlacement.Above:
            edge = base.bottom() + 1 + int(round(edge_offset))
            y = edge - height
        else:
            edge = base.top() - int(round(edge_offset))
            y = edge
        return QRect(x, y, width, height)

    def _snap_to_rect(self, rect: QRect) -> None:
        self._x.snap(float(rect.x()))
        self._y.snap(float(rect.y()))
        self._width.snap(float(rect.width()))
        self._height.snap(float(rect.height()))
        self.setGeometry(rect)

    def _retarget_rect(self, rect: QRect) -> None:
        self._x.retarget(float(rect.x()))
        self._y.retarget(float(rect.y()))
        self._width.retarget(float(rect.width()))
        self._height.retarget(float(rect.height()))

    def _advance_springs(self) -> None:
        if not self.isVisible():
            self._frame_timer.stop()
            return

        now = monotonic()
        dt = min(0.032, max(0.001, now - self._last_frame_at))
        self._last_frame_at = now
        for channel in (
            self._x,
            self._y,
            self._width,
            self._height,
            self._top_radius,
            self._bottom_radius,
        ):
            channel.step(dt)

        rect = QRect(
            int(round(self._x.value)),
            int(round(self._y.value)),
            max(1, int(round(self._width.value))),
            max(1, int(round(self._height.value))),
        )
        if self.geometry() != rect:
            self.setGeometry(rect)
        self.update()

        elapsed_ms = (now - self._transition_started_at) * 1000.0
        settled = all(
            channel.settled()
            for channel in (
                self._x,
                self._y,
                self._width,
                self._height,
                self._top_radius,
                self._bottom_radius,
            )
        )
        if self._phase == "closing" and (
            settled or elapsed_ms >= self._MAX_TRANSITION_MS
        ):
            self._frame_timer.stop()
            self.hide()
            return
        if self._phase in {"settling", "panel-lift"} and (
            settled or elapsed_ms >= self._MAX_TRANSITION_MS
        ):
            self._frame_timer.stop()
            self._phase = "idle"
            self._snap_to_rect(self._final_rect)
            self._top_radius.snap(self._panel_top_radius)
            self._bottom_radius.snap(self._panel_bottom_radius)
            self._content.setVisible(True)
            self.update()

    def _shape_path(self) -> QPainterPath:
        rect = QRectF(
            0.8,
            0.8,
            max(1.0, self.width() - 1.6),
            max(1.0, self.height() - 1.6),
        )
        max_radius = max(1.0, min(rect.width(), rect.height()) * 0.5)
        top = min(max_radius, max(1.0, self._top_radius.value))
        bottom = min(max_radius, max(1.0, self._bottom_radius.value))

        path = QPainterPath()
        path.moveTo(rect.left() + top, rect.top())
        path.lineTo(rect.right() - top, rect.top())
        path.quadTo(rect.right(), rect.top(), rect.right(), rect.top() + top)
        path.lineTo(rect.right(), rect.bottom() - bottom)
        path.quadTo(
            rect.right(),
            rect.bottom(),
            rect.right() - bottom,
            rect.bottom(),
        )
        path.lineTo(rect.left() + bottom, rect.bottom())
        path.quadTo(rect.left(), rect.bottom(), rect.left(), rect.bottom() - bottom)
        path.lineTo(rect.left(), rect.top() + top)
        path.quadTo(rect.left(), rect.top(), rect.left() + top, rect.top())
        path.closeSubpath()
        return path

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        path = self._shape_path()

        # Source quick-panel surface without backdrop-filter: same deep-blue
        # direction and translucent glass values, but no live background sampling.
        gradient = QLinearGradient(
            0.0,
            0.0,
            float(self.width()),
            float(self.height()),
        )
        gradient.setColorAt(0.0, QColor(21, 45, 78, 245))
        gradient.setColorAt(1.0, QColor(8, 22, 48, 245))
        painter.fillPath(path, gradient)

        painter.setPen(QPen(QColor(221, 239, 253, 38), 1.0))
        painter.drawPath(path)
        painter.setPen(QPen(QColor(255, 255, 255, 18), 0.8))
        painter.drawPath(path)
        painter.end()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        if event.key() == Qt.Key.Key_Escape:
            event.accept()
            self.dismiss()
            return
        super().keyPressEvent(event)

    def hideEvent(self, event) -> None:  # type: ignore[override]
        self._transition_token += 1
        self._frame_timer.stop()
        self._phase = "idle"
        self.setWindowOpacity(1.0)
        super().hideEvent(event)
        if self._visible_session:
            self._visible_session = False
            self.dismissed.emit()


__all__ = ["AnchoredQuickPanel", "AnchoredQuickPanelPlacement"]
