from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QTimer
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication, QFrame, QMainWindow, QWidget


def _qt_top_level_window(widget: QWidget) -> QWidget | None:
    """Resolve QWidget::window without instance attribute lookup."""

    try:
        top = QWidget.window(widget)
    except (RuntimeError, TypeError):
        return None
    return top if isinstance(top, QWidget) else None


class StaticPresentationRuntime(QObject):
    """Event-only card input for the non-drifting presentation path.

    This object never changes widget geometry, never enables mouse tracking on the
    widget tree, never blocks PresentationClock and never owns a second frame
    timer. Native enter/leave/press/release events update the existing card state
    machine immediately; PresentationClock remains the single animation clock for
    the established 300 ms tween and ambient effects.

    When background drift is enabled this filter becomes inert. The original
    drifting presentation path remains owned entirely by PresentationClock.
    """

    def __init__(
        self,
        window: QMainWindow,
        *,
        clock: Any,
        card_fx: Any,
        effects: Any,
    ) -> None:
        super().__init__(window)
        self.window = window
        self.clock = clock
        self.card_fx = card_fx
        self.effects = effects  # Kept only for install signature compatibility.
        self._cleaned = False
        self._app = QApplication.instance()

        self._remove_legacy_static_bridge()
        if self._app is not None:
            self._app.installEventFilter(self)
        window.destroyed.connect(self.cleanup)

    def _remove_legacy_static_bridge(self) -> None:
        bridge = getattr(self.window, "_static_card_input_bridge", None)
        cleanup = getattr(bridge, "cleanup", None)
        if callable(cleanup):
            try:
                cleanup()
            except RuntimeError:
                pass
        if hasattr(self.window, "_static_card_input_bridge"):
            self.window._static_card_input_bridge = None  # type: ignore[attr-defined]

    def _belongs_to_window(self, watched: QObject) -> bool:
        if watched is self.window:
            return True
        if not isinstance(watched, QWidget):
            return False
        return _qt_top_level_window(watched) is self.window

    def _can_present_static(self) -> bool:
        if self._cleaned or bool(getattr(self.clock, "background_drift_enabled", False)):
            return False
        if getattr(self.clock, "_holds", None):
            return False
        try:
            return bool(self.window.isVisible() and not self.window.isMinimized())
        except RuntimeError:
            return False

    def _nearest_card(self, widget: QWidget | None) -> QFrame | None:
        resolver = getattr(self.card_fx, "_nearest_card", None)
        if callable(resolver):
            try:
                card = resolver(widget)
            except RuntimeError:
                card = None
            return card if isinstance(card, QFrame) else None

        states = getattr(self.card_fx, "states", {})
        current = widget
        while current is not None:
            if isinstance(current, QFrame) and current in states:
                return current
            if current is self.window:
                break
            current = current.parentWidget()
        return None

    def _card_at_global(self, global_pos: QPoint) -> QFrame | None:
        resolver = getattr(self.card_fx, "_card_at_global", None)
        if not callable(resolver):
            return None
        try:
            card = resolver(global_pos)
        except RuntimeError:
            return None
        return card if isinstance(card, QFrame) else None

    @staticmethod
    def _event_global_pos(event: QEvent) -> QPoint:
        for name in ("globalPosition", "globalPos"):
            getter = getattr(event, name, None)
            if not callable(getter):
                continue
            try:
                point = getter()
                to_point = getattr(point, "toPoint", None)
                if callable(to_point):
                    return QPoint(to_point())
                return QPoint(int(round(float(point.x()))), int(round(float(point.y()))))
            except (AttributeError, RuntimeError, TypeError, ValueError):
                continue
        try:
            return QPoint(QCursor.pos())
        except RuntimeError:
            return QPoint()

    def _wake_card_animation(self) -> None:
        wake = getattr(self.clock, "_mark_interaction_active", None)
        if callable(wake):
            try:
                wake(time.perf_counter())
            except RuntimeError:
                pass

    def _set_hover(self, card: QFrame | None) -> None:
        setter = getattr(self.card_fx, "_set_hover", None)
        if not callable(setter):
            return
        try:
            setter(card)
        except RuntimeError:
            return
        self._wake_card_animation()

    def _begin_press(self, card: QFrame | None) -> None:
        begin = getattr(self.card_fx, "_begin_press", None)
        if not callable(begin):
            return
        try:
            begin(card)
        except RuntimeError:
            return
        self._wake_card_animation()

    def _end_press(self, global_pos: QPoint) -> None:
        end = getattr(self.card_fx, "_end_press", None)
        if not callable(end):
            return
        try:
            end(global_pos)
        except RuntimeError:
            return
        self._wake_card_animation()

    def _sync_hover_from_cursor(self) -> None:
        if not self._can_present_static():
            return
        try:
            global_pos = QPoint(QCursor.pos())
        except RuntimeError:
            return
        self._set_hover(self._card_at_global(global_pos))

    @staticmethod
    def _is_left_button(event: QEvent) -> bool:
        getter = getattr(event, "button", None)
        if not callable(getter):
            return True
        try:
            return getter() == Qt.MouseButton.LeftButton
        except RuntimeError:
            return False

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        event_type = event.type()

        if event_type == QEvent.Type.WindowDeactivate and watched is self.window:
            if self._can_present_static():
                try:
                    global_pos = QPoint(QCursor.pos())
                except RuntimeError:
                    global_pos = QPoint()
                self._end_press(global_pos)
                self._set_hover(None)
            return False

        if not self._can_present_static() or not self._belongs_to_window(watched):
            return False

        if event_type == QEvent.Type.Enter:
            widget = watched if isinstance(watched, QWidget) else None
            card = self._nearest_card(widget)
            if card is not None:
                self._set_hover(card)
            return False

        if event_type == QEvent.Type.Leave:
            if getattr(self.card_fx, "hovered", None) is not None:
                QTimer.singleShot(0, self._sync_hover_from_cursor)
            return False

        if event_type == QEvent.Type.MouseButtonPress:
            if not self._is_left_button(event):
                return False
            widget = watched if isinstance(watched, QWidget) else None
            card = self._nearest_card(widget)
            if card is None:
                card = self._card_at_global(self._event_global_pos(event))
            self._begin_press(card)
            return False

        if event_type == QEvent.Type.MouseButtonRelease:
            if self._is_left_button(event):
                self._end_press(self._event_global_pos(event))
            return False

        return False

    def cleanup(self) -> None:
        if self._cleaned:
            return
        self._cleaned = True
        app = self._app
        self._app = None
        if app is not None:
            try:
                app.removeEventFilter(self)
            except RuntimeError:
                pass


def install_static_presentation_runtime(
    window: QMainWindow,
    *,
    clock: Any,
    card_fx: Any,
    effects: Any,
) -> StaticPresentationRuntime:
    existing = getattr(window, "_static_presentation_runtime", None)
    if isinstance(existing, StaticPresentationRuntime):
        return existing
    runtime = StaticPresentationRuntime(
        window,
        clock=clock,
        card_fx=card_fx,
        effects=effects,
    )
    window._static_presentation_runtime = runtime  # type: ignore[attr-defined]
    return runtime


__all__ = ["StaticPresentationRuntime", "install_static_presentation_runtime"]
