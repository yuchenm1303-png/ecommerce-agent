from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QElapsedTimer, QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QCursor, QMouseEvent
from PySide6.QtWidgets import QApplication, QFrame, QMainWindow, QWidget

from .visual_style import GlassBackdrop, VisualStyleController


_GLASS_NAMES = {"glassCard", "heroCard", "statusCard", "microCard"}

_NORMAL_ALPHA = 64.0
_HOVER_ALPHA = 90.0
_ACTIVE_ALPHA = 110.0

# This is input sampling, not presentation animation. Eight milliseconds keeps the
# pointer/card relationship fresher than one 60 Hz display frame even when Qt
# coalesces or skips sibling Enter/Leave traffic in the layered child window.
_POINTER_SAMPLE_MS = 8

# A fast physical click can deliver press+release before QWidget's backing store is
# presented. Keep the active tint alive long enough to cross at least one display
# frame; business click delivery is never delayed by this visual hold.
_MIN_PRESSED_MS = 24


@dataclass(slots=True)
class _CardState:
    frame: QFrame
    surface: GlassBackdrop
    current_alpha: float = _NORMAL_ALPHA

    def snap(self, alpha: float) -> None:
        alpha = float(alpha)
        if abs(alpha - self.current_alpha) < 0.1:
            return
        self.current_alpha = alpha
        self.surface.set_interaction(scale=1.0, overlay_alpha=alpha)

    def republish(self) -> None:
        self.surface.set_interaction(scale=1.0, overlay_alpha=self.current_alpha)


class NekroCardInteractionController(QObject):
    """One pointer router for all glass-card hover and press feedback.

    Card interaction no longer depends on every nested QWidget producing a
    perfectly paired Enter/Leave/Press/Release sequence. The current global cursor
    position is the authority. Existing card-local mouse events trigger an
    immediate resample, while one tiny 8 ms sampler closes any gaps left by Qt or
    Windows event coalescing in the manually embedded layered QWidget surface.

    Presentation remains a deterministic three-state model:
        NORMAL 64 -> HOVER 90 -> PRESSED 110.

    Pressed tint is held for at least 24 ms purely so a fast press/release pair
    cannot collapse into one backing-store present. No business click, card open,
    or child-control event is consumed or delayed.
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
        self._left_down = bool(QApplication.mouseButtons() & Qt.MouseButton.LeftButton)

        for frame in window.findChildren(QFrame):
            if frame.objectName() not in _GLASS_NAMES:
                continue
            surface = visual.surface_for(frame)
            if surface is None:
                continue
            self.states[frame] = _CardState(frame=frame, surface=surface)

        for frame in self.states:
            self._register_widget_tree(frame)

        self._press_clock = QElapsedTimer()

        self._release_timer = QTimer(self)
        self._release_timer.setSingleShot(True)
        self._release_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._release_timer.timeout.connect(self._finish_release)

        self._pointer_timer = QTimer(self)
        self._pointer_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._pointer_timer.setInterval(_POINTER_SAMPLE_MS)
        self._pointer_timer.timeout.connect(self._sample_pointer)
        self._pointer_timer.start()

        window.destroyed.connect(self._cleanup)

    def _nearest_card(self, widget: QWidget | None) -> QFrame | None:
        current = widget
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
        for widget in [root, *root.findChildren(QWidget)]:
            frame = self._nearest_card(widget)
            if frame is not None:
                self._watch_widget(widget, frame)

    def _belongs_to_window(self, widget: QWidget | None) -> bool:
        current = widget
        while current is not None:
            if current is self.window:
                return True
            current = current.parentWidget()
        return False

    def _card_at_global(self, global_pos) -> QFrame | None:  # noqa: ANN001
        """Resolve the actual card below a global cursor point.

        QApplication.widgetAt() is preferred because it respects real QWidget
        stacking and clipping. The geometry fallback exists only for the unusual
        layered-child case where Qt cannot resolve a widget for an otherwise valid
        point inside this application window.
        """

        widget = QApplication.widgetAt(global_pos)
        if widget is not None:
            card = self._nearest_card(widget)
            if card is not None:
                return card
            if self._belongs_to_window(widget):
                return None

        best: QFrame | None = None
        best_depth = -1
        for frame in self.states:
            if not frame.isVisibleTo(self.window) or not frame.isEnabled():
                continue
            try:
                local = frame.mapFromGlobal(global_pos)
            except RuntimeError:
                continue
            if not frame.rect().contains(local):
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

    def _publish(self, frame: QFrame | None, alpha: float) -> None:
        if self._suspended or frame is None:
            return
        state = self.states.get(frame)
        if state is not None:
            state.snap(alpha)

    def _set_hover(self, frame: QFrame | None) -> None:
        if self._suspended or self.pressed is not None or self._release_timer.isActive():
            return
        previous = self.hovered
        if previous is frame:
            return
        self.hovered = frame
        if previous is not None:
            self._publish(previous, _NORMAL_ALPHA)
        if frame is not None:
            self._publish(frame, _HOVER_ALPHA)

    def _begin_press(self, frame: QFrame | None) -> None:
        if self._suspended:
            return

        # A new physical press supersedes any visual release still waiting to
        # finish. Normalize the previous card first, then start the new pulse.
        if self._release_timer.isActive():
            self._release_timer.stop()
            previous = self.pressed
            self.pressed = None
            if previous is not None:
                if previous is frame:
                    self._publish(previous, _HOVER_ALPHA)
                else:
                    self._publish(previous, _NORMAL_ALPHA)

        if frame is None:
            self.pressed = None
            self._set_hover(None)
            return

        previous_hover = self.hovered
        if previous_hover is not None and previous_hover is not frame:
            self._publish(previous_hover, _NORMAL_ALPHA)

        self.hovered = frame
        self.pressed = frame
        self._press_clock.start()
        self._publish(frame, _ACTIVE_ALPHA)

    def _end_press(self) -> None:
        if self._suspended:
            return
        if self.pressed is None:
            self._set_hover(self._card_at_global(QCursor.pos()))
            return

        elapsed = self._press_clock.elapsed() if self._press_clock.isValid() else _MIN_PRESSED_MS
        remaining = max(0, _MIN_PRESSED_MS - int(elapsed))
        if remaining > 0:
            self._release_timer.start(remaining)
        else:
            self._finish_release()

    def _finish_release(self) -> None:
        if self._suspended:
            return
        previous = self.pressed
        self.pressed = None
        current = self._card_at_global(QCursor.pos())

        if previous is not None and previous is not current:
            self._publish(previous, _NORMAL_ALPHA)
        self.hovered = current
        if current is not None:
            self._publish(current, _HOVER_ALPHA)

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
            # Recovery path: if a native transition caused the edge event to be
            # missed, the sampled current button state still restores PRESSED.
            if self.pressed is None:
                self._begin_press(current)
            return

        if not self._release_timer.isActive():
            self._set_hover(current)

    def suspend_for_modal(self) -> None:
        if self._suspended:
            return
        self._suspended = True
        self._pointer_timer.stop()
        self._release_timer.stop()
        self._left_down = False
        self.hovered = None
        self.pressed = None
        for state in self.states.values():
            state.republish()

    def resume_from_modal(self) -> None:
        if not self._suspended:
            return
        self._suspended = False
        self._left_down = bool(QApplication.mouseButtons() & Qt.MouseButton.LeftButton)
        self.hovered = None
        self.pressed = None

        current = self._card_at_global(QCursor.pos())
        for frame, state in self.states.items():
            state.snap(_HOVER_ALPHA if frame is current else _NORMAL_ALPHA)
        self.hovered = current
        self._pointer_timer.start()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        frame = self._watched_to_card.get(watched)
        if frame is None:
            return False

        event_type = event.type()

        if event_type == QEvent.Type.ChildAdded:
            child_getter = getattr(event, "child", None)
            child = child_getter() if callable(child_getter) else None
            if isinstance(child, QWidget):
                self._register_widget_tree(child)
            return False

        if self._suspended:
            return False

        # Existing widget events are zero-latency hints. The pointer sampler is
        # still the authority and guarantees correction when sibling events are
        # coalesced or skipped during fast movement.
        if event_type in {QEvent.Type.Enter, QEvent.Type.MouseMove, QEvent.Type.Leave}:
            self._sample_pointer()
        elif event_type == QEvent.Type.MouseButtonPress and isinstance(event, QMouseEvent):
            if event.button() == Qt.MouseButton.LeftButton:
                self._left_down = True
                self._begin_press(self._card_at_global(QCursor.pos()))
        elif event_type == QEvent.Type.MouseButtonRelease and isinstance(event, QMouseEvent):
            if event.button() == Qt.MouseButton.LeftButton:
                self._left_down = False
                self._end_press()
        elif watched is frame and event_type in {QEvent.Type.Hide, QEvent.Type.EnabledChange}:
            if not frame.isVisible() or not frame.isEnabled():
                if self.hovered is frame:
                    self.hovered = None
                if self.pressed is frame:
                    self.pressed = None
                    self._release_timer.stop()
                self._publish(frame, _NORMAL_ALPHA)
        return False

    def _cleanup(self) -> None:
        self._pointer_timer.stop()
        self._release_timer.stop()
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
