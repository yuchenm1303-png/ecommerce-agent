from __future__ import annotations

import time
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QCursor, QMouseEvent
from PySide6.QtWidgets import QFrame, QMainWindow, QWidget

from .visual_style import GlassBackdrop, VisualStyleController


_GLASS_NAMES = {"glassCard", "heroCard", "statusCard", "microCard"}
_ANIMATION_FRAME_MS = 8

_NORMAL_ALPHA = 64.0
_HOVER_ALPHA = 90.0
_ACTIVE_ALPHA = 110.0

_HOVER_SECONDS = 0.065
_PRESS_SECONDS = 0.040
_RELEASE_SECONDS = 0.085


def _css_ease(progress: float) -> float:
    p = min(1.0, max(0.0, progress))
    x1, y1, x2, y2 = 0.25, 0.10, 0.25, 1.00

    def cubic(t: float, a: float, b: float) -> float:
        omt = 1.0 - t
        return 3.0 * omt * omt * t * a + 3.0 * omt * t * t * b + t * t * t

    lo, hi = 0.0, 1.0
    for _ in range(9):
        mid = (lo + hi) * 0.5
        if cubic(mid, x1, x2) < p:
            lo = mid
        else:
            hi = mid
    return cubic((lo + hi) * 0.5, y1, y2)


@dataclass(slots=True)
class _CardState:
    frame: QFrame
    surface: GlassBackdrop
    current_alpha: float = _NORMAL_ALPHA
    start_alpha: float = _NORMAL_ALPHA
    target_alpha: float = _NORMAL_ALPHA
    started_at: float = 0.0
    duration: float = _RELEASE_SECONDS
    animating: bool = False

    def begin(self, *, alpha: float, duration: float) -> None:
        alpha = float(alpha)
        if abs(alpha - self.current_alpha) < 0.1:
            self.snap(alpha)
            return
        self.start_alpha = self.current_alpha
        self.target_alpha = alpha
        self.started_at = time.monotonic()
        self.duration = max(0.001, float(duration))
        self.animating = True

    def snap(self, alpha: float) -> None:
        """Publish one interaction state immediately, without waiting for a timer tick."""

        alpha = float(alpha)
        self.current_alpha = alpha
        self.start_alpha = alpha
        self.target_alpha = alpha
        self.animating = False
        self.surface.set_interaction(scale=1.0, overlay_alpha=alpha)

    def freeze(self) -> None:
        # Preserve the exact alpha already on screen. Forcing hover/pressed
        # feedback to NORMAL here would itself create the flash the modal is
        # trying to eliminate.
        self.snap(self.current_alpha)


class NekroCardInteractionController(QObject):
    """Reliable card hover/press feedback without pointer polling.

    Input is observed across each card's entire QWidget subtree rather than only
    on the outer QFrame. Child labels, buttons and editors therefore cannot make
    hover/press feedback disappear. Events are never consumed: real child controls
    continue to own their normal behavior.

    Press feedback is committed synchronously so even a click shorter than the
    8 ms animation timer interval visibly reaches ACTIVE_ALPHA once. Hover and
    release keep the existing easing, colors and durations.
    """

    def __init__(self, window: QMainWindow, visual: VisualStyleController) -> None:
        super().__init__(window)
        self.window = window
        self.visual = visual
        self.states: dict[QFrame, _CardState] = {}
        self.hovered: QFrame | None = None
        self.pressed: QFrame | None = None
        self._suspended = False
        self._watched_to_card: dict[QObject, QFrame] = {}

        # Build every card state before assigning descendants. This lets nested
        # glass cards resolve to their nearest card instead of an outer ancestor.
        for frame in window.findChildren(QFrame):
            if frame.objectName() not in _GLASS_NAMES:
                continue
            surface = visual.surface_for(frame)
            if surface is None:
                continue
            self.states[frame] = _CardState(frame=frame, surface=surface)

        for frame in self.states:
            self._register_widget_tree(frame)

        self._animation_timer = QTimer(self)
        self._animation_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._animation_timer.setInterval(_ANIMATION_FRAME_MS)
        self._animation_timer.timeout.connect(self._tick_animation)

        window.destroyed.connect(self._cleanup)

    def _nearest_card(self, widget: QWidget) -> QFrame | None:
        current: QWidget | None = widget
        while current is not None:
            if isinstance(current, QFrame) and current in self.states:
                return current
            current = current.parentWidget()
        return None

    def _watch_widget(self, widget: QWidget, frame: QFrame) -> None:
        previous = self._watched_to_card.get(widget)
        if previous is frame:
            return
        if previous is not None:
            try:
                widget.removeEventFilter(self)
            except RuntimeError:
                pass
        widget.setMouseTracking(True)
        widget.installEventFilter(self)
        self._watched_to_card[widget] = frame

    def _register_widget_tree(self, root: QWidget) -> None:
        widgets = [root, *root.findChildren(QWidget)]
        for widget in widgets:
            frame = self._nearest_card(widget)
            if frame is not None:
                self._watch_widget(widget, frame)

    @staticmethod
    def _cursor_inside_card(frame: QFrame) -> bool:
        if not frame.isVisible() or not frame.isEnabled():
            return False
        try:
            local = frame.mapFromGlobal(QCursor.pos())
        except RuntimeError:
            return False
        return frame.rect().contains(local)

    def suspend_for_modal(self) -> None:
        if self._suspended:
            return
        self._suspended = True
        self._animation_timer.stop()
        self.hovered = None
        self.pressed = None
        for state in self.states.values():
            state.freeze()

    def resume_from_modal(self) -> None:
        if not self._suspended:
            return
        self._suspended = False
        for state in self.states.values():
            state.begin(alpha=_NORMAL_ALPHA, duration=_RELEASE_SECONDS)
        self._ensure_animation_timer()

    def _ensure_animation_timer(self) -> None:
        if self._suspended:
            return
        if any(state.animating for state in self.states.values()) and not self._animation_timer.isActive():
            self._animation_timer.start()

    def _animate(self, frame: QFrame, alpha: float, duration: float) -> None:
        if self._suspended:
            return
        state = self.states.get(frame)
        if state is None:
            return
        state.begin(alpha=alpha, duration=duration)
        self._ensure_animation_timer()

    def _snap(self, frame: QFrame, alpha: float) -> None:
        if self._suspended:
            return
        state = self.states.get(frame)
        if state is None:
            return
        state.snap(alpha)

    def _enter(self, frame: QFrame) -> None:
        if self.hovered is frame:
            return
        previous = self.hovered
        self.hovered = frame
        if previous is not None and previous is not self.pressed:
            self._animate(previous, _NORMAL_ALPHA, _RELEASE_SECONDS)
        if frame is not self.pressed:
            self._animate(frame, _HOVER_ALPHA, _HOVER_SECONDS)

    def _leave(self, frame: QFrame) -> None:
        if self.hovered is frame:
            self.hovered = None
        if frame is not self.pressed:
            self._animate(frame, _NORMAL_ALPHA, _RELEASE_SECONDS)

    def _press(self, frame: QFrame) -> None:
        if self.hovered is not frame:
            self._enter(frame)
        previous = self.pressed
        if previous is not None and previous is not frame:
            self._animate(previous, _NORMAL_ALPHA, _RELEASE_SECONDS)
        self.pressed = frame

        # A fast click can complete before the first 8 ms animation tick. Commit
        # ACTIVE_ALPHA synchronously so every physical press produces one visible
        # dark frame, then let release use the normal easing back to hover/normal.
        self._snap(frame, _ACTIVE_ALPHA)

    def _release(self, frame: QFrame, *, inside: bool) -> None:
        if self.pressed is frame:
            self.pressed = None
        elif self.pressed is not None:
            previous = self.pressed
            self.pressed = None
            self._animate(previous, _NORMAL_ALPHA, _RELEASE_SECONDS)

        if inside:
            if self.hovered is not frame:
                self._enter(frame)
            target = _HOVER_ALPHA
        else:
            if self.hovered is frame:
                self.hovered = None
            target = _NORMAL_ALPHA
        self._animate(frame, target, _RELEASE_SECONDS)

    def _reset_card(self, frame: QFrame) -> None:
        if self.hovered is frame:
            self.hovered = None
        if self.pressed is frame:
            self.pressed = None
        self._animate(frame, _NORMAL_ALPHA, _RELEASE_SECONDS)

    def _tick_animation(self) -> None:
        if self._suspended:
            self._animation_timer.stop()
            return

        now = time.monotonic()
        any_animating = False
        for state in self.states.values():
            if not state.animating:
                continue
            raw = (now - state.started_at) / state.duration
            if raw >= 1.0:
                state.current_alpha = state.target_alpha
                state.animating = False
            else:
                eased = _css_ease(raw)
                state.current_alpha = state.start_alpha + (
                    state.target_alpha - state.start_alpha
                ) * eased
                any_animating = True
            state.surface.set_interaction(scale=1.0, overlay_alpha=state.current_alpha)

        if not any_animating and not any(state.animating for state in self.states.values()):
            self._animation_timer.stop()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        frame = self._watched_to_card.get(watched)
        if frame is None:
            return False

        event_type = event.type()

        # Cards may gain small helper widgets after startup. Register them as they
        # appear so the interaction surface stays complete without polling.
        if event_type == QEvent.Type.ChildAdded:
            child_getter = getattr(event, "child", None)
            child = child_getter() if callable(child_getter) else None
            if isinstance(child, QWidget):
                self._register_widget_tree(child)
            return False

        if self._suspended:
            return False

        if event_type in {QEvent.Type.Enter, QEvent.Type.MouseMove}:
            self._enter(frame)
        elif event_type == QEvent.Type.Leave:
            # Child-to-child transitions generate Leave events too. Only release
            # the card hover when the pointer has actually left the whole card.
            if not self._cursor_inside_card(frame):
                self._leave(frame)
        elif event_type == QEvent.Type.MouseButtonPress and isinstance(event, QMouseEvent):
            if event.button() == Qt.MouseButton.LeftButton:
                self._press(frame)
        elif event_type == QEvent.Type.MouseButtonRelease and isinstance(event, QMouseEvent):
            if event.button() == Qt.MouseButton.LeftButton:
                self._release(frame, inside=self._cursor_inside_card(frame))
        elif watched is frame and event_type in {QEvent.Type.Hide, QEvent.Type.EnabledChange}:
            if not frame.isVisible() or not frame.isEnabled():
                self._reset_card(frame)
        return False

    def _cleanup(self) -> None:
        self._animation_timer.stop()
        self._suspended = False
        for watched in tuple(self._watched_to_card):
            try:
                watched.removeEventFilter(self)
            except RuntimeError:
                pass
        self._watched_to_card.clear()

        for state in tuple(self.states.values()):
            try:
                state.surface.set_interaction(scale=1.0, overlay_alpha=_NORMAL_ALPHA)
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
