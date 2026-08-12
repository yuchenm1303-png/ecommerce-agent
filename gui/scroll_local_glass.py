"""Single-render-domain glass experiment for the scrolling Product Source card.

Only Product Source leaves the independent Quick card scene. Its blurred Fuji
sample, tint and QWidget contents are composited by one QGraphicsEffect attached to
the card itself. During scrolling the cached composite moves with QScrollArea; no
per-scroll repaint, Quick geometry publication or frameSwapped callback participates.
The blurred sample is refreshed once after scrolling settles.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QEvent, QModelIndex, QObject, QPoint, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QFrame, QGraphicsEffect, QMainWindow, QScrollArea, QWidget

from .native_background import _GLASS_RADIUS, _NORMAL_GLASS_ALPHA, _OVERSCAN


_EFFECT_BOUND_SCALE = 1.04
_CONTENT_EDGE_STEP_PX = 0.18
_NORMAL_SCALE_EPSILON = 1e-5
_SCROLL_SETTLE_MS = 84


def _ancestor_card(widget: QWidget | None, object_name: str) -> QFrame | None:
    current = widget
    while current is not None:
        if isinstance(current, QFrame) and current.objectName() == object_name:
            return current
        current = current.parentWidget()
    return None


class _SingleDomainGlassEffect(QGraphicsEffect):
    """Composite cached glass + live card contents in one QWidget render domain."""

    def __init__(self, frame: QFrame, proxy: Any) -> None:
        super().__init__(frame)
        self.frame = frame
        self.proxy = proxy
        self._scale = 1.0
        self._frozen = False
        self._freeze_requested = False
        self._frozen_source: QPixmap | None = None
        self._frozen_offset = QPoint()
        self._backdrop = QPixmap()
        self._last_alpha = _NORMAL_GLASS_ALPHA
        # The effect stays enabled at rest because it now owns the card backdrop,
        # not merely the temporary hover scale.
        self.setEnabled(True)

    @property
    def scale(self) -> float:
        return self._scale

    @property
    def frozen(self) -> bool:
        return self._frozen

    def _overlay_alpha(self) -> float:
        try:
            alpha = float(getattr(self.proxy, "overlay_alpha", _NORMAL_GLASS_ALPHA))
        except (RuntimeError, TypeError, ValueError):
            alpha = _NORMAL_GLASS_ALPHA
        return max(_NORMAL_GLASS_ALPHA, min(255.0, alpha))

    def set_backdrop(self, pixmap: QPixmap) -> None:
        if pixmap.isNull():
            return
        if not self._backdrop.isNull() and self._backdrop.cacheKey() == pixmap.cacheKey():
            return
        self._backdrop = QPixmap(pixmap)
        self.update()

    def _clear_frozen_source(self) -> None:
        self._frozen_source = None
        self._frozen_offset = QPoint()

    def _content_span(self) -> float:
        try:
            return max(1.0, float(self.frame.width()), float(self.frame.height()))
        except (RuntimeError, TypeError, ValueError):
            return 1.0

    def set_frozen(self, frozen: bool) -> None:
        frozen = bool(frozen)
        if frozen == self._frozen:
            return
        self._frozen = frozen
        self._freeze_requested = frozen
        self._clear_frozen_source()
        self.update()

    def set_scale(self, scale: float) -> None:
        requested = max(0.96, min(_EFFECT_BOUND_SCALE, float(scale)))
        exact_rest = abs(requested - 1.0) <= _NORMAL_SCALE_EPSILON
        if exact_rest:
            requested = 1.0
        else:
            edge_delta_px = self._content_span() * abs(requested - self._scale) * 0.5
            if edge_delta_px < _CONTENT_EDGE_STEP_PX:
                requested = self._scale

        alpha = self._overlay_alpha()
        alpha_changed = abs(alpha - self._last_alpha) >= 0.1
        self._last_alpha = alpha

        if abs(requested - self._scale) <= _NORMAL_SCALE_EPSILON:
            if exact_rest and self._frozen:
                self._frozen = False
                self._freeze_requested = False
                self._clear_frozen_source()
            if alpha_changed:
                self.update()
            return

        self._scale = requested
        if exact_rest:
            self._frozen = False
            self._freeze_requested = False
            self._clear_frozen_source()
        self.update()

    def boundingRectFor(self, source_rect: QRectF) -> QRectF:  # noqa: N802
        center = source_rect.center()
        half_w = source_rect.width() * _EFFECT_BOUND_SCALE * 0.5
        half_h = source_rect.height() * _EFFECT_BOUND_SCALE * 0.5
        return QRectF(
            center.x() - half_w,
            center.y() - half_h,
            half_w * 2.0,
            half_h * 2.0,
        )

    def _current_composite(self) -> tuple[QPixmap | None, QPoint]:
        if (
            self._frozen
            and not self._freeze_requested
            and self._frozen_source is not None
            and not self._frozen_source.isNull()
        ):
            return self._frozen_source, self._frozen_offset

        offset = QPoint()
        pixmap = self.sourcePixmap(
            Qt.CoordinateSystem.LogicalCoordinates,
            offset,
            QGraphicsEffect.PixmapPadMode.NoPad,
        )
        if pixmap.isNull():
            return None, QPoint()

        if self._frozen:
            self._frozen_source = pixmap
            self._frozen_offset = QPoint(offset)
            self._freeze_requested = False
        return pixmap, offset

    def _draw_backdrop(self, painter: QPainter, rect: QRectF) -> None:
        if rect.isEmpty():
            return
        painter.save()
        path = QPainterPath()
        path.addRoundedRect(rect, _GLASS_RADIUS, _GLASS_RADIUS)
        painter.setClipPath(path)
        if not self._backdrop.isNull():
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.drawPixmap(rect, self._backdrop, QRectF(self._backdrop.rect()))
        painter.fillRect(rect, QColor(0, 0, 0, round(self._overlay_alpha())))
        painter.restore()

    def draw(self, painter: QPainter) -> None:  # type: ignore[override]
        source_rect = self.sourceBoundingRect(Qt.CoordinateSystem.LogicalCoordinates)
        scale = self._scale

        if abs(scale - 1.0) <= 1e-4:
            self._draw_backdrop(painter, source_rect)
            self.drawSource(painter)
            return

        pixmap, offset = self._current_composite()
        if pixmap is None:
            self._draw_backdrop(painter, source_rect)
            self.drawSource(painter)
            return

        center = source_rect.center()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.translate(center)
        painter.scale(scale, scale)
        painter.translate(-center)
        self._draw_backdrop(painter, source_rect)
        painter.drawPixmap(offset, pixmap)
        painter.restore()


class ScrollLocalGlassController(QObject):
    """Keep only Product Source on the single-domain cached-glass path."""

    def __init__(self, window: QMainWindow, visual: Any, scroll: QScrollArea) -> None:
        super().__init__(window)
        self.window = window
        self.visual = visual
        self.background = getattr(visual, "background", None)
        self.scroll = scroll
        self.quick = getattr(self.background, "quick_window", None)
        self._source = QPixmap(str(getattr(self.background, "_blur_path", "")))
        self._scaled_item = QPixmap()
        self._scaled_key: tuple[int, int] | None = None
        self._frame: QFrame | None = None
        self._proxy: Any = None
        self._effect: _SingleDomainGlassEffect | None = None
        self._scrolling = False

        self._settle_timer = QTimer(self)
        self._settle_timer.setSingleShot(True)
        self._settle_timer.setInterval(_SCROLL_SETTLE_MS)
        self._settle_timer.timeout.connect(self._finish_scroll)

        if self.background is None or self.quick is None or self._source.isNull():
            return

        url_input = getattr(self.window, "url_input", None)
        frame = _ancestor_card(url_input if isinstance(url_input, QWidget) else None, "heroCard")
        if frame is None:
            return

        surface_for = getattr(self.visual, "surface_for", None)
        proxy = surface_for(frame) if callable(surface_for) else None
        if proxy is None:
            return

        backdrop = self._build_backdrop(frame)
        if backdrop is None or backdrop.isNull():
            return

        effect = _SingleDomainGlassEffect(frame, proxy)
        effect.set_backdrop(backdrop)
        frame.setGraphicsEffect(effect)
        # NativeGlassProxy is intentionally duck-typed by the interaction layer.
        # Re-point its scale effect so hover/press keeps exactly the existing motion.
        proxy._scale_effect = effect  # noqa: SLF001

        self._frame = frame
        self._proxy = proxy
        self._effect = effect
        frame.installEventFilter(self)
        self._detach_from_quick_model(frame)

        # During scroll this callback ONLY restarts an idle timer. No QWidget
        # repaint, no Quick geometry sync and no render-thread callback runs.
        self.scroll.verticalScrollBar().valueChanged.connect(self._mark_scrolling)
        self.quick.widthChanged.connect(self._invalidate_scene_cache)
        self.quick.heightChanged.connect(self._invalidate_scene_cache)

        # A parallax cycle may change the correct blurred sample while idle. Refresh
        # only once after that cycle ends; never listen to frameSwapped.
        animation_signal = getattr(self.quick, "animationRunningChanged", None)
        if animation_signal is not None and hasattr(animation_signal, "connect"):
            try:
                animation_signal.connect(self._on_parallax_state_changed)
            except (RuntimeError, TypeError):
                pass

        try:
            self.background.schedule_mask_update()
        except RuntimeError:
            pass

    @property
    def active_count(self) -> int:
        return 1 if self._frame is not None and self._effect is not None else 0

    def _detach_from_quick_model(self, frame: QFrame) -> None:
        model = getattr(self.background, "card_model", None)
        cards = getattr(model, "cards", None)
        states = getattr(model, "_states", None)
        rows = getattr(model, "_rows", None)
        if model is None or not isinstance(cards, list) or not isinstance(states, list) or not isinstance(rows, dict):
            return

        row = rows.get(frame)
        if row is None:
            return
        row = int(row)
        if row < 0 or row >= len(cards):
            return

        model.beginRemoveRows(QModelIndex(), row, row)
        try:
            del cards[row]
            del states[row]
        finally:
            model.endRemoveRows()
        model._rows = {card: index for index, card in enumerate(cards)}  # noqa: SLF001

    def _ensure_scaled_item(self) -> QPixmap | None:
        quick = self.quick
        if quick is None:
            return None
        try:
            width = max(1, round(float(quick.width()) * _OVERSCAN))
            height = max(1, round(float(quick.height()) * _OVERSCAN))
        except RuntimeError:
            return None

        key = (width, height)
        if self._scaled_key == key and not self._scaled_item.isNull():
            return self._scaled_item

        scaled = self._source.scaled(
            width,
            height,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        if scaled.isNull():
            return None

        crop_x = max(0, (scaled.width() - width) // 2)
        crop_y = max(0, (scaled.height() - height) // 2)
        self._scaled_item = scaled.copy(crop_x, crop_y, width, height)
        self._scaled_key = key
        return self._scaled_item

    def _quick_offset(self) -> tuple[float, float]:
        quick = self.quick
        if quick is None:
            return 0.0, 0.0
        try:
            return float(quick.property("offsetX") or 0.0), float(quick.property("offsetY") or 0.0)
        except (RuntimeError, TypeError, ValueError):
            return 0.0, 0.0

    def _build_backdrop(self, frame: QFrame) -> QPixmap | None:
        item = self._ensure_scaled_item()
        quick = self.quick
        if item is None or quick is None or frame.width() <= 0 or frame.height() <= 0:
            return None

        try:
            quick_width = float(quick.width())
            quick_height = float(quick.height())
            offset_x, offset_y = self._quick_offset()
            image_x = (quick_width - float(item.width())) * 0.5 + offset_x
            image_y = (quick_height - float(item.height())) * 0.5 + offset_y
            top_left = frame.mapTo(self.window, QPoint(0, 0))
            dpr = max(1.0, float(frame.devicePixelRatioF()))
        except (RuntimeError, TypeError, ValueError):
            return None

        source = QRectF(
            float(top_left.x()) - image_x,
            float(top_left.y()) - image_y,
            float(frame.width()),
            float(frame.height()),
        )
        result = QPixmap(
            max(1, round(frame.width() * dpr)),
            max(1, round(frame.height() * dpr)),
        )
        result.setDevicePixelRatio(dpr)
        result.fill(Qt.GlobalColor.transparent)
        painter = QPainter(result)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawPixmap(QRectF(0.0, 0.0, float(frame.width()), float(frame.height())), item, source)
        painter.end()
        return result

    def _refresh_backdrop(self) -> None:
        frame = self._frame
        effect = self._effect
        if frame is None or effect is None:
            return
        backdrop = self._build_backdrop(frame)
        if backdrop is not None and not backdrop.isNull():
            effect.set_backdrop(backdrop)

    def _mark_scrolling(self, _value: int) -> None:
        self._scrolling = True
        self._settle_timer.start()

    def _finish_scroll(self) -> None:
        self._scrolling = False
        self._refresh_backdrop()

    def _invalidate_scene_cache(self, *_args: object) -> None:
        self._scaled_item = QPixmap()
        self._scaled_key = None
        if not self._scrolling:
            QTimer.singleShot(0, self._refresh_backdrop)

    def _on_parallax_state_changed(self, *_args: object) -> None:
        if self._scrolling or self.quick is None:
            return
        try:
            running = bool(self.quick.property("animationRunning"))
        except RuntimeError:
            return
        if not running:
            QTimer.singleShot(0, self._refresh_backdrop)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is not self._frame:
            return False
        if event.type() in {QEvent.Type.Resize, QEvent.Type.Show} and not self._scrolling:
            QTimer.singleShot(0, self._refresh_backdrop)
        return False


def install_scroll_local_glass(window: QMainWindow, visual: Any) -> ScrollLocalGlassController | None:
    """Install the one-card Product Source single-domain A/B experiment."""

    scroll = getattr(window, "_single_page_scroll", None)
    if not isinstance(scroll, QScrollArea):
        return None

    controller = ScrollLocalGlassController(window, visual, scroll)
    if controller.active_count <= 0:
        controller.deleteLater()
        return None

    window._scroll_local_glass = controller  # type: ignore[attr-defined]
    return controller
