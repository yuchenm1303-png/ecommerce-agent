from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QCursor, QMouseEvent
from PySide6.QtWidgets import QFrame, QMainWindow, QWidget

from .visual_style import GlassBackdrop, VisualStyleController


_GLASS_NAMES = {"glassCard", "heroCard", "statusCard", "microCard"}

_NORMAL_ALPHA = 64.0
_HOVER_ALPHA = 90.0
_ACTIVE_ALPHA = 110.0


@dataclass(slots=True)
class _CardState:
    frame: QFrame
    surface: GlassBackdrop
    current_alpha: float = _NORMAL_ALPHA

    def snap(self, alpha: float) -> None:
        """Publish one card interaction state synchronously."""

        alpha = float(alpha)
        if abs(alpha - self.current_alpha) < 0.1:
            return
        self.current_alpha = alpha
        self.surface.set_interaction(scale=1.0, overlay_alpha=alpha)

    def republish(self) -> None:
        """Re-assert the exact visible state without changing it."""

        self.surface.set_interaction(scale=1.0, overlay_alpha=self.current_alpha)


class NekroCardInteractionController(QObject):
    """Immediate three-state interaction for every glass card.

    The interaction path is deliberately simple and deterministic:

    NORMAL 64 -> HOVER 90 -> PRESSED 110.

    Enter/move publishes HOVER immediately, every left press publishes PRESSED
    immediately, every release publishes HOVER (or NORMAL when released outside),
    and leaving publishes NORMAL. There is no Python animation timer between the
    physical pointer event and the native Quick card tint, so rapid repeated clicks
    always produce a complete 90 -> 110 -> 90 pulse.

    Events are observed across each card's complete QWidget subtree and are never
    consumed, so child controls retain their normal behavior.
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

        # Build all card states first so descendants of nested glass cards resolve
        # to their nearest glass-card ancestor.
        for frame in window.findChildren(QFrame):
            if frame.objectName() not in _GLASS_NAMES:
                continue
            surface = visual.surface_for(frame)
            if surface is None:
                continue
            self.states[frame] = _CardState(frame=frame, surface=surface)

        for frame in self.states:
            self._register_widget_tree(frame)

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
        for widget in [root, *root.findChildren(QWidget)]:
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

    def _publish(self, frame: QFrame, alpha: float) -> None:
        if self._suspended:
            return
        state = self.states.get(frame)
        if state is not None:
            state.snap(alpha)

    def _normalize_other_hover(self, frame: QFrame) -> None:
        previous = self.hovered
        if previous is not None and previous is not frame and previous is not self.pressed:
            self._publish(previous, _NORMAL_ALPHA)

    def _enter(self, frame: QFrame) -> None:
        self._normalize_other_hover(frame)
        self.hovered = frame
        if self.pressed is not frame:
            self._publish(frame, _HOVER_ALPHA)

    def _leave(self, frame: QFrame) -> None:
        if self.hovered is frame:
            self.hovered = None
        if self.pressed is not frame:
            self._publish(frame, _NORMAL_ALPHA)

    def _press(self, frame: QFrame) -> None:
        # Every physical press starts from the deterministic hover baseline, then
        # immediately publishes the darker active state. Release always restores
        # the baseline, so rapid clicks can never collapse into one long pressed
        # state or require a second click to become visible.
        self._normalize_other_hover(frame)
        self.hovered = frame
        previous = self.pressed
        if previous is not None and previous is not frame:
            self._publish(previous, _NORMAL_ALPHA)
        self.pressed = frame
        self._publish(frame, _ACTIVE_ALPHA)

    def _release(self, frame: QFrame, *, inside: bool) -> None:
        if self.pressed is frame:
            self.pressed = None
        elif self.pressed is not None:
            previous = self.pressed
            self.pressed = None
            if previous is not frame:
                self._publish(previous, _NORMAL_ALPHA)

        if inside:
            self._normalize_other_hover(frame)
            self.hovered = frame
            self._publish(frame, _HOVER_ALPHA)
        else:
            if self.hovered is frame:
                self.hovered = None
            self._publish(frame, _NORMAL_ALPHA)

    def _reset_card(self, frame: QFrame) -> None:
        if self.hovered is frame:
            self.hovered = None
        if self.pressed is frame:
            self.pressed = None
        self._publish(frame, _NORMAL_ALPHA)

    def suspend_for_modal(self) -> None:
        if self._suspended:
            return
        self._suspended = True
        self.hovered = None
        self.pressed = None
        for state in self.states.values():
            # Keep the exact tint already visible under the frozen modal backdrop.
            state.republish()

    def resume_from_modal(self) -> None:
        if not self._suspended:
            return
        self._suspended = False
        self.hovered = None
        self.pressed = None

        # Reconcile once with the real cursor position so the card already under
        # the cursor resumes directly in HOVER without waiting for another move.
        hovered: QFrame | None = None
        for frame, state in self.states.items():
            if hovered is None and self._cursor_inside_card(frame):
                hovered = frame
                state.snap(_HOVER_ALPHA)
            else:
                state.snap(_NORMAL_ALPHA)
        self.hovered = hovered

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        frame = self._watched_to_card.get(watched)
        if frame is None:
            return False

        event_type = event.type()

        # Cards can gain helper widgets after startup. Extend the same scoped
        # subtree watch on demand; no pointer polling or QApplication-wide filter.
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
            # Moving between children creates Leave events. Only clear hover when
            # the cursor has actually left the outer card.
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
