from __future__ import annotations

import math
import time
from typing import Any

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QPointF,
    QRectF,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QMainWindow, QWidget

from .native_background import _OVERSCAN


_FRAME_MS = 16
_CAPTURE_DELAY_MS = 48
_UI_FADE_MS = 620
_CURTAIN_DELAY_MS = 0
_CURTAIN_MS = 500
_BACKGROUND_DELAY_MS = 120
_BACKGROUND_MS = 760
_UI_SCALE_DELAY_MS = 0
_UI_SCALE_MS = 0
_TOTAL_MS = 1000

_BG_START_SCALE = 1.16
_UI_START_SCALE = 1.0
_BG_START_DIM = 0.46
_CURTAIN_FRACTION = 0.51
_CURTAIN_COLOR = QColor("#333333")
_LIVE_REVEAL_DELAY_MS = 160


def _curve(c1x: float, c1y: float, c2x: float, c2y: float) -> QEasingCurve:
    curve = QEasingCurve(QEasingCurve.Type.BezierSpline)
    curve.addCubicBezierSegment(
        QPointF(c1x, c1y),
        QPointF(c2x, c2y),
        QPointF(1.0, 1.0),
    )
    return curve


_CURTAIN_EASE = _curve(0.645, 0.045, 0.355, 1.0)
_SOFT_EASE = _curve(0.25, 0.46, 0.45, 0.94)
_REVEAL_EASE = QEasingCurve(QEasingCurve.Type.OutCubic)


def _unit_progress(elapsed_ms: float, delay_ms: float, duration_ms: float) -> float:
    if elapsed_ms <= delay_ms:
        return 0.0
    if duration_ms <= 0:
        return 1.0
    return max(0.0, min(1.0, (elapsed_ms - delay_ms) / duration_ms))


class _StartupEntranceOverlay(QWidget):
    """Startup cover that never rasterizes or reconstructs the application UI.

    The previous entrance rendered the live QWidget tree into a pixmap and rebuilt
    glass cards from a separately sampled geometry set. During the first native
    maximize/layout pass those two geometry sources could disagree, so the animation
    showed a provisional card layout and then snapped to the real one at handoff.

    This overlay owns only wallpaper + curtains. The real QWidget/Quick hierarchy is
    kept alive underneath and is revealed directly once its final geometry is stable.
    Consequently there is only one source of truth for card positions at startup.
    """

    finished = Signal()

    def __init__(self, window: QMainWindow, visual: Any) -> None:
        super().__init__(window)
        self.window = window
        self.background = getattr(visual, "background", None)
        self._sharp_source = QPixmap(str(getattr(self.background, "_sharp_path", "")))
        self._blur_source = QPixmap(str(getattr(self.background, "_blur_path", "")))
        self._sharp_scene = QPixmap()
        self._blur_scene = QPixmap()
        self._scene_key: tuple[int, int] | None = None
        self._reveal_started_s: float | None = None

        self.setObjectName("startupEntranceOverlay")
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setGeometry(window.rect())
        self.show()
        self.raise_()

        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(_FRAME_MS)
        self._timer.timeout.connect(self._tick)

    def begin(self) -> None:
        if not self._timer.isActive():
            self._timer.start()
        self.update()

    def begin_reveal(self) -> None:
        if self._reveal_started_s is None:
            self._reveal_started_s = time.perf_counter()
            self.update()

    def resize_to_window(self) -> None:
        self.setGeometry(self.window.rect())
        self._sharp_scene = QPixmap()
        self._blur_scene = QPixmap()
        self._scene_key = None
        self.update()

    def _tick(self) -> None:
        self.update()
        if self._reveal_started_s is None:
            return
        elapsed_ms = (time.perf_counter() - self._reveal_started_s) * 1000.0
        if elapsed_ms >= _TOTAL_MS:
            self._timer.stop()
            self.finished.emit()

    @staticmethod
    def _cover(source: QPixmap, width: int, height: int) -> QPixmap:
        if source.isNull() or width <= 0 or height <= 0:
            return QPixmap()
        scaled = source.scaled(
            width,
            height,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        if scaled.isNull():
            return QPixmap()
        crop_x = max(0, (scaled.width() - width) // 2)
        crop_y = max(0, (scaled.height() - height) // 2)
        return scaled.copy(crop_x, crop_y, width, height)

    def _scene_images(self) -> tuple[QPixmap | None, QPixmap | None]:
        width = max(1, round(float(self.width()) * _OVERSCAN))
        height = max(1, round(float(self.height()) * _OVERSCAN))
        key = (width, height)
        if self._scene_key != key or self._sharp_scene.isNull() or self._blur_scene.isNull():
            self._sharp_scene = self._cover(self._sharp_source, width, height)
            self._blur_scene = self._cover(self._blur_source, width, height)
            self._scene_key = key
        sharp = None if self._sharp_scene.isNull() else self._sharp_scene
        blur = None if self._blur_scene.isNull() else self._blur_scene
        return sharp, blur

    def _elapsed_ms(self) -> float:
        if self._reveal_started_s is None:
            return 0.0
        return max(0.0, (time.perf_counter() - self._reveal_started_s) * 1000.0)

    def _background_state(self, elapsed_ms: float) -> tuple[float, float, float]:
        raw = _unit_progress(elapsed_ms, _BACKGROUND_DELAY_MS, _BACKGROUND_MS)
        eased = float(_SOFT_EASE.valueForProgress(raw))
        return (
            _BG_START_SCALE + (1.0 - _BG_START_SCALE) * eased,
            1.0 - eased,
            _BG_START_DIM * (1.0 - eased),
        )

    def _cover_opacity(self, elapsed_ms: float) -> float:
        raw = _unit_progress(elapsed_ms, _LIVE_REVEAL_DELAY_MS, _UI_FADE_MS)
        eased = float(_REVEAL_EASE.valueForProgress(raw))
        return max(0.0, min(1.0, 1.0 - eased))

    def _background_target(self, scene: QPixmap, scale: float) -> QRectF:
        center = QRectF(self.rect()).center()
        width = float(scene.width()) * scale
        height = float(scene.height()) * scale
        return QRectF(
            center.x() - width * 0.5,
            center.y() - height * 0.5,
            width,
            height,
        )

    def _paint_background(self, painter: QPainter, elapsed_ms: float) -> None:
        sharp, blur = self._scene_images()
        scale, blur_mix, dim = self._background_state(elapsed_ms)
        cover_opacity = self._cover_opacity(elapsed_ms)
        if cover_opacity <= 0.001:
            return

        painter.save()
        painter.setOpacity(cover_opacity)
        if sharp is None:
            painter.fillRect(self.rect(), QColor("#17263a"))
            painter.restore()
            return

        target = self._background_target(sharp, scale)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawPixmap(target, sharp, QRectF(sharp.rect()))
        if blur is not None and blur_mix > 0.001:
            painter.save()
            painter.setOpacity(blur_mix)
            painter.drawPixmap(target, blur, QRectF(blur.rect()))
            painter.restore()
        if dim > 0.001:
            painter.fillRect(
                self.rect(),
                QColor(0, 0, 0, round(max(0.0, min(1.0, dim)) * 255.0)),
            )
        painter.restore()

    def _paint_curtains(self, painter: QPainter, elapsed_ms: float) -> None:
        raw = (
            0.0
            if self._reveal_started_s is None
            else _unit_progress(elapsed_ms, _CURTAIN_DELAY_MS, _CURTAIN_MS)
        )
        progress = float(_CURTAIN_EASE.valueForProgress(raw))
        width = float(self.width())
        height = float(self.height())
        panel_w = math.ceil(width * _CURTAIN_FRACTION)
        displacement = panel_w * progress
        painter.fillRect(
            QRectF(-displacement, 0.0, panel_w + 1.0, height),
            _CURTAIN_COLOR,
        )
        painter.fillRect(
            QRectF(width - panel_w + displacement, 0.0, panel_w + 1.0, height),
            _CURTAIN_COLOR,
        )

    def paintEvent(self, _event) -> None:  # noqa: ANN001, N802
        painter = QPainter(self)
        elapsed_ms = self._elapsed_ms()
        self._paint_background(painter, elapsed_ms)
        self._paint_curtains(painter, elapsed_ms)
        painter.end()


class StartupEntranceController(QObject):
    """Reveal the already-settled live interface instead of replaying a UI snapshot."""

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
        QTimer.singleShot(_CAPTURE_DELAY_MS, self._capture_and_reveal)

    def _capture_and_reveal(self) -> None:
        """Compatibility boundary: no QWidget capture is performed anymore."""

        if self._finished:
            return
        self.overlay.begin_reveal()

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
