from __future__ import annotations

import time
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication, QFrame, QMainWindow, QWidget

from .visual_style import GlassBackdrop, VisualStyleController


_GLASS_NAMES = {"glassCard", "heroCard", "statusCard", "microCard"}
_ANIMATION_FRAME_MS = 8
_POINTER_SAMPLE_MS = 8

_NORMAL_ALPHA = 64.0
_HOVER_ALPHA = 88.0
_ACTIVE_ALPHA = 106.0

_HOVER_SECONDS = 0.07
_PRESS_SECONDS = 0.045
_RELEASE_SECONDS = 0.09


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
            self.current_alpha = alpha
            self.start_alpha = alpha
            self.target_alpha = alpha
            self.animating = False
            self.surface.set_interaction(scale=1.0, overlay_alpha=alpha)
            return
        self.start_alpha = self.current_alpha
        self.target_alpha = alpha
        self.started_at = time.monotonic()
        self.duration = max(0.001, float(duration))
        self.animating = True


class NekroCardInteractionController(QObject):
    """Responsive low-cost hover/press feedback without filtering every widget."""

    def __init__(self, window: QMainWindow, visual: VisualStyleController) -> None:
        super().__init__(window)
        self.window = window
        self.visual = visual
        self.states: dict[QFrame, _CardState] = {}
        self.hovered: QFrame | None = None
        self.pressed: QFrame | None = None
        self._button_down = False

        for frame in window.findChildren(QFrame):
            if frame.objectName() not in _GLASS_NAMES:
                continue
            surface = visual.surface_for(frame)
            if surface is not None:
                self.states[frame] = _CardState(frame=frame, surface=surface)

        self._sample_timer = QTimer(self)
        self._sample_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._sample_timer.setInterval(_POINTER_SAMPLE_MS)
        self._sample_timer.timeout.connect(self._sample_pointer)

        self._animation_timer = QTimer(self)
        self._animation_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._animation_timer.setInterval(_ANIMATION_FRAME_MS)
        self._animation_timer.timeout.connect(self._tick_animation)

        window.installEventFilter(self)
        window.destroyed.connect(self._cleanup)
        # This controller is installed immediately before shell.show(). Starting
        # on the first event-loop turn avoids relying on a particular layered
        # child-window Show/Enter ordering on Windows.
        QTimer.singleShot(0, self._start_sampling_if_visible)

    def _start_sampling_if_visible(self) -> None:
        if self.window.isVisible() and not self.window.isMinimized() and not self._sample_timer.isActive():
            self._sample_timer.start()
            self._sample_pointer()

    def _card_from_widget(self, widget: QWidget | None) -> QFrame | None:
        current = widget
        while current is not None:
            if isinstance(current, QFrame) and current in self.states:
                return current
            current = current.parentWidget()
        return None

    def _card_under_cursor(self) -> QFrame | None:
        point = QCursor.pos()
        local = self.window.mapFromGlobal(point)
        if not self.window.rect().contains(local):
            return None
        return self._card_from_widget(self.window.childAt(local))

    def _ensure_animation_timer(self) -> None:
        if any(state.animating for state in self.states.values()) and not self._animation_timer.isActive():
            self._animation_timer.start()

    def _animate(self, frame: QFrame, alpha: float, duration: float) -> None:
        state = self.states.get(frame)
        if state is None:
            return
        state.begin(alpha=alpha, duration=duration)
        self._ensure_animation_timer()

    def _set_hovered(self, frame: QFrame | None) -> None:
        if frame is self.hovered:
            return
        previous = self.hovered
        self.hovered = frame
        if previous is not None and previous is not self.pressed:
            self._animate(previous, _NORMAL_ALPHA, _RELEASE_SECONDS)
        if frame is not None and frame is not self.pressed:
            self._animate(frame, _HOVER_ALPHA, _HOVER_SECONDS)

    def _sample_pointer(self) -> None:
        card = self._card_under_cursor()
        down = bool(QApplication.mouseButtons() & Qt.MouseButton.LeftButton)

        self._set_hovered(card)
        if down and not self._button_down:
            self.pressed = card
            if card is not None:
                self._animate(card, _ACTIVE_ALPHA, _PRESS_SECONDS)
        elif not down and self._button_down:
            pressed = self.pressed
            self.pressed = None
            if pressed is not None:
                target = _HOVER_ALPHA if pressed is card else _NORMAL_ALPHA
                self._animate(pressed, target, _RELEASE_SECONDS)
        self._button_down = down

    def _tick_animation(self) -> None:
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
        if watched is not self.window:
            return False
        event_type = event.type()
        if event_type in {
            QEvent.Type.Show,
            QEvent.Type.Enter,
            QEvent.Type.WindowActivate,
        }:
            self._start_sampling_if_visible()
        elif event_type in {QEvent.Type.Hide, QEvent.Type.Leave}:
            self._sample_timer.stop()
            self._button_down = False
            self.pressed = None
            self._set_hovered(None)
        elif event_type == QEvent.Type.WindowStateChange:
            if self.window.isMinimized():
                self._sample_timer.stop()
            else:
                self._start_sampling_if_visible()
        return False

    def _cleanup(self) -> None:
        self._sample_timer.stop()
        self._animation_timer.stop()
        try:
            self.window.removeEventFilter(self)
        except RuntimeError:
            pass
        for state in self.states.values():
            state.surface.set_interaction(scale=1.0, overlay_alpha=_NORMAL_ALPHA)


def install_nekro_card_fx(
    window: QMainWindow,
    visual: VisualStyleController,
) -> NekroCardInteractionController:
    controller = NekroCardInteractionController(window, visual)
    window._nekro_card_fx = controller  # type: ignore[attr-defined]
    return controller
