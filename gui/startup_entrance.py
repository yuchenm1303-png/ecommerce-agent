from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QPoint,
    QPointF,
    QRectF,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPixmap, QRegion
from PySide6.QtWidgets import QFrame, QMainWindow, QWidget

from .native_background import _GLASS_RADIUS, _NORMAL_GLASS_ALPHA, _OVERSCAN


_FRAME_MS = 16
_CAPTURE_DELAY_MS = 48

# Reference entrance with the loader phase removed. These are the original
# choreography offsets rebased by -300 ms: curtains start immediately after the
# first stable GUI composite is captured, while background/UI keep their relative
# 150/200 ms stagger.
_UI_FADE_MS = 300
_CURTAIN_DELAY_MS = 0
_CURTAIN_MS = 500
_BACKGROUND_DELAY_MS = 150
_BACKGROUND_MS = 800
_UI_SCALE_DELAY_MS = 200
_UI_SCALE_MS = 650
_TOTAL_MS = 1000

_BG_START_SCALE = 1.60
_UI_START_SCALE = 1.20
_BG_START_DIM = 0.70
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
_SOFT_EASE = _curve(0.25, 0.46, 0.45, 0.94)
_OPACITY_EASE = QEasingCurve(QEasingCurve.Type.InOutQuad)


def _unit_progress(elapsed_ms: float, delay_ms: float, duration_ms: float) -> float:
    if elapsed_ms <= delay_ms:
        return 0.0
    if duration_ms <= 0:
        return 1.0
    return max(0.0, min(1.0, (elapsed_ms - delay_ms) / duration_ms))


def _scaled_about(rect: QRectF, scale: float, center: QPointF) -> QRectF:
    left = center.x() + (rect.left() - center.x()) * scale
    top = center.y() + (rect.top() - center.y()) * scale
    right = center.x() + (rect.right() - center.x()) * scale
    bottom = center.y() + (rect.bottom() - center.y()) * scale
    return QRectF(left, top, right - left, bottom - top)


@dataclass(slots=True)
class _GlassRecord:
    rect: QRectF
    clip_rect: QRectF
    alpha: float


class _StartupEntranceOverlay(QWidget):
    """One-shot curtain + camera entrance inspired by the reference page.

    There is deliberately no loading spinner/text phase. Before the first stable
    GUI composite is captured, the two 51% #333 curtains simply cover the window.
    As soon as that composite exists, the curtains open immediately while the
    wallpaper focus/scale and frozen UI scale run on the original relative timing.
    """

    finished = Signal()

    def __init__(self, window: QMainWindow, visual: Any) -> None:
        super().__init__(window)
        self.window = window
        self.visual = visual
        self.background = getattr(visual, "background", None)
        self._sharp_source = QPixmap(str(getattr(self.background, "_sharp_path", "")))
        self._blur_source = QPixmap(str(getattr(self.background, "_blur_path", "")))
        self._sharp_scene = QPixmap()
        self._blur_scene = QPixmap()
        self._scene_key: tuple[int, int] | None = None
        self._ui_snapshot = QPixmap()
        self._ui_rect = QRectF()
        self._glass_records: list[_GlassRecord] = []
        self._reveal_started_s: float | None = None

        self.setObjectName("startupEntranceOverlay")
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setGeometry(window.rect())
        self.show()
        self.raise_()

        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(_FRAME_MS)
        self._timer.timeout.connect(self._tick)

    def set_snapshot(
        self,
        pixmap: QPixmap,
        rect: QRectF,
        glass_records: list[_GlassRecord],
    ) -> None:
        self._ui_snapshot = QPixmap(pixmap)
        self._ui_rect = QRectF(rect)
        self._glass_records = list(glass_records)
        self.update()

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
        if (
            self._scene_key != key
            or self._sharp_scene.isNull()
            or self._blur_scene.isNull()
        ):
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
        scale = _BG_START_SCALE + (1.0 - _BG_START_SCALE) * eased
        blur_mix = 1.0 - eased
        dim = _BG_START_DIM * (1.0 - eased)
        return scale, blur_mix, dim

    def _ui_state(self, elapsed_ms: float) -> tuple[float, float]:
        opacity_raw = _unit_progress(elapsed_ms, 0.0, _UI_FADE_MS)
        opacity = float(_OPACITY_EASE.valueForProgress(opacity_raw))
        scale_raw = _unit_progress(elapsed_ms, _UI_SCALE_DELAY_MS, _UI_SCALE_MS)
        scale_eased = float(_SOFT_EASE.valueForProgress(scale_raw))
        scale = _UI_START_SCALE + (1.0 - _UI_START_SCALE) * scale_eased
        return scale, opacity

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

    def _paint_background(
        self,
        painter: QPainter,
        elapsed_ms: float,
    ) -> tuple[QPixmap | None, QRectF]:
        sharp, blur = self._scene_images()
        scale, blur_mix, dim = self._background_state(elapsed_ms)
        if sharp is None:
            painter.fillRect(self.rect(), QColor("#17263a"))
            return blur, QRectF(self.rect())

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
        return blur, target

    @staticmethod
    def _mapped_source_rect(
        target: QRectF,
        background_target: QRectF,
        source: QPixmap,
    ) -> QRectF:
        if (
            source.isNull()
            or background_target.width() <= 0.0
            or background_target.height() <= 0.0
        ):
            return QRectF()
        sx = float(source.width()) / background_target.width()
        sy = float(source.height()) / background_target.height()
        return QRectF(
            (target.x() - background_target.x()) * sx,
            (target.y() - background_target.y()) * sy,
            target.width() * sx,
            target.height() * sy,
        )

    def _paint_glass_records(
        self,
        painter: QPainter,
        blur: QPixmap | None,
        background_target: QRectF,
        ui_scale: float,
        ui_center: QPointF,
    ) -> None:
        if blur is None:
            return
        for record in self._glass_records:
            target = _scaled_about(record.rect, ui_scale, ui_center)
            clip = _scaled_about(record.clip_rect, ui_scale, ui_center)
            if (
                target.isEmpty()
                or clip.isEmpty()
                or not target.intersects(QRectF(self.rect()))
            ):
                continue
            source = self._mapped_source_rect(target, background_target, blur)
            if source.isEmpty():
                continue

            painter.save()
            painter.setClipRect(clip)
            path = QPainterPath()
            path.addRoundedRect(
                target,
                _GLASS_RADIUS * ui_scale,
                _GLASS_RADIUS * ui_scale,
            )
            painter.setClipPath(path, Qt.ClipOperation.IntersectClip)
            painter.drawPixmap(target, blur, source)
            painter.fillRect(
                target,
                QColor(
                    0,
                    0,
                    0,
                    round(max(_NORMAL_GLASS_ALPHA, min(255.0, record.alpha))),
                ),
            )
            painter.restore()

    def _paint_ui(
        self,
        painter: QPainter,
        elapsed_ms: float,
        blur: QPixmap | None,
        background_target: QRectF,
    ) -> None:
        if self._ui_snapshot.isNull() or self._ui_rect.isEmpty():
            return
        ui_scale, opacity = self._ui_state(elapsed_ms)
        if opacity <= 0.001:
            return

        center = self._ui_rect.center()
        target = _scaled_about(self._ui_rect, ui_scale, center)
        painter.save()
        painter.setOpacity(opacity)
        self._paint_glass_records(
            painter,
            blur,
            background_target,
            ui_scale,
            center,
        )
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawPixmap(target, self._ui_snapshot, QRectF(self._ui_snapshot.rect()))
        painter.restore()

    def _paint_curtains(self, painter: QPainter, elapsed_ms: float) -> None:
        if self._reveal_started_s is None:
            progress = 0.0
        else:
            raw = _unit_progress(elapsed_ms, _CURTAIN_DELAY_MS, _CURTAIN_MS)
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
        blur, background_target = self._paint_background(painter, elapsed_ms)
        self._paint_ui(painter, elapsed_ms, blur, background_target)
        self._paint_curtains(painter, elapsed_ms)
        painter.end()


class StartupEntranceController(QObject):
    """Coordinate the one-shot entrance without changing runtime UI ownership."""

    def __init__(self, window: QMainWindow, visual: Any) -> None:
        super().__init__(window)
        self.window = window
        self.visual = visual
        self.background = getattr(visual, "background", None)
        self.quick = getattr(self.background, "quick_window", None)
        self.overlay = _StartupEntranceOverlay(window, visual)
        self._started = False
        self._finished = False
        self._pointer_was_active = False
        self._card_fx_was_suspended = False
        self._hidden_effects: QWidget | None = None
        self._hidden_local_glass: QWidget | None = None

        window.installEventFilter(self)
        self.overlay.finished.connect(self._finish)
        self._freeze_runtime_presentation()

    def _freeze_runtime_presentation(self) -> None:
        pointer_timer = getattr(self.background, "_pointer_timer", None)
        if pointer_timer is not None:
            try:
                self._pointer_was_active = bool(pointer_timer.isActive())
                pointer_timer.stop()
            except RuntimeError:
                self._pointer_was_active = False

        quick = self.quick
        if quick is not None:
            try:
                quick.setProperty("animationRunning", False)
                quick.setProperty("pointerX", 0.0)
                quick.setProperty("pointerY", 0.0)
                quick.setProperty("offsetX", 0.0)
                quick.setProperty("offsetY", 0.0)
            except RuntimeError:
                pass

        card_fx = getattr(self.window, "_nekro_card_fx", None)
        suspend = getattr(card_fx, "suspend_for_modal", None)
        if callable(suspend):
            try:
                self._card_fx_was_suspended = bool(
                    getattr(card_fx, "_suspended", False)
                )
                if not self._card_fx_was_suspended:
                    suspend()
            except RuntimeError:
                pass

        effects = getattr(self.window, "_nekro_effects", None)
        if isinstance(effects, QWidget) and effects.isVisible():
            self._hidden_effects = effects
            effects.hide()

        local_glass = getattr(self.window, "_scroll_local_glass", None)
        layer = getattr(local_glass, "_layer", None)
        if isinstance(layer, QWidget) and layer.isVisible():
            self._hidden_local_glass = layer
            layer.hide()

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

    def _visible_clip(self, frame: QFrame) -> tuple[QRectF, QRectF] | None:
        try:
            if (
                not frame.isVisibleTo(self.window)
                or frame.width() <= 0
                or frame.height() <= 0
            ):
                return None
            top_left = frame.mapTo(self.window, QPoint(0, 0))
            rect = QRectF(
                float(top_left.x()),
                float(top_left.y()),
                float(frame.width()),
                float(frame.height()),
            )
            clip = QRectF(self.window.rect())
            ancestor = frame.parentWidget()
            while ancestor is not None:
                ancestor_top_left = ancestor.mapTo(self.window, QPoint(0, 0))
                ancestor_rect = QRectF(
                    float(ancestor_top_left.x()),
                    float(ancestor_top_left.y()),
                    float(ancestor.width()),
                    float(ancestor.height()),
                )
                clip = clip.intersected(ancestor_rect)
                if clip.isEmpty() or ancestor is self.window:
                    break
                ancestor = ancestor.parentWidget()
            if rect.intersected(clip).isEmpty():
                return None
            return rect, clip
        except RuntimeError:
            return None

    def _snapshot_glass_records(self) -> list[_GlassRecord]:
        records: list[_GlassRecord] = []
        glass = getattr(self.visual, "_glass", None)
        if not isinstance(glass, dict):
            return records
        for frame, proxy in glass.items():
            if not isinstance(frame, QFrame):
                continue
            geometry = self._visible_clip(frame)
            if geometry is None:
                continue
            rect, clip = geometry
            try:
                alpha = float(
                    getattr(proxy, "overlay_alpha", _NORMAL_GLASS_ALPHA)
                )
            except (RuntimeError, TypeError, ValueError):
                alpha = _NORMAL_GLASS_ALPHA
            records.append(
                _GlassRecord(
                    rect=rect,
                    clip_rect=clip,
                    alpha=alpha,
                )
            )
        return records

    def _capture_central(self) -> tuple[QPixmap, QRectF]:
        central = self.window.centralWidget()
        if central is None or central.width() <= 0 or central.height() <= 0:
            return QPixmap(), QRectF()
        dpr = max(1.0, float(central.devicePixelRatioF()))
        pixel_w = max(1, round(float(central.width()) * dpr))
        pixel_h = max(1, round(float(central.height()) * dpr))
        pixmap = QPixmap(pixel_w, pixel_h)
        pixmap.setDevicePixelRatio(dpr)
        pixmap.fill(Qt.GlobalColor.transparent)
        central.render(
            pixmap,
            QPoint(0, 0),
            QRegion(),
            QWidget.RenderFlag.DrawWindowBackground | QWidget.RenderFlag.DrawChildren,
        )
        top_left = central.mapTo(self.window, QPoint(0, 0))
        rect = QRectF(
            float(top_left.x()),
            float(top_left.y()),
            float(central.width()),
            float(central.height()),
        )
        return pixmap, rect

    def _capture_and_reveal(self) -> None:
        if self._finished:
            return
        try:
            pixmap, rect = self._capture_central()
            records = self._snapshot_glass_records()
            self.overlay.set_snapshot(pixmap, rect, records)
        except RuntimeError:
            pass
        self.overlay.begin_reveal()

    def _restore_runtime_presentation(self) -> None:
        if self._hidden_local_glass is not None:
            try:
                self._hidden_local_glass.show()
            except RuntimeError:
                pass
            self._hidden_local_glass = None

        if self._hidden_effects is not None:
            try:
                self._hidden_effects.show()
                self._hidden_effects.raise_()
            except RuntimeError:
                pass
            self._hidden_effects = None

        card_fx = getattr(self.window, "_nekro_card_fx", None)
        resume = getattr(card_fx, "resume_from_modal", None)
        if callable(resume) and not self._card_fx_was_suspended:
            try:
                resume()
            except RuntimeError:
                pass

        pointer_timer = getattr(self.background, "_pointer_timer", None)
        if self._pointer_was_active and pointer_timer is not None:
            try:
                if not pointer_timer.isActive():
                    pointer_timer.start()
            except RuntimeError:
                pass
        if self.background is not None:
            try:
                self.background._last_pointer_norm = None  # noqa: SLF001
            except (AttributeError, RuntimeError):
                pass

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
