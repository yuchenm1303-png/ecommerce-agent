from __future__ import annotations

import time
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QFrame, QMainWindow, QWidget

from .visual_style import GlassBackdrop, VisualStyleController


# Native-Qt interaction equivalent of the source card feel.
# We intentionally animate only the cached glass surface, never the whole card
# subtree. This keeps tables, logs, layouts and input widgets out of every
# animation frame and avoids the full-window hitch caused by QGraphicsEffect.
_GLASS_NAMES = {"glassCard", "heroCard", "statusCard", "microCard"}
_FRAME_MS = 16

_NORMAL_SCALE = 1.00
_NORMAL_ALPHA = 64.0
_HOVER_SCALE = 1.00
_HOVER_ALPHA = 102.0   # source link-card hover: rgb(0 0 0 / 40%)
_ACTIVE_SCALE = 0.98   # source .cards active scale
_ACTIVE_ALPHA = 78.0

_HOVER_SECONDS = 0.24
_PRESS_SECONDS = 0.12
_RELEASE_SECONDS = 0.16


def _css_ease(progress: float) -> float:
    """CSS default ease cubic-bezier(.25,.1,.25,1)."""

    p = min(1.0, max(0.0, progress))
    x1, y1, x2, y2 = 0.25, 0.10, 0.25, 1.00

    def cubic(t: float, a: float, b: float) -> float:
        omt = 1.0 - t
        return 3.0 * omt * omt * t * a + 3.0 * omt * t * t * b + t * t * t

    lo, hi = 0.0, 1.0
    for _ in range(10):
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
    current_scale: float = _NORMAL_SCALE
    current_alpha: float = _NORMAL_ALPHA
    start_scale: float = _NORMAL_SCALE
    start_alpha: float = _NORMAL_ALPHA
    target_scale: float = _NORMAL_SCALE
    target_alpha: float = _NORMAL_ALPHA
    started_at: float = 0.0
    duration: float = _RELEASE_SECONDS
    animating: bool = False

    def begin(self, *, scale: float, alpha: float, duration: float) -> bool:
        if (
            abs(scale - self.target_scale) < 0.0001
            and abs(alpha - self.target_alpha) < 0.1
            and self.animating
        ):
            return False

        if (
            abs(scale - self.current_scale) < 0.0001
            and abs(alpha - self.current_alpha) < 0.1
        ):
            self.current_scale = scale
            self.current_alpha = alpha
            self.target_scale = scale
            self.target_alpha = alpha
            self.animating = False
            self.surface.set_interaction(scale=scale, overlay_alpha=alpha)
            return False

        # Interrupted transitions continue from the exact currently visible
        # surface state. Press/release therefore never snaps.
        self.start_scale = self.current_scale
        self.start_alpha = self.current_alpha
        self.target_scale = scale
        self.target_alpha = alpha
        self.started_at = time.monotonic()
        self.duration = max(0.001, float(duration))
        self.animating = True
        return True


class NekroCardInteractionController(QObject):
    """Smooth card interaction with no layout or subtree animation work."""

    def __init__(self, window: QMainWindow, visual: VisualStyleController) -> None:
        super().__init__(window)
        self.window = window
        self.visual = visual
        self.states: dict[QFrame, _CardState] = {}
        self.hovered: QFrame | None = None
        self.pressed: QFrame | None = None

        for frame in window.findChildren(QFrame):
            if frame.objectName() not in _GLASS_NAMES:
                continue
            surface = visual.surface_for(frame)
            if surface is not None:
                self.states[frame] = _CardState(frame=frame, surface=surface)

        window.setMouseTracking(True)
        for widget in window.findChildren(QWidget):
            widget.setMouseTracking(True)

        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.setInterval(_FRAME_MS)
        self.timer.timeout.connect(self._tick)
        window.destroyed.connect(self._cleanup)

    def _card_from_widget(self, watched: QObject) -> QFrame | None:
        widget = watched if isinstance(watched, QWidget) else None
        while widget is not None:
            if isinstance(widget, QFrame) and widget in self.states:
                return widget
            widget = widget.parentWidget()
        return None

    def _ensure_timer(self) -> None:
        if any(state.animating for state in self.states.values()) and not self.timer.isActive():
            self.timer.start()

    def _animate_normal(self, frame: QFrame, duration: float = _RELEASE_SECONDS) -> None:
        self.states[frame].begin(
            scale=_NORMAL_SCALE,
            alpha=_NORMAL_ALPHA,
            duration=duration,
        )

    def _animate_hover(self, frame: QFrame, duration: float = _HOVER_SECONDS) -> None:
        self.states[frame].begin(
            scale=_HOVER_SCALE,
            alpha=_HOVER_ALPHA,
            duration=duration,
        )

    def _animate_active(self, frame: QFrame) -> None:
        self.states[frame].begin(
            scale=_ACTIVE_SCALE,
            alpha=_ACTIVE_ALPHA,
            duration=_PRESS_SECONDS,
        )

    def _set_hovered(self, frame: QFrame | None) -> None:
        if frame is self.hovered:
            return

        previous = self.hovered
        self.hovered = frame

        if previous is not None and previous is not self.pressed:
            self._animate_normal(previous)
        if frame is not None and frame is not self.pressed:
            self._animate_hover(frame)

        self._ensure_timer()

    def _press(self, frame: QFrame | None) -> None:
        self.pressed = frame
        if frame is not None:
            self._animate_active(frame)
            self._ensure_timer()

    def _release(self, frame_under_pointer: QFrame | None) -> None:
        pressed = self.pressed
        self.pressed = None
        self._set_hovered(frame_under_pointer)

        if pressed is not None:
            if pressed is frame_under_pointer:
                self._animate_hover(pressed, duration=_RELEASE_SECONDS)
            else:
                self._animate_normal(pressed, duration=_RELEASE_SECONDS)
            self._ensure_timer()

    def _tick(self) -> None:
        now = time.monotonic()
        any_animating = False

        for state in self.states.values():
            if not state.animating:
                continue

            raw = (now - state.started_at) / state.duration
            if raw >= 1.0:
                state.current_scale = state.target_scale
                state.current_alpha = state.target_alpha
                state.animating = False
            else:
                eased = _css_ease(raw)
                state.current_scale = state.start_scale + (
                    state.target_scale - state.start_scale
                ) * eased
                state.current_alpha = state.start_alpha + (
                    state.target_alpha - state.start_alpha
                ) * eased
                any_animating = True

            state.surface.set_interaction(
                scale=state.current_scale,
                overlay_alpha=state.current_alpha,
            )

        if not any_animating and not any(state.animating for state in self.states.values()):
            self.timer.stop()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        event_type = event.type()

        if isinstance(event, QMouseEvent):
            frame = self._card_from_widget(watched)
            if event_type == QEvent.MouseMove:
                self._set_hovered(frame)
            elif event_type == QEvent.MouseButtonPress:
                self._set_hovered(frame)
                self._press(frame)
            elif event_type == QEvent.MouseButtonRelease:
                self._release(frame)

        if watched is self.window and event_type == QEvent.Leave:
            pressed = self.pressed
            self.pressed = None
            self._set_hovered(None)
            if pressed is not None:
                self._animate_normal(pressed)
                self._ensure_timer()

        return False

    def _cleanup(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        self.timer.stop()
        for state in self.states.values():
            state.surface.set_interaction(
                scale=_NORMAL_SCALE,
                overlay_alpha=_NORMAL_ALPHA,
            )


def install_nekro_card_fx(
    window: QMainWindow,
    visual: VisualStyleController,
) -> NekroCardInteractionController:
    controller = NekroCardInteractionController(window, visual)
    window._nekro_card_fx = controller  # type: ignore[attr-defined]
    return controller
