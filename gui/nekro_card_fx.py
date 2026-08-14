from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QEasingCurve, QObject, QPoint, QPointF, QRectF, Qt
from PySide6.QtWidgets import QFrame, QMainWindow, QWidget


_GLASS_NAMES = {"glassCard", "heroCard", "statusCard", "microCard"}

_NORMAL_SCALE = 1.00
_HOVER_SCALE = 1.02
_ACTIVE_SCALE = 1.00
_NORMAL_ALPHA = 64.0
_HOVER_ALPHA = 102.0
_ACTIVE_ALPHA = 102.0
_TRANSITION_MS = 300

_REFERENCE_CARD_SPAN_PX = 300.0
_REFERENCE_EDGE_GROWTH_PX = (
    _REFERENCE_CARD_SPAN_PX * (_HOVER_SCALE - _NORMAL_SCALE) * 0.5
)
_MIN_NEIGHBOR_GAP_PX = 1.0
_WINDOW_EDGE_GAP_PX = 1.0
_HOVER_NONE_GRACE_SAMPLES = 1
_MAX_MOTION_HZ = 90.0
_MAX_CONCURRENT_MOTIONS = 2


def _css_ease() -> QEasingCurve:
    curve = QEasingCurve(QEasingCurve.Type.BezierSpline)
    curve.addCubicBezierSegment(
        QPointF(0.25, 0.10),
        QPointF(0.25, 1.00),
        QPointF(1.00, 1.00),
    )
    return curve


@dataclass(slots=True)
class _CardState:
    frame: QFrame
    surface: Any
    current_scale: float = _NORMAL_SCALE
    current_alpha: float = _NORMAL_ALPHA
    from_scale: float = _NORMAL_SCALE
    from_alpha: float = _NORMAL_ALPHA
    target_scale: float = _NORMAL_SCALE
    target_alpha: float = _NORMAL_ALPHA
    started_s: float = 0.0
    moving: bool = False

    def snap(self, scale: float, alpha: float) -> None:
        scale = float(scale)
        alpha = float(alpha)
        self.current_scale = scale
        self.current_alpha = alpha
        self.from_scale = scale
        self.from_alpha = alpha
        self.target_scale = scale
        self.target_alpha = alpha
        self.moving = False
        self.surface.set_interaction(scale=scale, overlay_alpha=alpha)


class NekroCardInteractionController(QObject):
    """Card interaction with one shared input clock and bounded raster work.

    Quick renders the glass shell every presentation step. QWidget card content is
    captured once when an interaction transition starts and that frozen composite
    is transformed throughout the 300 ms motion. At the endpoint the real content
    is thawed again, so inputs remain fully live while steady but the expensive
    QWidget subtree is not re-rasterized dozens of times during one scale tween.
    """

    def __init__(self, window: QMainWindow, visual: Any) -> None:
        super().__init__(window)
        self.window = window
        self.visual = visual
        self.states: dict[QFrame, _CardState] = {}
        self._moving_frames: set[QFrame] = set()
        self._hover_scale_cache: dict[QFrame, float] = {}
        self._hover_scale_cache_key: tuple[int, int, int, int] | None = None
        self._none_samples = 0
        self.hovered: QFrame | None = None
        self.pressed: QFrame | None = None
        self._suspended = False
        self._left_down = False
        self._ease = _css_ease()
        self._motion_interval_s = self._frame_interval_ms() / 1000.0
        self._next_motion_s = 0.0

        for frame in window.findChildren(QFrame):
            if frame.objectName() not in _GLASS_NAMES:
                continue
            surface = visual.surface_for(frame)
            if surface is not None:
                self.states[frame] = _CardState(frame=frame, surface=surface)

        window.destroyed.connect(self._cleanup)

    def _frame_interval_ms(self) -> int:
        refresh_hz = 60.0
        screen = self.window.screen()
        if screen is not None:
            try:
                candidate = float(screen.refreshRate())
                if 30.0 <= candidate <= 500.0:
                    refresh_hz = candidate
            except (RuntimeError, TypeError, ValueError):
                pass
        target_hz = max(60.0, min(_MAX_MOTION_HZ, refresh_hz))
        return max(4, int(1000.0 / target_hz))

    @staticmethod
    def _set_content_frozen(state: _CardState | None, frozen: bool) -> None:
        if state is None:
            return
        setter = getattr(state.surface, "set_content_frozen", None)
        if callable(setter):
            try:
                setter(bool(frozen))
            except RuntimeError:
                pass

    def _recapture_for_motion(self, state: _CardState) -> None:
        # Switching False -> True clears the previous frozen source so the next
        # effect draw captures the latest hover/press/focus pixels exactly once.
        self._set_content_frozen(state, False)
        self._set_content_frozen(state, True)

    def _retire_stale_motions(self) -> None:
        while len(self._moving_frames) > _MAX_CONCURRENT_MOTIONS:
            protected = {frame for frame in (self.hovered, self.pressed) if frame is not None}
            candidates = [frame for frame in self._moving_frames if frame not in protected]
            if not candidates:
                break
            stale = min(candidates, key=lambda frame: self.states[frame].started_s)
            state = self.states.get(stale)
            if state is not None:
                try:
                    state.snap(_NORMAL_SCALE, _NORMAL_ALPHA)
                except RuntimeError:
                    state.moving = False
                self._set_content_frozen(state, False)
            self._moving_frames.discard(stale)

    def _card_rect_in_window(self, frame: QFrame) -> QRectF | None:
        try:
            top_left = frame.mapTo(self.window, frame.rect().topLeft())
        except RuntimeError:
            return None
        return QRectF(
            float(top_left.x()),
            float(top_left.y()),
            float(frame.width()),
            float(frame.height()),
        )

    def _geometry_cache_key(self) -> tuple[int, int, int, int]:
        background = getattr(self.visual, "background", None)
        revision = int(getattr(background, "_geometry_revision", -1))
        return (
            revision,
            int(self.window.width()),
            int(self.window.height()),
            len(self.states),
        )

    def _available_edge_growth(
        self,
        frame: QFrame,
        desired_growth: float,
        rects: dict[QFrame, QRectF],
    ) -> float:
        rect = rects.get(frame)
        if rect is None or rect.isEmpty():
            return 0.0

        edge_cap = min(
            max(0.0, rect.left() - _WINDOW_EDGE_GAP_PX),
            max(0.0, rect.top() - _WINDOW_EDGE_GAP_PX),
            max(0.0, float(self.window.width()) - rect.right() - _WINDOW_EDGE_GAP_PX),
            max(0.0, float(self.window.height()) - rect.bottom() - _WINDOW_EDGE_GAP_PX),
        )
        allowed = min(max(0.0, desired_growth), edge_cap)
        if allowed <= 0.0:
            return 0.0

        for other, other_rect in rects.items():
            if other is frame or frame.isAncestorOf(other) or other.isAncestorOf(frame):
                continue
            horizontal_overlap = min(rect.right(), other_rect.right()) - max(
                rect.left(), other_rect.left()
            )
            vertical_overlap = min(rect.bottom(), other_rect.bottom()) - max(
                rect.top(), other_rect.top()
            )
            if horizontal_overlap > 0.0:
                if other_rect.top() >= rect.bottom():
                    allowed = min(
                        allowed,
                        max(0.0, other_rect.top() - rect.bottom() - _MIN_NEIGHBOR_GAP_PX),
                    )
                elif rect.top() >= other_rect.bottom():
                    allowed = min(
                        allowed,
                        max(0.0, rect.top() - other_rect.bottom() - _MIN_NEIGHBOR_GAP_PX),
                    )
            if vertical_overlap > 0.0:
                if other_rect.left() >= rect.right():
                    allowed = min(
                        allowed,
                        max(0.0, other_rect.left() - rect.right() - _MIN_NEIGHBOR_GAP_PX),
                    )
                elif rect.left() >= other_rect.right():
                    allowed = min(
                        allowed,
                        max(0.0, rect.left() - other_rect.right() - _MIN_NEIGHBOR_GAP_PX),
                    )
            if allowed <= 0.0:
                return 0.0
        return allowed

    def _rebuild_hover_scale_cache(self) -> None:
        rects: dict[QFrame, QRectF] = {}
        for frame in self.states:
            if not frame.isVisibleTo(self.window) or not frame.isEnabled():
                continue
            rect = self._card_rect_in_window(frame)
            if rect is not None and not rect.isEmpty():
                rects[frame] = rect

        cache: dict[QFrame, float] = {}
        for frame, rect in rects.items():
            span = max(1.0, rect.width(), rect.height())
            reference_growth = min(
                _REFERENCE_EDGE_GROWTH_PX,
                span * (_HOVER_SCALE - _NORMAL_SCALE) * 0.5,
            )
            growth = self._available_edge_growth(frame, reference_growth, rects)
            cache[frame] = max(
                _NORMAL_SCALE,
                min(_HOVER_SCALE, _NORMAL_SCALE + (2.0 * growth / span)),
            )
        self._hover_scale_cache = cache
        self._hover_scale_cache_key = self._geometry_cache_key()

    def _hover_scale_for(self, frame: QFrame) -> float:
        key = self._geometry_cache_key()
        if key != self._hover_scale_cache_key:
            self._rebuild_hover_scale_cache()
        return self._hover_scale_cache.get(frame, _NORMAL_SCALE)

    def _nearest_card(self, widget: QWidget | None) -> QFrame | None:
        current = widget
        while current is not None:
            if isinstance(current, QFrame) and current in self.states:
                return current
            if current is self.window:
                break
            current = current.parentWidget()
        return None

    def _card_at_global(self, global_pos: QPoint) -> QFrame | None:
        try:
            local = self.window.mapFromGlobal(global_pos)
        except RuntimeError:
            return None
        if not self.window.rect().contains(local):
            return None

        widget = self.window.childAt(local)
        card = self._nearest_card(widget)
        if card is not None and card.isVisibleTo(self.window) and card.isEnabled():
            return card
        if widget is not None:
            return None

        best: QFrame | None = None
        best_depth = -1
        for frame in self.states:
            if not frame.isVisibleTo(self.window) or not frame.isEnabled():
                continue
            try:
                point = frame.mapFrom(self.window, local)
            except RuntimeError:
                continue
            if not frame.rect().contains(point):
                continue
            depth = 0
            parent = frame.parentWidget()
            while parent is not None:
                depth += 1
                if parent is self.window:
                    break
                parent = parent.parentWidget()
            if depth > best_depth:
                best = frame
                best_depth = depth
        return best

    def _hover_still_owns_global(self, frame: QFrame, global_pos: QPoint) -> bool:
        if not frame.isVisibleTo(self.window) or not frame.isEnabled():
            return False
        try:
            local = frame.mapFromGlobal(global_pos)
        except RuntimeError:
            return False
        if not frame.rect().contains(local):
            return False
        nested = self._nearest_card(frame.childAt(local))
        return nested is None or nested is frame

    def _advance_state(self, state: _CardState, now_s: float) -> bool:
        if not state.moving:
            return False
        elapsed_s = max(0.0, now_s - state.started_s)
        linear = min(1.0, elapsed_s / (_TRANSITION_MS / 1000.0))
        eased = float(self._ease.valueForProgress(linear))
        if linear >= 1.0:
            state.current_scale = state.target_scale
            state.current_alpha = state.target_alpha
            state.moving = False
        else:
            state.current_scale = state.from_scale + (state.target_scale - state.from_scale) * eased
            state.current_alpha = state.from_alpha + (state.target_alpha - state.from_alpha) * eased
        state.surface.set_interaction(
            scale=state.current_scale,
            overlay_alpha=state.current_alpha,
        )
        if not state.moving:
            self._set_content_frozen(state, False)
        return state.moving

    def _advance_motions(self, now_s: float) -> None:
        if self._suspended:
            return
        for frame in tuple(self._moving_frames):
            state = self.states.get(frame)
            if state is None:
                self._moving_frames.discard(frame)
                continue
            try:
                if not self._advance_state(state, now_s):
                    self._moving_frames.discard(frame)
            except RuntimeError:
                state.moving = False
                self._set_content_frozen(state, False)
                self._moving_frames.discard(frame)

    def _animate_to(self, frame: QFrame | None, *, scale: float, alpha: float) -> None:
        if self._suspended or frame is None:
            return
        state = self.states.get(frame)
        if state is None:
            return

        now_s = time.perf_counter()
        if state.moving:
            self._advance_state(state, now_s)
        scale = float(scale)
        alpha = float(alpha)
        if (
            abs(scale - state.target_scale) <= 1e-5
            and abs(alpha - state.target_alpha) <= 0.05
        ):
            return
        if (
            abs(scale - state.current_scale) <= 1e-5
            and abs(alpha - state.current_alpha) <= 0.05
        ):
            state.snap(scale, alpha)
            self._set_content_frozen(state, False)
            self._moving_frames.discard(frame)
            return

        state.from_scale = state.current_scale
        state.from_alpha = state.current_alpha
        state.target_scale = scale
        state.target_alpha = alpha
        state.started_s = now_s
        state.moving = True
        self._recapture_for_motion(state)
        self._moving_frames.add(frame)
        self._retire_stale_motions()
        self._next_motion_s = min(self._next_motion_s or now_s, now_s)

    def _normal(self, frame: QFrame | None) -> None:
        self._animate_to(frame, scale=_NORMAL_SCALE, alpha=_NORMAL_ALPHA)

    def _hover(self, frame: QFrame | None) -> None:
        if frame is not None:
            self._animate_to(
                frame,
                scale=self._hover_scale_for(frame),
                alpha=_HOVER_ALPHA,
            )

    def _active(self, frame: QFrame | None) -> None:
        self._animate_to(frame, scale=_ACTIVE_SCALE, alpha=_ACTIVE_ALPHA)

    def _set_hover(self, frame: QFrame | None) -> None:
        if self._suspended or self.pressed is not None:
            return
        previous = self.hovered
        if previous is frame:
            return
        self.hovered = frame
        if previous is not None:
            self._normal(previous)
        if frame is not None:
            self._hover(frame)

    def _begin_press(self, frame: QFrame | None) -> None:
        if self._suspended:
            return
        self._none_samples = 0
        if frame is None:
            previous = self.hovered
            self.hovered = None
            self.pressed = None
            if previous is not None:
                self._normal(previous)
            return
        previous_hover = self.hovered
        self.hovered = frame
        self.pressed = frame
        if previous_hover is not None and previous_hover is not frame:
            self._normal(previous_hover)
        self._active(frame)

    def _end_press(self, global_pos: QPoint) -> None:
        if self._suspended:
            return
        self._none_samples = 0
        previous = self.pressed
        self.pressed = None
        current = self._card_at_global(global_pos)
        self.hovered = current
        if previous is not None:
            if previous is current:
                self._hover(previous)
            else:
                self._normal(previous)
        if current is not None and current is not previous:
            self._hover(current)

    def _sample_input(self, global_pos: QPoint, *, left_down: bool) -> None:
        if self._suspended or not self.window.isVisible() or self.window.isMinimized():
            return
        if left_down and self._left_down and self.pressed is not None:
            return
        if not left_down and self._left_down:
            self._left_down = False
            self._end_press(global_pos)
            return

        if (
            not left_down
            and self.hovered is not None
            and self.pressed is None
            and self._hover_still_owns_global(self.hovered, global_pos)
        ):
            self._none_samples = 0
            return

        current = self._card_at_global(global_pos)
        if left_down and not self._left_down:
            self._left_down = True
            self._begin_press(current)
            return
        if left_down:
            if self.pressed is None:
                self._begin_press(current)
            return

        if current is None and self.hovered is not None:
            self._none_samples += 1
            if self._none_samples <= _HOVER_NONE_GRACE_SAMPLES:
                return
            self._none_samples = 0
        else:
            self._none_samples = 0
        self._set_hover(current)

    def presentation_tick(
        self,
        global_pos: QPoint,
        *,
        left_down: bool,
        now_s: float,
        input_changed: bool,
    ) -> None:
        if self._suspended:
            return
        if input_changed:
            self._sample_input(global_pos, left_down=left_down)
        if self._moving_frames and now_s >= self._next_motion_s:
            self._advance_motions(now_s)
            self._next_motion_s = now_s + self._motion_interval_s

    def suspend_for_modal(self) -> None:
        if self._suspended:
            return
        self._suspended = True
        self._moving_frames.clear()
        self._none_samples = 0
        self._left_down = False
        self.hovered = None
        self.pressed = None
        for state in self.states.values():
            try:
                self._set_content_frozen(state, False)
                state.snap(_NORMAL_SCALE, _NORMAL_ALPHA)
            except RuntimeError:
                state.moving = False

    def resume_from_modal(self) -> None:
        if not self._suspended:
            return
        self._suspended = False
        self._moving_frames.clear()
        self._hover_scale_cache.clear()
        self._hover_scale_cache_key = None
        self._none_samples = 0
        self._left_down = False
        self.hovered = None
        self.pressed = None
        self._next_motion_s = 0.0
        for state in self.states.values():
            try:
                self._set_content_frozen(state, False)
                state.snap(_NORMAL_SCALE, _NORMAL_ALPHA)
            except RuntimeError:
                state.moving = False

    def _cleanup(self) -> None:
        self._moving_frames.clear()
        self._hover_scale_cache.clear()
        self._hover_scale_cache_key = None
        for state in tuple(self.states.values()):
            try:
                self._set_content_frozen(state, False)
                state.snap(_NORMAL_SCALE, _NORMAL_ALPHA)
            except RuntimeError:
                pass
        self.states.clear()
        self.hovered = None
        self.pressed = None


def install_nekro_card_fx(window: QMainWindow, visual: Any) -> NekroCardInteractionController:
    controller = NekroCardInteractionController(window, visual)
    window._nekro_card_fx = controller  # type: ignore[attr-defined]
    return controller


__all__ = ["NekroCardInteractionController", "install_nekro_card_fx"]
