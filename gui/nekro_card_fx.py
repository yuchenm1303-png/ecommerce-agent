from __future__ import annotations

import time
from dataclasses import dataclass

from PySide6.QtCore import QEasingCurve, QObject, QPointF, Qt, QTimer
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication, QFrame, QMainWindow, QWidget

from .visual_style import GlassBackdrop, VisualStyleController


_GLASS_NAMES = {"glassCard", "heroCard", "statusCard", "microCard"}

# Exact reference values from `.links .link-all .item.cards`:
# normal inherits `.cards` background #00000040 and scale(1);
# hover overrides to rgba(0, 0, 0, .4) and scale(1.02);
# active returns transform to scale(1) while the hover background remains active.
_NORMAL_SCALE = 1.00
_HOVER_SCALE = 1.02
_ACTIVE_SCALE = 1.00
_NORMAL_ALPHA = 64.0
_HOVER_ALPHA = 102.0
_ACTIVE_ALPHA = 102.0
_TRANSITION_MS = 300

# The reference link cards are roughly 300 px on their long edge. A centered
# scale(1.02) moves that edge about 3 px outward. Preserve that visible lift in
# pixels instead of applying a raw 2% expansion to very large desktop cards.
_REFERENCE_CARD_SPAN_PX = 300.0
_REFERENCE_EDGE_GROWTH_PX = (
    _REFERENCE_CARD_SPAN_PX * (_HOVER_SCALE - _NORMAL_SCALE) * 0.5
)

# Pointer ownership remains one cheap local sampler. Motion presentation gets its
# own refresh-aware timer so a 165 Hz display is not limited by the hit-test rate.
_POINTER_SAMPLE_MS = 8


def _css_ease() -> QEasingCurve:
    """CSS default `ease`: cubic-bezier(.25, .1, .25, 1)."""

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
    surface: GlassBackdrop
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
    """Reference-website interaction for every registered glass card.

    The website-list cards use one continuous 300 ms CSS transition. Their
    original 1.02 hover scale is retained as the reference maximum, while larger
    application cards normalize the same transform so the visible edge lift stays
    near the reference site's ~3 px instead of growing with the card dimensions.

    Target changes never restart from a canned state. A press/release/leave samples
    the current interpolated value and reverses from there, matching browser CSS
    transform behavior. Business clicks remain owned by the real child widgets.
    """

    def __init__(self, window: QMainWindow, visual: VisualStyleController) -> None:
        super().__init__(window)
        self.window = window
        self.visual = visual
        self.states: dict[QFrame, _CardState] = {}
        self.hovered: QFrame | None = None
        self.pressed: QFrame | None = None
        self._suspended = False
        self._left_down = bool(QApplication.mouseButtons() & Qt.MouseButton.LeftButton)
        self._ease = _css_ease()

        for frame in window.findChildren(QFrame):
            if frame.objectName() not in _GLASS_NAMES:
                continue
            surface = visual.surface_for(frame)
            if surface is None:
                continue
            self.states[frame] = _CardState(frame=frame, surface=surface)

        self._motion_timer = QTimer(self)
        self._motion_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._motion_timer.setInterval(self._frame_interval_ms())
        self._motion_timer.timeout.connect(self._advance_motions)

        self._pointer_timer = QTimer(self)
        self._pointer_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._pointer_timer.setInterval(_POINTER_SAMPLE_MS)
        self._pointer_timer.timeout.connect(self._sample_pointer)
        self._pointer_timer.start()

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
        target_hz = max(60.0, min(240.0, refresh_hz))
        return max(4, int(1000.0 / target_hz))

    @staticmethod
    def _hover_scale_for(frame: QFrame) -> float:
        """Keep hover edge displacement visually stable across card sizes."""

        span = max(1.0, float(frame.width()), float(frame.height()))
        normalized = _NORMAL_SCALE + (2.0 * _REFERENCE_EDGE_GROWTH_PX / span)
        return max(_NORMAL_SCALE, min(_HOVER_SCALE, normalized))

    def _nearest_card(self, widget: QWidget | None) -> QFrame | None:
        current = widget
        while current is not None:
            if isinstance(current, QFrame) and current in self.states:
                return current
            if current is self.window:
                break
            current = current.parentWidget()
        return None

    def _card_at_global(self, global_pos) -> QFrame | None:  # noqa: ANN001
        """Resolve the deepest visible glass card under one global point locally."""

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

        # childAt can return None through a transparent/native gap. The fallback
        # remains local to this one window and is only used for that unusual case.
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

    def _advance_state(self, state: _CardState, now_s: float) -> bool:
        if not state.moving:
            return False
        elapsed_s = max(0.0, now_s - state.started_s)
        linear = min(1.0, elapsed_s / (_TRANSITION_MS / 1000.0))
        eased = float(self._ease.valueForProgress(linear))
        state.current_scale = state.from_scale + (state.target_scale - state.from_scale) * eased
        state.current_alpha = state.from_alpha + (state.target_alpha - state.from_alpha) * eased
        state.surface.set_interaction(
            scale=state.current_scale,
            overlay_alpha=state.current_alpha,
        )
        if linear >= 1.0:
            state.current_scale = state.target_scale
            state.current_alpha = state.target_alpha
            state.moving = False
            state.surface.set_interaction(
                scale=state.current_scale,
                overlay_alpha=state.current_alpha,
            )
        return state.moving

    def _advance_motions(self) -> None:
        if self._suspended:
            self._motion_timer.stop()
            return
        now_s = time.perf_counter()
        any_moving = False
        for state in self.states.values():
            try:
                any_moving = self._advance_state(state, now_s) or any_moving
            except RuntimeError:
                state.moving = False
        if not any_moving:
            self._motion_timer.stop()

    def _animate_to(self, frame: QFrame | None, *, scale: float, alpha: float) -> None:
        if self._suspended or frame is None:
            return
        state = self.states.get(frame)
        if state is None:
            return

        now_s = time.perf_counter()
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
            return

        state.from_scale = state.current_scale
        state.from_alpha = state.current_alpha
        state.target_scale = scale
        state.target_alpha = alpha
        state.started_s = now_s
        state.moving = True
        if not self._motion_timer.isActive():
            self._motion_timer.setInterval(self._frame_interval_ms())
            self._motion_timer.start()

    def _normal(self, frame: QFrame | None) -> None:
        self._animate_to(frame, scale=_NORMAL_SCALE, alpha=_NORMAL_ALPHA)

    def _hover(self, frame: QFrame | None) -> None:
        if frame is None:
            return
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
        if frame is None:
            previous = self.hovered
            self.hovered = None
            self.pressed = None
            if previous is not None:
                self._normal(previous)
            return

        previous_hover = self.hovered
        if previous_hover is not None and previous_hover is not frame:
            self._normal(previous_hover)

        self.hovered = frame
        self.pressed = frame
        self._active(frame)

    def _end_press(self) -> None:
        if self._suspended:
            return
        previous = self.pressed
        self.pressed = None
        current = self._card_at_global(QCursor.pos())
        self.hovered = current

        if previous is not None:
            if previous is current:
                self._hover(previous)
            else:
                self._normal(previous)
        if current is not None and current is not previous:
            self._hover(current)

    def _sample_pointer(self) -> None:
        if self._suspended or not self.window.isVisible() or self.window.isMinimized():
            return

        current = self._card_at_global(QCursor.pos())
        left_down = bool(QApplication.mouseButtons() & Qt.MouseButton.LeftButton)

        if left_down and not self._left_down:
            self._left_down = True
            self._begin_press(current)
            return

        if not left_down and self._left_down:
            self._left_down = False
            self._end_press()
            return

        if left_down:
            if self.pressed is None:
                self._begin_press(current)
            return

        self._set_hover(current)

    def suspend_for_modal(self) -> None:
        if self._suspended:
            return
        self._suspended = True
        self._pointer_timer.stop()
        self._motion_timer.stop()
        self._left_down = False
        self.hovered = None
        self.pressed = None
        for state in self.states.values():
            try:
                state.snap(_NORMAL_SCALE, _NORMAL_ALPHA)
            except RuntimeError:
                state.moving = False

    def resume_from_modal(self) -> None:
        if not self._suspended:
            return
        self._suspended = False
        self._left_down = bool(QApplication.mouseButtons() & Qt.MouseButton.LeftButton)
        self.hovered = None
        self.pressed = None

        for state in self.states.values():
            try:
                state.snap(_NORMAL_SCALE, _NORMAL_ALPHA)
            except RuntimeError:
                state.moving = False

        current = self._card_at_global(QCursor.pos())
        self.hovered = current
        if current is not None:
            if self._left_down:
                self.pressed = current
                self._active(current)
            else:
                self._hover(current)
        self._pointer_timer.start()

    def _cleanup(self) -> None:
        self._pointer_timer.stop()
        self._motion_timer.stop()
        self._suspended = False
        for state in tuple(self.states.values()):
            try:
                state.snap(_NORMAL_SCALE, _NORMAL_ALPHA)
            except RuntimeError:
                pass
        self.states.clear()
        self.hovered = None
        self.pressed = None


def install_nekro_card_fx(
    window: QMainWindow,
    visual: VisualStyleController,
) -> NekroCardInteractionController:
    controller = NekroCardInteractionController(window, visual)
    window._nekro_card_fx = controller  # type: ignore[attr-defined]
    return controller
