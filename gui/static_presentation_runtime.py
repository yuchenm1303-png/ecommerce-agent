from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QTimer
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication, QMainWindow, QWidget


class StaticPresentationRuntime(QObject):
    """Browser-style presentation owner used only while background drift is off.

    Static mode is event driven: native mouse events update interaction intent
    immediately and one visual frame pump advances only the already-established
    card tween plus sakura/cursor surface. There is no QCursor polling, widget
    sample queue, Quick frameSwapped wait, renderer switch or alternate glass
    implementation in this path.

    Background-drift mode remains owned by PresentationClock. This controller
    only blocks that clock's timeout signal while static mode is active and
    removes the previous static input bridge; when drift is enabled it stops
    itself and unblocks the original clock without changing its implementation.
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
        self.effects = effects
        self._static_enabled = False
        self._cleaned = False
        self._left_down = False
        self._last_global: QPoint | None = None
        self._app = QApplication.instance()
        self._toggle = getattr(window, "_background_drift_switch", None)

        interval_s = float(getattr(card_fx, "_motion_interval_s", 1.0 / 60.0) or (1.0 / 60.0))
        self._frame_timer = QTimer(self)
        self._frame_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._frame_timer.setInterval(max(4, int(round(interval_s * 1000.0))))
        self._frame_timer.timeout.connect(self._frame)

        self._remove_legacy_static_bridge()
        self._enable_mouse_tracking_tree()
        if self._app is not None:
            self._app.installEventFilter(self)
        if self._toggle is not None:
            try:
                self._toggle.toggled.connect(self._on_drift_changed)
            except (AttributeError, RuntimeError, TypeError):
                self._toggle = None

        window.destroyed.connect(self.cleanup)
        self._on_drift_changed(bool(getattr(clock, "background_drift_enabled", False)))

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

    def _enable_widget_tracking(self, widget: QWidget | None) -> None:
        if widget is None:
            return
        try:
            if widget.window() is self.window or widget is self.window:
                widget.setMouseTracking(True)
        except RuntimeError:
            return

    def _enable_mouse_tracking_tree(self) -> None:
        self._enable_widget_tracking(self.window)
        for widget in self.window.findChildren(QWidget):
            self._enable_widget_tracking(widget)

    def _belongs_to_window(self, watched: QObject) -> bool:
        if watched is self.window:
            return True
        if not isinstance(watched, QWidget):
            return False
        try:
            return watched.window() is self.window
        except RuntimeError:
            return False

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

    def _can_present(self) -> bool:
        if not self._static_enabled or self._cleaned:
            return False
        if getattr(self.clock, "_holds", None):
            return False
        try:
            return bool(self.window.isVisible() and not self.window.isMinimized())
        except RuntimeError:
            return False

    def _sample_effect_pointer(self, global_pos: QPoint) -> None:
        sampler = getattr(self.effects, "_sample_pointer", None)
        if callable(sampler):
            try:
                sampler(global_pos, left_down=self._left_down)
                return
            except RuntimeError:
                return

        try:
            self.effects.presentation_tick(
                global_pos,
                left_down=self._left_down,
                now_s=time.perf_counter(),
            )
        except RuntimeError:
            pass

    def _publish_pointer(self, global_pos: QPoint, *, input_changed: bool) -> None:
        if not self._can_present():
            return
        self._last_global = QPoint(global_pos)
        now_s = time.perf_counter()
        try:
            self.card_fx.presentation_tick(
                global_pos,
                left_down=self._left_down,
                now_s=now_s,
                input_changed=input_changed,
            )
        except RuntimeError:
            pass
        self._sample_effect_pointer(global_pos)

    def _confirm_leave(self) -> None:
        if not self._can_present():
            return
        try:
            global_pos = QPoint(QCursor.pos())
        except RuntimeError:
            return
        self._publish_pointer(global_pos, input_changed=True)

    def _sync_pointer_once(self) -> None:
        if not self._can_present():
            return
        try:
            global_pos = QPoint(QCursor.pos())
        except RuntimeError:
            return
        self._publish_pointer(global_pos, input_changed=True)

    def _frame(self) -> None:
        if not self._can_present():
            return
        global_pos = self._last_global
        if global_pos is None:
            try:
                global_pos = QPoint(QCursor.pos())
            except RuntimeError:
                return
            self._last_global = QPoint(global_pos)

        now_s = time.perf_counter()
        try:
            self.card_fx.presentation_tick(
                global_pos,
                left_down=self._left_down,
                now_s=now_s,
                input_changed=False,
            )
        except RuntimeError:
            pass
        try:
            self.effects.presentation_tick(
                global_pos,
                left_down=self._left_down,
                now_s=now_s,
            )
        except RuntimeError:
            pass

    def _enter_static(self) -> None:
        if self._cleaned:
            return
        self._static_enabled = True
        self._left_down = False
        self._last_global = None

        timer = getattr(self.clock, "timer", None)
        if timer is not None:
            try:
                timer.blockSignals(True)
            except RuntimeError:
                pass
        clear_lane = getattr(self.clock, "_clear_widget_lane", None)
        if callable(clear_lane):
            try:
                clear_lane()
            except RuntimeError:
                pass

        if not self._frame_timer.isActive():
            self._frame_timer.start()
        QTimer.singleShot(0, self._sync_pointer_once)

    def _leave_static(self) -> None:
        self._static_enabled = False
        self._frame_timer.stop()
        timer = getattr(self.clock, "timer", None)
        if timer is not None:
            try:
                timer.blockSignals(False)
            except RuntimeError:
                pass

    def _on_drift_changed(self, enabled: bool) -> None:
        if bool(enabled):
            self._leave_static()
        else:
            self._enter_static()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        event_type = event.type()

        if event_type == QEvent.Type.ChildAdded and self._belongs_to_window(watched):
            child_getter = getattr(event, "child", None)
            child = child_getter() if callable(child_getter) else None
            if isinstance(child, QWidget):
                QTimer.singleShot(0, lambda widget=child: self._enable_widget_tracking(widget))
            return False

        if not self._can_present() or not self._belongs_to_window(watched):
            return False

        if event_type not in {
            QEvent.Type.MouseMove,
            QEvent.Type.Enter,
            QEvent.Type.Leave,
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseButtonRelease,
        }:
            return False

        global_pos = self._event_global_pos(event)

        if event_type in {QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease}:
            button_getter = getattr(event, "button", None)
            if callable(button_getter):
                try:
                    if button_getter() != Qt.MouseButton.LeftButton:
                        self._publish_pointer(global_pos, input_changed=True)
                        return False
                except RuntimeError:
                    return False
            self._left_down = event_type == QEvent.Type.MouseButtonPress

        self._publish_pointer(global_pos, input_changed=True)

        if event_type == QEvent.Type.Leave and getattr(self.card_fx, "hovered", None) is not None:
            QTimer.singleShot(0, self._confirm_leave)
        return False

    def cleanup(self) -> None:
        if self._cleaned:
            return
        self._cleaned = True
        self._static_enabled = False
        self._frame_timer.stop()

        toggle = self._toggle
        self._toggle = None
        if toggle is not None:
            try:
                toggle.toggled.disconnect(self._on_drift_changed)
            except (AttributeError, RuntimeError, TypeError):
                pass

        app = self._app
        self._app = None
        if app is not None:
            try:
                app.removeEventFilter(self)
            except RuntimeError:
                pass

        timer = getattr(self.clock, "timer", None)
        if timer is not None:
            try:
                timer.blockSignals(False)
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
