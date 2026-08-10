from __future__ import annotations

import time
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import QFrame, QMainWindow

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

    def freeze(self) -> None:
        # Preserve the exact alpha already on screen. Forcing hover/pressed
        # feedback to NORMAL here would itself create the flash the modal is
        # trying to eliminate.
        self.start_alpha = self.current_alpha
        self.target_alpha = self.current_alpha
        self.animating = False
        self.surface.set_interaction(scale=1.0, overlay_alpha=self.current_alpha)


class NekroCardInteractionController(QObject):
    """Immediate card hover/press feedback with no pointer polling loop.

    Modal suspension freezes every card at the alpha already presented on screen
    and stops the animation timer. Nothing changes while the modal is active.
    After the modal is fully gone, cards ease back to neutral with the existing
    release curve instead of jumping between interaction states.
    """

    def __init__(self, window: QMainWindow, visual: VisualStyleController) -> None:
        super().__init__(window)
        self.window = window
        self.visual = visual
        self.states: dict[QFrame, _CardState] = {}
        self.hovered: QFrame | None = None
        self.pressed: QFrame | None = None
        self._suspended = False

        for frame in window.findChildren(QFrame):
            if frame.objectName() not in _GLASS_NAMES:
                continue
            surface = visual.surface_for(frame)
            if surface is None:
                continue
            self.states[frame] = _CardState(frame=frame, surface=surface)
            frame.setMouseTracking(True)
            frame.installEventFilter(self)

        self._animation_timer = QTimer(self)
        self._animation_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._animation_timer.setInterval(_ANIMATION_FRAME_MS)
        self._animation_timer.timeout.connect(self._tick_animation)

        window.destroyed.connect(self._cleanup)

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
        self.pressed = frame
        self._animate(frame, _ACTIVE_ALPHA, _PRESS_SECONDS)

    def _release(self, frame: QFrame) -> None:
        if self.pressed is frame:
            self.pressed = None
        target = _HOVER_ALPHA if self.hovered is frame else _NORMAL_ALPHA
        self._animate(frame, target, _RELEASE_SECONDS)

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
        if not isinstance(watched, QFrame) or watched not in self.states:
            return False
        if self._suspended:
            return False

        event_type = event.type()
        if event_type == QEvent.Type.Enter:
            self._enter(watched)
        elif event_type == QEvent.Type.Leave:
            self._leave(watched)
        elif event_type == QEvent.Type.MouseButtonPress:
            self._press(watched)
        elif event_type == QEvent.Type.MouseButtonRelease:
            self._release(watched)
        elif event_type in {QEvent.Type.Hide, QEvent.Type.EnabledChange} and not watched.isVisible():
            if self.hovered is watched:
                self.hovered = None
            if self.pressed is watched:
                self.pressed = None
            self._animate(watched, _NORMAL_ALPHA, _RELEASE_SECONDS)
        return False

    def _cleanup(self) -> None:
        self._animation_timer.stop()
        self._suspended = False
        for frame, state in tuple(self.states.items()):
            try:
                frame.removeEventFilter(self)
            except RuntimeError:
                pass
            try:
                state.surface.set_interaction(scale=1.0, overlay_alpha=_NORMAL_ALPHA)
            except RuntimeError:
                pass
        self.states.clear()


def install_nekro_card_fx(
    window: QMainWindow,
    visual: VisualStyleController,
) -> NekroCardInteractionController:
    controller = NekroCardInteractionController(window, visual)
    window._nekro_card_fx = controller  # type: ignore[attr-defined]
    return controller
