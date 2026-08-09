from __future__ import annotations

import time
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject, QPointF, Qt, QTimer
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QFrame, QMainWindow, QWidget

from .visual_style import GlassBackdrop, VisualStyleController


# Keep card interaction cheap and uniform across every business card.
# QWidget subtrees are never scaled/re-laid-out. Only the cached glass surface
# opacity changes, so tables, logs, inputs and labels stay out of animation work.
_GLASS_NAMES = {"glassCard", "heroCard", "statusCard", "microCard"}
_FRAME_MS = 16

_NORMAL_ALPHA = 64.0
_HOVER_ALPHA = 82.0   # subtle darkening for every card
_ACTIVE_ALPHA = 96.0  # slightly deeper press feedback, still no subtree scale

_HOVER_SECONDS = 0.12
_PRESS_SECONDS = 0.08
_RELEASE_SECONDS = 0.12


def _css_ease(progress: float) -> float:
    """CSS default ease cubic-bezier(.25,.1,.25,1)."""

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

    def begin(self, *, alpha: float, duration: float) -> bool:
        alpha = float(alpha)
        if abs(alpha - self.target_alpha) < 0.1 and self.animating:
            return False

        if abs(alpha - self.current_alpha) < 0.1:
            self.current_alpha = alpha
            self.start_alpha = alpha
            self.target_alpha = alpha
            self.animating = False
            self.surface.set_interaction(scale=1.0, overlay_alpha=alpha)
            return False

        # Interrupted hover/press/release transitions continue from the exact
        # currently visible opacity, so feedback never snaps between states.
        self.start_alpha = self.current_alpha
        self.target_alpha = alpha
        self.started_at = time.monotonic()
        self.duration = max(0.001, float(duration))
        self.animating = True
        return True


class NekroCardInteractionController(QObject):
    """Uniform low-cost hover/press feedback for every glass business card."""

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

        # Ensure MouseMove keeps arriving over labels, buttons, table viewports,
        # scroll-area viewports and other child widgets.
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

    def _card_from_widget(self, widget: QWidget | None) -> QFrame | None:
        current = widget
        while current is not None:
            if isinstance(current, QFrame) and current in self.states:
                return current
            current = current.parentWidget()
        return None

    def _card_at_global(self, point: QPointF) -> QFrame | None:
        """Hit-test inside the baseline QWidget tree before desktop widgetAt().

        The QWidget tree is now a native child of QQuickWindow. window.childAt()
        stays in Qt's own logical client coordinates and remains reliable there,
        while QApplication.widgetAt() can miss the dynamically reparented HWND.
        """

        local = self.window.mapFromGlobal(point.toPoint())
        if self.window.rect().contains(local):
            card = self._card_from_widget(self.window.childAt(local))
            if card is not None:
                return card
        return self._card_from_widget(QApplication.widgetAt(point.toPoint()))

    def _ensure_timer(self) -> None:
        if any(state.animating for state in self.states.values()) and not self.timer.isActive():
            self.timer.start()

    def _animate(self, frame: QFrame, alpha: float, duration: float) -> None:
        state = self.states.get(frame)
        if state is not None:
            state.begin(alpha=alpha, duration=duration)
            self._ensure_timer()

    def _set_hovered(self, frame: QFrame | None) -> None:
        if frame is self.hovered:
            return

        previous = self.hovered
        self.hovered = frame

        if previous is not None and previous is not self.pressed:
            self._animate(previous, _NORMAL_ALPHA, _RELEASE_SECONDS)
        if frame is not None and frame is not self.pressed:
            self._animate(frame, _HOVER_ALPHA, _HOVER_SECONDS)

    def _press(self, frame: QFrame | None) -> None:
        self.pressed = frame
        if frame is not None:
            self._animate(frame, _ACTIVE_ALPHA, _PRESS_SECONDS)

    def _release(self, frame_under_pointer: QFrame | None) -> None:
        pressed = self.pressed
        self.pressed = None
        self._set_hovered(frame_under_pointer)

        if pressed is not None:
            target = _HOVER_ALPHA if pressed is frame_under_pointer else _NORMAL_ALPHA
            self._animate(pressed, target, _RELEASE_SECONDS)

    def _tick(self) -> None:
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

            # Native-size cached glass repaint only; never scale a card subtree.
            state.surface.set_interaction(
                scale=1.0,
                overlay_alpha=state.current_alpha,
            )

        if not any_animating and not any(state.animating for state in self.states.values()):
            self.timer.stop()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        event_type = event.type()

        if isinstance(event, QMouseEvent):
            card = self._card_from_widget(watched if isinstance(watched, QWidget) else None)
            if card is None:
                card = self._card_at_global(event.globalPosition())

            if event_type == QEvent.MouseMove:
                self._set_hovered(card)
            elif event_type == QEvent.MouseButtonPress:
                self._set_hovered(card)
                self._press(card)
            elif event_type == QEvent.MouseButtonRelease:
                self._release(card)

        # Enter is not a QMouseEvent in Qt 6. Resolve it through the same Qt-local
        # widget tree so a card responds immediately when the pointer crosses in.
        if event_type == QEvent.Enter and isinstance(watched, QWidget):
            card = self._card_from_widget(watched)
            if card is not None:
                self._set_hovered(card)

        if watched is self.window and event_type == QEvent.Leave:
            pressed = self.pressed
            self.pressed = None
            self._set_hovered(None)
            if pressed is not None:
                self._animate(pressed, _NORMAL_ALPHA, _RELEASE_SECONDS)

        return False

    def _cleanup(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        self.timer.stop()
        for state in self.states.values():
            state.surface.set_interaction(scale=1.0, overlay_alpha=_NORMAL_ALPHA)


def install_nekro_card_fx(
    window: QMainWindow,
    visual: VisualStyleController,
) -> NekroCardInteractionController:
    controller = NekroCardInteractionController(window, visual)
    window._nekro_card_fx = controller  # type: ignore[attr-defined]
    return controller
