"""Continuous universal wheel scrolling without a QApplication-wide event filter."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import Property, QEvent, QObject, QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtWidgets import QAbstractItemView, QAbstractScrollArea, QWidget


@dataclass(slots=True)
class _ScrollMotion:
    area: QAbstractScrollArea
    bar: object
    position: float
    target: float
    velocity: float
    committed_position: float
    hold_until_s: float = 0.0


class QuickScrollState(QObject):
    """Publish transient scrollbar positions without recomputing viewport geometry per frame.

    QWidget remains the committed scroll/layout state owner, while Quick owns active
    scroll presentation. Viewport identity changes only when layout geometry changes,
    so it is cached independently from the high-frequency transient scroll position.
    """

    positionsChanged = Signal()

    def __init__(self, root: QWidget, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._root = root
        self._positions: dict[str, dict[str, int]] = {}
        self._area_keys: dict[int, str] = {}
        self._watched_area_ids: set[int] = set()

    def _get_positions(self):  # noqa: ANN201
        return self._positions

    positions = Property("QVariantMap", _get_positions, notify=positionsChanged)

    def _compute_viewport_key(self, area: QAbstractScrollArea) -> str | None:
        try:
            viewport = area.viewport()
            point = viewport.mapTo(self._root, QPoint(0, 0))
            return f"{int(point.x())}:{int(point.y())}:{int(viewport.width())}:{int(viewport.height())}"
        except RuntimeError:
            return None

    def _viewport_key(self, area: QAbstractScrollArea, *, refresh: bool = False) -> str | None:
        identity = id(area)
        if not refresh:
            cached = self._area_keys.get(identity)
            if cached is not None:
                return cached
        return self._compute_viewport_key(area)

    def watch_area(self, area: QAbstractScrollArea) -> None:
        identity = id(area)
        if identity not in self._watched_area_ids:
            self._watched_area_ids.add(identity)
            try:
                area.verticalScrollBar().valueChanged.connect(
                    lambda *_args, source=area: self.sync_area(source)
                )
                area.horizontalScrollBar().valueChanged.connect(
                    lambda *_args, source=area: self.sync_area(source)
                )
            except (RuntimeError, TypeError):
                pass
        self.sync_area(area, refresh_geometry=True)

    def set_area_position(
        self,
        area: QAbstractScrollArea,
        *,
        x: float | None = None,
        y: float | None = None,
        refresh_geometry: bool = False,
    ) -> None:
        key = self._viewport_key(area, refresh=refresh_geometry)
        if key is None:
            return
        try:
            value = {
                "x": int(round(area.horizontalScrollBar().value() if x is None else x)),
                "y": int(round(area.verticalScrollBar().value() if y is None else y)),
            }
        except RuntimeError:
            return

        identity = id(area)
        previous_key = self._area_keys.get(identity)
        previous_value = self._positions.get(key)
        if previous_key == key and previous_value == value:
            return

        positions = dict(self._positions)
        if previous_key is not None and previous_key != key:
            positions.pop(previous_key, None)
        positions[key] = value
        self._area_keys[identity] = key
        self._positions = positions
        self.positionsChanged.emit()

    def sync_area(self, area: QAbstractScrollArea, *, refresh_geometry: bool = False) -> None:
        self.set_area_position(area, refresh_geometry=refresh_geometry)

    def sync_all(self, areas: list[QAbstractScrollArea]) -> None:
        for area in areas:
            self.watch_area(area)

    def clear(self) -> None:
        self._positions = {}
        self._area_keys.clear()
        self._watched_area_ids.clear()
        self.positionsChanged.emit()


class SmoothScroller(QObject):
    """Continuous Quick-side target follower with one committed QWidget update.

    Discrete wheel input extends one persistent spring target. Precision touchpad
    input moves the Quick presentation directly and is committed after a short idle
    window. In both cases the hidden QWidget scroll area is kept off the frame hot
    path and receives only the final settled value.
    """

    _STEP_MS = 16
    _WHEEL_TRAVEL_PX = 92.0
    _SPRING_OMEGA = 13.0
    _STOP_DISTANCE_PX = 0.45
    _STOP_SPEED_PX_S = 5.0
    _REVERSE_VELOCITY_RETENTION = 0.30
    _EXTERNAL_SYNC_TOLERANCE_PX = 3.0
    _PIXEL_COMMIT_IDLE_S = 0.080

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._animations: dict[int, _ScrollMotion] = {}
        self._presentation_state: QuickScrollState | None = None
        self._last_tick_s = time.perf_counter()
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(self._STEP_MS)
        self._timer.timeout.connect(self._tick)

    def set_presentation_state(self, state: QuickScrollState | None) -> None:
        self._presentation_state = state

    @staticmethod
    def _clamp_position(bar, position: float) -> float:  # noqa: ANN001
        return float(max(bar.minimum(), min(bar.maximum(), position)))

    def _ensure_timer(self) -> None:
        if self._timer.isActive():
            return
        self._last_tick_s = time.perf_counter()
        self._timer.start()

    def _stop_if_idle(self) -> None:
        if not self._animations:
            self._timer.stop()

    def _new_motion(self, area: QAbstractScrollArea) -> _ScrollMotion | None:
        try:
            bar = area.verticalScrollBar()
            actual = float(bar.value())
        except RuntimeError:
            return None
        motion = _ScrollMotion(
            area=area,
            bar=bar,
            position=actual,
            target=actual,
            velocity=0.0,
            committed_position=actual,
        )
        self._animations[id(bar)] = motion
        return motion

    def _motion_for(self, area: QAbstractScrollArea) -> _ScrollMotion | None:
        try:
            bar = area.verticalScrollBar()
            actual = float(bar.value())
        except RuntimeError:
            return None

        motion = self._animations.get(id(bar))
        if motion is None:
            return self._new_motion(area)

        if abs(actual - motion.committed_position) > self._EXTERNAL_SYNC_TOLERANCE_PX:
            motion.position = actual
            motion.target = actual
            motion.velocity = 0.0
            motion.committed_position = actual
            motion.hold_until_s = 0.0
            state = self._presentation_state
            if state is not None:
                state.sync_area(area)
        return motion

    def effective_position(self, bar) -> float:  # noqa: ANN001
        motion = self._animations.get(id(bar))
        if motion is not None:
            return float(motion.target)
        try:
            return float(bar.value())
        except RuntimeError:
            return 0.0

    def scroll_pixels(self, area: QAbstractScrollArea, delta_px: float) -> None:
        """Present high-resolution pixel input immediately, then commit once idle."""

        motion = self._motion_for(area)
        if motion is None:
            return
        bar = motion.bar
        target = self._clamp_position(bar, motion.position + float(delta_px))
        motion.position = target
        motion.target = target
        motion.velocity = 0.0
        motion.hold_until_s = time.perf_counter() + self._PIXEL_COMMIT_IDLE_S
        state = self._presentation_state
        if state is not None:
            state.set_area_position(area, y=target)
        self._ensure_timer()

    def add_wheel_delta(self, area: QAbstractScrollArea, notch_delta: float) -> None:
        """Extend one persistent Quick-side spring target by a wheel notch."""

        if abs(notch_delta) <= 1e-6:
            return
        motion = self._motion_for(area)
        if motion is None:
            return

        delta_px = float(notch_delta) * self._WHEEL_TRAVEL_PX
        remaining = motion.target - motion.position
        if remaining * delta_px < 0.0:
            motion.target = motion.position
            motion.velocity *= self._REVERSE_VELOCITY_RETENTION

        motion.target = self._clamp_position(motion.bar, motion.target + delta_px)
        motion.hold_until_s = 0.0
        self._ensure_timer()

    def _commit_motion(self, key: int, motion: _ScrollMotion, final: float) -> None:
        final = self._clamp_position(motion.bar, final)
        motion.committed_position = final
        motion.position = final
        motion.target = final
        state = self._presentation_state
        if state is not None:
            state.set_area_position(motion.area, y=final)
        try:
            motion.bar.setValue(round(final))
        except RuntimeError:
            pass
        self._animations.pop(key, None)

    def _tick(self) -> None:
        now = time.perf_counter()
        dt = max(0.001, min(0.050, now - self._last_tick_s))
        self._last_tick_s = now
        omega = self._SPRING_OMEGA
        decay = math.exp(-omega * dt)
        state = self._presentation_state

        for key, motion in list(self._animations.items()):
            bar = motion.bar
            try:
                actual = float(bar.value())
                minimum = float(bar.minimum())
                maximum = float(bar.maximum())
            except RuntimeError:
                del self._animations[key]
                continue

            if abs(actual - motion.committed_position) > self._EXTERNAL_SYNC_TOLERANCE_PX:
                motion.position = actual
                motion.target = actual
                motion.velocity = 0.0
                motion.committed_position = actual
                motion.hold_until_s = 0.0
                if state is not None:
                    state.sync_area(motion.area)
                del self._animations[key]
                continue

            if motion.hold_until_s > now:
                continue
            if motion.hold_until_s > 0.0:
                self._commit_motion(key, motion, motion.target)
                continue

            offset = motion.position - motion.target
            c2 = motion.velocity + omega * offset
            next_offset = (offset + c2 * dt) * decay
            next_velocity = (motion.velocity - omega * c2 * dt) * decay
            next_position = motion.target + next_offset
            clamped = max(minimum, min(maximum, next_position))
            hit_boundary = clamped != next_position

            motion.position = clamped
            motion.velocity = next_velocity
            if state is not None:
                state.set_area_position(motion.area, y=clamped)

            distance = abs(motion.target - motion.position)
            if hit_boundary or (
                distance <= self._STOP_DISTANCE_PX
                and abs(motion.velocity) <= self._STOP_SPEED_PX_S
            ):
                self._commit_motion(key, motion, motion.target)

        self._stop_if_idle()


class SmoothWheelFilter(QObject):
    """Universal nested scrolling for both QWidget and the unified Quick owner."""

    _ANGLE_UNITS_PER_NOTCH = 120.0
    _GEOMETRY_EVENTS = {
        QEvent.Type.Move,
        QEvent.Type.Resize,
        QEvent.Type.Show,
        QEvent.Type.Hide,
        QEvent.Type.LayoutRequest,
        QEvent.Type.ParentChange,
    }

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._scroller = SmoothScroller(self)
        self._areas: dict[QObject, QAbstractScrollArea] = {}
        self._root: QWidget | None = None
        self._quick_owner: QObject | None = None
        self._quick_state: QuickScrollState | None = None
        self._quick_bridge: Any = None
        self._bridge_scroll_detached = False
        self._quick_hit_rects: dict[int, tuple[QRect, QAbstractScrollArea]] = {}
        self._quick_hit_geometry_dirty = True

    def install(self, root: QWidget) -> None:
        self._root = root
        self._quick_state = QuickScrollState(root, self)
        self._scroller.set_presentation_state(self._quick_state)
        for area in root.findChildren(QAbstractScrollArea):
            self._attach(area)

        # Normal presentation now belongs to the existing QQuickWindow while the
        # QWidget tree remains the canonical committed scroll/layout state owner.
        visual = getattr(root, "_visual_style", None)
        background = getattr(visual, "background", None)
        quick_owner = getattr(background, "quick_window", None)
        if isinstance(quick_owner, QObject):
            self._quick_owner = quick_owner
            quick_owner.installEventFilter(self)

        # The engine already exists here but StaticQmlView is created later. Expose
        # the lightweight scroll state before the QML component is compiled.
        engine = getattr(background, "engine", None)
        context_getter = getattr(engine, "rootContext", None)
        if callable(context_getter):
            try:
                context_getter().setContextProperty("quickScrollState", self._quick_state)
            except RuntimeError:
                pass

        QTimer.singleShot(0, self._adopt_quick_scroll_presentation)

    def _attach(self, area: QAbstractScrollArea) -> None:
        if isinstance(area, QAbstractItemView):
            area.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        state = self._quick_state
        if state is not None:
            state.watch_area(area)
        for watched in (area, area.viewport()):
            if watched in self._areas:
                continue
            self._areas[watched] = area
            watched.installEventFilter(self)
        self._quick_hit_geometry_dirty = True
        if self._bridge_scroll_detached:
            self._detach_bridge_value_refresh(area)

    def _adopt_quick_scroll_presentation(self) -> None:
        root = self._root
        controller = getattr(root, "_static_qml_view_controller", None) if root is not None else None
        bridge = getattr(controller, "bridge", None)
        if bridge is None:
            QTimer.singleShot(0, self._adopt_quick_scroll_presentation)
            return
        self._quick_bridge = bridge
        self._bridge_scroll_detached = True
        for area in set(self._areas.values()):
            self._detach_bridge_value_refresh(area)
        state = self._quick_state
        if state is not None:
            state.sync_all(list(set(self._areas.values())))
        self._quick_hit_geometry_dirty = True

    def _detach_bridge_value_refresh(self, area: QAbstractScrollArea) -> None:
        bridge = self._quick_bridge
        if bridge is None:
            return
        callback = getattr(bridge, "schedule_refresh", None)
        if callback is None:
            return
        try:
            area.verticalScrollBar().valueChanged.disconnect(callback)
        except (RuntimeError, TypeError):
            pass
        try:
            area.horizontalScrollBar().valueChanged.disconnect(callback)
        except (RuntimeError, TypeError):
            pass

    def _can_move(self, area: QAbstractScrollArea, scroll_delta: float) -> bool:
        bar = area.verticalScrollBar()
        if bar.maximum() <= bar.minimum():
            return False
        position = self._scroller.effective_position(bar)
        if scroll_delta > 0.0:
            return position < float(bar.maximum())
        if scroll_delta < 0.0:
            return position > float(bar.minimum())
        return False

    @staticmethod
    def _parent_scroll_area(area: QAbstractScrollArea) -> QAbstractScrollArea | None:
        parent = area.parentWidget()
        while parent is not None:
            if isinstance(parent, QAbstractScrollArea):
                return parent
            parent = parent.parentWidget()
        return None

    def _scroll_owner(
        self,
        area: QAbstractScrollArea,
        scroll_delta: float,
    ) -> QAbstractScrollArea | None:
        current: QAbstractScrollArea | None = area
        while current is not None:
            if self._can_move(current, scroll_delta):
                return current
            current = self._parent_scroll_area(current)
        return None

    def _scroll_area_delta(
        self,
        area: QAbstractScrollArea,
        *,
        pixel_y: float,
        angle_y: float,
    ) -> bool:
        if pixel_y:
            scroll_delta = -float(pixel_y)
            owner = self._scroll_owner(area, scroll_delta)
            if owner is None:
                return False
            self._scroller.scroll_pixels(owner, scroll_delta)
            return True

        if not angle_y:
            return False
        notch_delta = -float(angle_y) / self._ANGLE_UNITS_PER_NOTCH
        owner = self._scroll_owner(area, notch_delta)
        if owner is None:
            return False
        self._scroller.add_wheel_delta(owner, notch_delta)
        return True

    def _refresh_quick_hit_geometry(self) -> None:
        root = self._root
        if root is None:
            self._quick_hit_rects = {}
            self._quick_hit_geometry_dirty = False
            return

        hit_rects: dict[int, tuple[QRect, QAbstractScrollArea]] = {}
        for area in set(self._areas.values()):
            try:
                if not area.isVisibleTo(root):
                    continue
                viewport = area.viewport()
                top_left = viewport.mapTo(root, QPoint(0, 0))
                rect = viewport.rect().translated(top_left)
                if rect.width() <= 0 or rect.height() <= 0:
                    continue
                hit_rects[id(area)] = (rect, area)
            except RuntimeError:
                continue
        self._quick_hit_rects = hit_rects
        self._quick_hit_geometry_dirty = False

    def _area_at_quick_position(self, x: float, y: float) -> QAbstractScrollArea | None:
        if self._root is None:
            return None
        if self._quick_hit_geometry_dirty:
            self._refresh_quick_hit_geometry()

        point = QPoint(round(float(x)), round(float(y)))
        best_area: QAbstractScrollArea | None = None
        best_area_pixels: int | None = None
        for rect, area in self._quick_hit_rects.values():
            if not rect.contains(point):
                continue
            pixels = max(1, rect.width() * rect.height())
            if best_area_pixels is None or pixels < best_area_pixels:
                best_area = area
                best_area_pixels = pixels
        return best_area

    def _geometry_changed(self, area: QAbstractScrollArea) -> None:
        self._quick_hit_geometry_dirty = True
        state = self._quick_state
        if state is not None:
            state.sync_area(area, refresh_geometry=True)

    def eventFilter(self, watched: QObject, event) -> bool:  # noqa: ANN001, N802
        event_type = event.type()
        area = self._areas.get(watched)
        if area is not None and event_type in self._GEOMETRY_EVENTS:
            self._geometry_changed(area)

        if watched is self._quick_owner and event_type in {
            QEvent.Type.Resize,
            QEvent.Type.Show,
            QEvent.Type.Expose,
            QEvent.Type.WindowStateChange,
        }:
            self._quick_hit_geometry_dirty = True

        if event_type != QEvent.Type.Wheel:
            return False
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            return False

        if not self._bridge_scroll_detached:
            self._adopt_quick_scroll_presentation()

        if watched is self._quick_owner:
            position = event.position()
            area = self._area_at_quick_position(position.x(), position.y())
        else:
            area = self._areas.get(watched)
        if area is None:
            return False

        return self._scroll_area_delta(
            area,
            pixel_y=float(event.pixelDelta().y()),
            angle_y=float(event.angleDelta().y()),
        )

    def cleanup(self) -> None:
        self._scroller._timer.stop()
        self._scroller._animations.clear()
        self._scroller.set_presentation_state(None)
        quick_owner = self._quick_owner
        self._quick_owner = None
        if quick_owner is not None:
            try:
                quick_owner.removeEventFilter(self)
            except RuntimeError:
                pass
        for watched in tuple(self._areas):
            try:
                watched.removeEventFilter(self)
            except RuntimeError:
                pass
        self._areas.clear()
        self._quick_hit_rects.clear()
        self._quick_hit_geometry_dirty = True
        state = self._quick_state
        self._quick_state = None
        if state is not None:
            state.clear()
        self._quick_bridge = None
        self._root = None