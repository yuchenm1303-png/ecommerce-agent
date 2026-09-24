from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QTimer
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QMainWindow, QVBoxLayout, QWidget

from .mode_toggle import WorkspaceModeSwitch


_LEGACY_FRAME_MS = 16
_BACKGROUND_DRIFT_DEFAULT = True


class BackgroundDriftSwitch(WorkspaceModeSwitch):
    """Established micro-switch visuals for wallpaper pointer parallax."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("backgroundDriftSwitch")
        self.setAccessibleName("背景漂移")
        self.set_checked_immediate(_BACKGROUND_DRIFT_DEFAULT)

    def _sync_tooltip(self, checked: bool) -> None:
        self.setToolTip("背景漂移已开启 · 点击关闭" if checked else "背景漂移已关闭 · 点击开启")


class PresentationClock(QObject):
    """Event-driven presentation coordinator.

    Normal presentation belongs to QQuickWindow. Pointer motion is forwarded only
    when Qt actually delivers a pointer event; the Quick scene's own FrameAnimation
    performs the visual interpolation. There is therefore no 8/16 ms Python cursor
    polling loop while the normal Quick UI is active.

    The small timer below exists only for the legacy QWidget fallback lane, where
    QWidget sakura/card effects still require time-based ticks. StaticQmlView disables
    that lane as soon as the unified Quick scene owns presentation.
    """

    def __init__(
        self,
        window: QMainWindow,
        *,
        background: Any,
        card_fx: Any,
        effects: Any,
    ) -> None:
        super().__init__(window)
        self.window = window
        self.background = background
        self.card_fx = card_fx
        self.effects = effects
        self._quick_window = getattr(background, "quick_window", None)

        self._holds: set[str] = set()
        self._window_paused = False
        self._background_drift_enabled = _BACKGROUND_DRIFT_DEFAULT
        self._widget_lane_enabled = True
        self._last_legacy_global: tuple[int, int] | None = None
        self._last_legacy_left: bool | None = None

        # Keep the historical public attribute name for compatibility, but this
        # timer is now strictly a legacy-fallback clock. In normal Quick mode it is
        # stopped for the entire application lifetime.
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.setInterval(_LEGACY_FRAME_MS)
        self.timer.timeout.connect(self._legacy_tick)

        window.installEventFilter(self)
        quick = self._quick_window
        if isinstance(quick, QObject):
            quick.installEventFilter(self)
        window.destroyed.connect(self.cleanup)

        self.set_background_drift_enabled(_BACKGROUND_DRIFT_DEFAULT)
        self._sync_window_state()

    @property
    def running(self) -> bool:
        return bool(
            not self._holds
            and not self._window_paused
            and (self._widget_lane_enabled or self._background_drift_enabled)
        )

    @property
    def background_drift_enabled(self) -> bool:
        return self._background_drift_enabled

    @property
    def widget_lane_enabled(self) -> bool:
        return self._widget_lane_enabled

    def set_widget_lane_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._widget_lane_enabled:
            return
        self._widget_lane_enabled = enabled
        self._last_legacy_global = None
        self._last_legacy_left = None
        self._sync_window_state()

    def _center_background(self) -> None:
        quick = self._quick_window
        if quick is None:
            return
        try:
            quick.setProperty("pointerX", 0.0)
            quick.setProperty("pointerY", 0.0)
            quick.setProperty("animationRunning", True)
        except RuntimeError:
            pass

    def set_background_drift_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._background_drift_enabled:
            if not enabled:
                self._center_background()
            return
        self._background_drift_enabled = enabled
        try:
            self.background.reset_pointer_identity()
        except (AttributeError, RuntimeError):
            pass
        if not enabled:
            self._center_background()

    def _quick_available(self) -> bool:
        quick = self._quick_window
        if quick is None:
            return False
        try:
            return bool(
                quick.isVisible()
                and quick.isExposed()
                and not (quick.windowState() & Qt.WindowState.WindowMinimized)
            )
        except (AttributeError, RuntimeError, TypeError):
            return False

    def _sync_window_state(self) -> None:
        try:
            paused = bool(not self.window.isVisible() or self.window.isMinimized())
        except RuntimeError:
            paused = True
        quick = self._quick_window
        if quick is not None:
            try:
                paused = paused or bool(quick.windowState() & Qt.WindowState.WindowMinimized)
            except RuntimeError:
                pass
        self._window_paused = paused

        legacy_should_run = bool(
            self._widget_lane_enabled
            and not self._holds
            and not self._window_paused
        )
        if legacy_should_run:
            if not self.timer.isActive():
                self._last_legacy_global = None
                self._last_legacy_left = None
                self.timer.start()
        else:
            self.timer.stop()

    def suspend(self, reason: str) -> None:
        token = str(reason or "presentation").strip() or "presentation"
        self._holds.add(token)
        self.timer.stop()
        try:
            self.background.pause_pointer_animation()
        except (AttributeError, RuntimeError):
            pass

    def resume(self, reason: str) -> None:
        token = str(reason or "presentation").strip() or "presentation"
        self._holds.discard(token)
        self._last_legacy_global = None
        self._last_legacy_left = None
        try:
            self.background.reset_pointer_identity()
        except (AttributeError, RuntimeError):
            pass
        self._sync_window_state()

    def _publish_quick_pointer(self, event: QEvent) -> None:
        if (
            not self._background_drift_enabled
            or self._holds
            or self._window_paused
            or not self._quick_available()
        ):
            return

        quick = self._quick_window
        if quick is None:
            return
        position_getter = getattr(event, "position", None)
        try:
            if callable(position_getter):
                position = position_getter()
                local = QPoint(round(float(position.x())), round(float(position.y())))
                global_pos = quick.mapToGlobal(local)
            else:
                global_pos = QCursor.pos()
            self.background.presentation_tick(global_pos, input_changed=True)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return

    def _legacy_tick(self) -> None:
        if (
            not self._widget_lane_enabled
            or self._holds
            or self._window_paused
        ):
            self._sync_window_state()
            return

        try:
            global_pos = QCursor.pos()
            point = (int(global_pos.x()), int(global_pos.y()))
            left_down = bool(QApplication.mouseButtons() & Qt.MouseButton.LeftButton)
        except RuntimeError:
            return

        previous_left = self._last_legacy_left
        input_changed = point != self._last_legacy_global or left_down != previous_left
        self._last_legacy_global = point
        self._last_legacy_left = left_down
        now_s = time.perf_counter()

        if self._background_drift_enabled and input_changed:
            try:
                self.background.presentation_tick(global_pos, input_changed=True)
            except RuntimeError:
                pass

        try:
            self.card_fx.presentation_tick(
                global_pos,
                left_down=left_down,
                now_s=now_s,
                input_changed=input_changed,
            )
        except RuntimeError:
            pass
        try:
            self.effects.presentation_tick(
                global_pos,
                left_down=left_down,
                now_s=now_s,
            )
        except RuntimeError:
            pass

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        event_type = event.type()

        if watched is self.window:
            if event_type in {
                QEvent.Type.Show,
                QEvent.Type.Hide,
                QEvent.Type.WindowStateChange,
            }:
                QTimer.singleShot(0, self._sync_window_state)
            return False

        if watched is self._quick_window:
            if event_type in {
                QEvent.Type.Show,
                QEvent.Type.Hide,
                QEvent.Type.Expose,
                QEvent.Type.WindowStateChange,
            }:
                QTimer.singleShot(0, self._sync_window_state)
                return False

            if event_type in {
                QEvent.Type.MouseMove,
                QEvent.Type.HoverMove,
                QEvent.Type.Enter,
            }:
                self._publish_quick_pointer(event)
            elif event_type == QEvent.Type.Leave and self._background_drift_enabled:
                self._center_background()
            return False

        return False

    def cleanup(self) -> None:
        self.timer.stop()
        self._holds.clear()
        try:
            self.window.removeEventFilter(self)
        except RuntimeError:
            pass
        quick = self._quick_window
        self._quick_window = None
        if isinstance(quick, QObject):
            try:
                quick.removeEventFilter(self)
            except RuntimeError:
                pass


def _install_background_drift_switch(
    window: QMainWindow,
    clock: PresentationClock,
) -> BackgroundDriftSwitch:
    existing = getattr(window, "_background_drift_switch", None)
    if isinstance(existing, BackgroundDriftSwitch):
        existing.set_checked_immediate(clock.background_drift_enabled)
        return existing

    root = window.centralWidget()
    outer = root.layout() if root is not None else None
    header_item = outer.itemAt(0) if isinstance(outer, QVBoxLayout) and outer.count() else None
    header = header_item.layout() if header_item is not None else None
    if root is None or not isinstance(header, QHBoxLayout):
        raise RuntimeError("background drift switch expected the common application header")

    label = QLabel("背景漂移", root)
    label.setObjectName("backgroundDriftToggleLabel")
    label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
    label.setFixedHeight(32)
    label.setStyleSheet("color: rgba(232, 241, 252, 178);")

    toggle = BackgroundDriftSwitch(root)
    toggle.set_checked_immediate(clock.background_drift_enabled)
    toggle.toggled.connect(clock.set_background_drift_enabled)

    header.addSpacing(10)
    header.addWidget(label, 0, Qt.AlignmentFlag.AlignBottom)
    header.addWidget(toggle, 0, Qt.AlignmentFlag.AlignBottom)

    window._background_drift_toggle_label = label  # type: ignore[attr-defined]
    window._background_drift_switch = toggle  # type: ignore[attr-defined]
    return toggle


def install_presentation_clock(
    window: QMainWindow,
    *,
    background: Any,
    card_fx: Any,
    effects: Any,
) -> PresentationClock:
    existing = getattr(window, "_presentation_clock", None)
    if isinstance(existing, PresentationClock):
        _install_background_drift_switch(window, existing)
        return existing
    clock = PresentationClock(
        window,
        background=background,
        card_fx=card_fx,
        effects=effects,
    )
    window._presentation_clock = clock  # type: ignore[attr-defined]
    _install_background_drift_switch(window, clock)
    return clock


__all__ = [
    "BackgroundDriftSwitch",
    "PresentationClock",
    "install_presentation_clock",
]
