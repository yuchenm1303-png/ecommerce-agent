from __future__ import annotations

import math
from typing import Any

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QPointF,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QMainWindow, QWidget


_REVEAL_DELAY_MS = 48
_CURTAIN_MS = 500
_CURTAIN_FRACTION = 0.51
_CURTAIN_COLOR = QColor("#333333")


def _curve(c1x: float, c1y: float, c2x: float, c2y: float) -> QEasingCurve:
    curve = QEasingCurve(QEasingCurve.Type.BezierSpline)
    curve.addCubicBezierSegment(
        QPointF(c1x, c1y),
        QPointF(c2x, c2y),
        QPointF(1.0, 1.0),
    )
    return curve


_CURTAIN_EASE = _curve(0.645, 0.045, 0.355, 1.0)


class _StartupEntranceOverlay(QWidget):
    """Two lightweight opaque curtains over the already-settled live UI.

    Startup used to resample and blend two full-screen wallpaper pixmaps on every
    16 ms GUI-thread tick. That made the entrance cost scale directly with display
    resolution and competed with Qt Quick's render thread. The wallpaper and live
    interface already exist underneath this overlay, so the entrance only needs to
    move two solid panels out of the way. QVariantAnimation drives geometry only;
    no full-screen pixmap, blur, snapshot or custom paint pass exists here.
    """

    finished = Signal()

    def __init__(self, window: QMainWindow, visual: Any) -> None:
        super().__init__(window)
        self.window = window
        self.background = getattr(visual, "background", None)
        self._progress = 0.0
        self._reveal_started = False

        self.setObjectName("startupEntranceOverlay")
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._left_curtain = self._create_curtain("startupLeftCurtain")
        self._right_curtain = self._create_curtain("startupRightCurtain")

        self._animation = QVariantAnimation(self)
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.setDuration(_CURTAIN_MS)
        self._animation.setEasingCurve(_CURTAIN_EASE)
        self._animation.valueChanged.connect(self._on_animation_value)
        self._animation.finished.connect(self._on_animation_finished)

        self.setGeometry(window.rect())
        self._apply_progress(0.0)
        self.show()
        self.raise_()

    def _create_curtain(self, object_name: str) -> QWidget:
        panel = QWidget(self)
        panel.setObjectName(object_name)
        panel.setAutoFillBackground(True)
        panel.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        palette = panel.palette()
        palette.setColor(QPalette.ColorRole.Window, _CURTAIN_COLOR)
        panel.setPalette(palette)
        panel.show()
        return panel

    def begin(self) -> None:
        self._apply_progress(0.0)
        self.show()
        self.raise_()

    def begin_reveal(self) -> None:
        if self._reveal_started:
            return
        self._reveal_started = True
        self._progress = 0.0
        self._apply_progress(0.0)
        self._animation.start()

    def resize_to_window(self) -> None:
        self.setGeometry(self.window.rect())
        self._apply_progress(self._progress)

    def _on_animation_value(self, value: object) -> None:
        try:
            progress = float(value)
        except (TypeError, ValueError):
            return
        self._progress = max(0.0, min(1.0, progress))
        self._apply_progress(self._progress)

    def _on_animation_finished(self) -> None:
        self._progress = 1.0
        self._apply_progress(1.0)
        self.finished.emit()

    def _apply_progress(self, progress: float) -> None:
        width = max(1, int(self.width()))
        height = max(1, int(self.height()))
        panel_width = max(1, math.ceil(width * _CURTAIN_FRACTION))
        displacement = round(panel_width * max(0.0, min(1.0, progress)))
        self._left_curtain.setGeometry(-displacement, 0, panel_width + 1, height)
        self._right_curtain.setGeometry(
            width - panel_width + displacement,
            0,
            panel_width + 1,
            height,
        )


class StartupEntranceController(QObject):
    """Reveal the settled live interface without animating full-screen pixels."""

    def __init__(self, window: QMainWindow, visual: Any) -> None:
        super().__init__(window)
        self.window = window
        self.visual = visual
        self.background = getattr(visual, "background", None)
        self.quick = getattr(self.background, "quick_window", None)
        self.overlay = _StartupEntranceOverlay(window, visual)
        self._started = False
        self._finished = False
        self._card_fx_was_suspended = False
        self._hidden_effects: QWidget | None = None

        window.installEventFilter(self)
        self.overlay.finished.connect(self._finish)
        self._freeze_runtime_presentation()

    def _freeze_runtime_presentation(self) -> None:
        clock = getattr(self.window, "_presentation_clock", None)
        suspend_clock = getattr(clock, "suspend", None)
        if callable(suspend_clock):
            suspend_clock("startup")

        if self.quick is not None:
            try:
                self.quick.setProperty("animationRunning", False)
                self.quick.setProperty("pointerX", 0.0)
                self.quick.setProperty("pointerY", 0.0)
                self.quick.setProperty("offsetX", 0.0)
                self.quick.setProperty("offsetY", 0.0)
            except RuntimeError:
                pass

        card_fx = getattr(self.window, "_nekro_card_fx", None)
        suspend_cards = getattr(card_fx, "suspend_for_modal", None)
        if callable(suspend_cards):
            try:
                self._card_fx_was_suspended = bool(getattr(card_fx, "_suspended", False))
                if not self._card_fx_was_suspended:
                    suspend_cards()
            except RuntimeError:
                pass

        effects = getattr(self.window, "_nekro_effects", None)
        if isinstance(effects, QWidget) and effects.isVisible():
            self._hidden_effects = effects
            effects.hide()

    def raise_overlay(self) -> None:
        if not self._finished:
            self.overlay.raise_()

    def start(self) -> None:
        if self._started or self._finished:
            return
        self._started = True
        self.overlay.begin()
        self.raise_overlay()
        QTimer.singleShot(_REVEAL_DELAY_MS, self.overlay.begin_reveal)

    def _restore_runtime_presentation(self) -> None:
        if self._hidden_effects is not None:
            try:
                self._hidden_effects.show()
                self._hidden_effects.raise_()
            except RuntimeError:
                pass
            self._hidden_effects = None

        card_fx = getattr(self.window, "_nekro_card_fx", None)
        resume_cards = getattr(card_fx, "resume_from_modal", None)
        if callable(resume_cards) and not self._card_fx_was_suspended:
            try:
                resume_cards()
            except RuntimeError:
                pass

        clock = getattr(self.window, "_presentation_clock", None)
        resume_clock = getattr(clock, "resume", None)
        if callable(resume_clock):
            resume_clock("startup")

    def _finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        self._restore_runtime_presentation()
        try:
            self.overlay.hide()
            self.overlay.deleteLater()
        except RuntimeError:
            pass
        assistant = getattr(self.window, "_runtime_assistant", None)
        if isinstance(assistant, QWidget):
            try:
                assistant.raise_()
            except RuntimeError:
                pass

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if (
            not self._finished
            and watched is self.window
            and event.type() in {QEvent.Type.Resize, QEvent.Type.Show}
        ):
            QTimer.singleShot(0, self.overlay.resize_to_window)
            QTimer.singleShot(0, self.raise_overlay)
        return False


def install_startup_entrance(
    window: QMainWindow,
    visual: Any,
) -> StartupEntranceController:
    existing = getattr(window, "_startup_entrance", None)
    if isinstance(existing, StartupEntranceController):
        return existing
    controller = StartupEntranceController(window, visual)
    window._startup_entrance = controller  # type: ignore[attr-defined]
    return controller


__all__ = ["StartupEntranceController", "install_startup_entrance"]
