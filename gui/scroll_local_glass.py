"""Viewport-owned QWidget glass for the scrolling Product Source card.

Product Source leaves the independent Quick card scene, but its glass is not frozen
into the card. A transparent QWidget compositor is attached to QScrollArea.viewport()
behind the scrolling page. It samples the already-preblurred Fuji in viewport/window
coordinates and paints only the card shell, while the card's text/controls remain in
the normal scrolling QWidget tree.

Because both the scrolling page and glass compositor use the same QWidget backing
store, card motion no longer crosses the Quick render loop. The Quick window remains
responsible only for the Fuji wallpaper/parallax and for cards that have not yet been
migrated.
"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QEvent, QModelIndex, QObject, QPoint, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPixmap, QRegion
from PySide6.QtWidgets import QFrame, QMainWindow, QScrollArea, QWidget

from .native_background import _GLASS_RADIUS, _NORMAL_GLASS_ALPHA, _OVERSCAN
from .native_visual_style import _CardScaleEffect


_PARALLAX_REPAINT_MS = 16
_DIRTY_PAD_PX = 8


def _ancestor_card(widget: QWidget | None, object_name: str) -> QFrame | None:
    current = widget
    while current is not None:
        if isinstance(current, QFrame) and current.objectName() == object_name:
            return current
        current = current.parentWidget()
    return None


class _ViewportContentScaleEffect(_CardScaleEffect):
    """Keep the existing card-content scale path and invalidate local glass with it."""

    def __init__(self, frame: QFrame, invalidate: Callable[[], None]) -> None:
        super().__init__(frame)
        self._invalidate_glass = invalidate

    def set_scale(self, scale: float) -> None:
        super().set_scale(scale)
        # NativeGlassProxy stores alpha before calling set_scale(), so this also
        # republishes alpha-only hover/press changes when scale itself stays 1.0.
        self._invalidate_glass()


class _ViewportGlassLayer(QWidget):
    """One fixed viewport-space glass compositor behind the scrolling page."""

    def __init__(self, controller: "ScrollLocalGlassController", viewport: QWidget) -> None:
        super().__init__(viewport)
        self.controller = controller
        self._last_card_rect = QRectF()
        self.setObjectName("singlePageViewportGlass")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setGeometry(viewport.rect())
        self.show()

    @staticmethod
    def _padded_region(rect: QRectF) -> QRegion:
        if rect.isEmpty():
            return QRegion()
        padded = rect.adjusted(-_DIRTY_PAD_PX, -_DIRTY_PAD_PX, _DIRTY_PAD_PX, _DIRTY_PAD_PX)
        return QRegion(padded.toAlignedRect())

    def sync_card_geometry(self) -> None:
        current = self.controller.card_rect_in_viewport()
        dirty = self._padded_region(self._last_card_rect)
        dirty = dirty.united(self._padded_region(current))
        self._last_card_rect = QRectF(current)
        if dirty.isEmpty():
            return
        self.update(dirty)

    def refresh_current_card(self) -> None:
        current = self.controller.card_rect_in_viewport()
        self._last_card_rect = QRectF(current)
        dirty = self._padded_region(current)
        if not dirty.isEmpty():
            self.update(dirty)

    def resize_to_viewport(self) -> None:
        viewport = self.parentWidget()
        if viewport is None:
            return
        self.setGeometry(viewport.rect())
        self.sync_card_geometry()

    def paintEvent(self, _event) -> None:  # noqa: ANN001, N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        self.controller.paint_glass(painter)
        painter.end()


class ScrollLocalGlassController(QObject):
    """Own Product Source glass in QScrollArea.viewport() instead of QQuickWindow."""

    def __init__(self, window: QMainWindow, visual: Any, scroll: QScrollArea) -> None:
        super().__init__(window)
        self.window = window
        self.visual = visual
        self.background = getattr(visual, "background", None)
        self.scroll = scroll
        self.viewport = scroll.viewport()
        self.quick = getattr(self.background, "quick_window", None)
        self._source = QPixmap(str(getattr(self.background, "_blur_path", "")))
        self._scaled_item = QPixmap()
        self._scaled_key: tuple[int, int] | None = None
        self._frame: QFrame | None = None
        self._proxy: Any = None
        self._layer: _ViewportGlassLayer | None = None

        self._parallax_timer = QTimer(self)
        self._parallax_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._parallax_timer.setInterval(_PARALLAX_REPAINT_MS)
        self._parallax_timer.timeout.connect(self._parallax_tick)

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

        self._frame = frame
        self._proxy = proxy
        self._detach_from_quick_model(frame)

        layer = _ViewportGlassLayer(self, self.viewport)
        self._layer = layer

        page = scroll.widget()
        if isinstance(page, QWidget) and page.parentWidget() is self.viewport:
            # The transparent scrolling page stays above the glass compositor so
            # its inputs/buttons keep normal paint order and hit testing.
            layer.stackUnder(page)
            page.raise_()

        effect = _ViewportContentScaleEffect(frame, self._invalidate_card_region)
        frame.setGraphicsEffect(effect)
        # NativeGlassProxy is intentionally duck-typed by the card FX controller.
        proxy._scale_effect = effect  # noqa: SLF001

        self.viewport.installEventFilter(self)
        frame.installEventFilter(self)
        self.scroll.verticalScrollBar().valueChanged.connect(self._sync_scroll_position)
        self.quick.widthChanged.connect(self._invalidate_scene_cache)
        self.quick.heightChanged.connect(self._invalidate_scene_cache)

        animation_signal = getattr(self.quick, "animationRunningChanged", None)
        if animation_signal is not None and hasattr(animation_signal, "connect"):
            try:
                animation_signal.connect(self._on_parallax_state_changed)
            except (RuntimeError, TypeError):
                pass

        # Remove the old Quick shell/mask region once. No Quick card geometry is
        # published from this controller during scrolling.
        schedule_mask = getattr(self.background, "schedule_mask_update", None)
        if callable(schedule_mask):
            try:
                schedule_mask()
            except RuntimeError:
                pass

        QTimer.singleShot(0, self._sync_initial_state)

    @property
    def active_count(self) -> int:
        return 1 if self._frame is not None and self._layer is not None else 0

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

    def card_rect_in_viewport(self) -> QRectF:
        frame = self._frame
        proxy = self._proxy
        if frame is None or proxy is None:
            return QRectF()
        try:
            if not frame.isVisibleTo(self.viewport) or frame.width() <= 0 or frame.height() <= 0:
                return QRectF()
            top_left = frame.mapTo(self.viewport, QPoint(0, 0))
            rect = QRectF(
                float(top_left.x()),
                float(top_left.y()),
                float(frame.width()),
                float(frame.height()),
            )
            scale = max(0.96, min(1.04, float(getattr(proxy, "surface_scale", 1.0))))
        except (RuntimeError, TypeError, ValueError):
            return QRectF()

        if abs(scale - 1.0) > 1e-5:
            center = rect.center()
            width = rect.width() * scale
            height = rect.height() * scale
            rect = QRectF(
                center.x() - width * 0.5,
                center.y() - height * 0.5,
                width,
                height,
            )
        return rect

    def paint_glass(self, painter: QPainter) -> None:
        item = self._ensure_scaled_item()
        quick = self.quick
        proxy = self._proxy
        layer = self._layer
        if item is None or quick is None or proxy is None or layer is None:
            return

        card_rect = self.card_rect_in_viewport()
        if card_rect.isEmpty() or not card_rect.intersects(QRectF(layer.rect())):
            return

        try:
            quick_width = float(quick.width())
            quick_height = float(quick.height())
            offset_x, offset_y = self._quick_offset()
            image_x = (quick_width - float(item.width())) * 0.5 + offset_x
            image_y = (quick_height - float(item.height())) * 0.5 + offset_y
            viewport_origin = self.viewport.mapTo(self.window, QPoint(0, 0))
            overlay_alpha = float(getattr(proxy, "overlay_alpha", _NORMAL_GLASS_ALPHA))
        except (RuntimeError, TypeError, ValueError):
            return

        source = QRectF(
            float(viewport_origin.x()) + card_rect.x() - image_x,
            float(viewport_origin.y()) + card_rect.y() - image_y,
            card_rect.width(),
            card_rect.height(),
        )

        painter.save()
        path = QPainterPath()
        path.addRoundedRect(card_rect, _GLASS_RADIUS, _GLASS_RADIUS)
        painter.setClipPath(path)
        painter.drawPixmap(card_rect, item, source)
        painter.fillRect(
            card_rect,
            QColor(
                0,
                0,
                0,
                round(max(_NORMAL_GLASS_ALPHA, min(255.0, overlay_alpha))),
            ),
        )
        painter.restore()

    def _sync_initial_state(self) -> None:
        layer = self._layer
        if layer is None:
            return
        layer.resize_to_viewport()
        layer.sync_card_geometry()

    def _sync_scroll_position(self, _value: int) -> None:
        # QScrollArea has already moved its content widget when valueChanged fires.
        # Repaint only the old/new glass shell regions in the SAME QWidget backing
        # store. No Quick geometry, mask upload or frameSwapped handoff is involved.
        layer = self._layer
        if layer is not None:
            layer.sync_card_geometry()

    def _invalidate_card_region(self) -> None:
        layer = self._layer
        if layer is not None:
            layer.sync_card_geometry()

    def _invalidate_scene_cache(self, *_args: object) -> None:
        self._scaled_item = QPixmap()
        self._scaled_key = None
        layer = self._layer
        if layer is not None:
            layer.resize_to_viewport()

    def _on_parallax_state_changed(self, *_args: object) -> None:
        if self.quick is None:
            return
        try:
            running = bool(self.quick.property("animationRunning"))
        except RuntimeError:
            return
        if running:
            if not self._parallax_timer.isActive():
                self._parallax_timer.start()
        else:
            self._parallax_timer.stop()
            layer = self._layer
            if layer is not None:
                layer.refresh_current_card()

    def _parallax_tick(self) -> None:
        quick = self.quick
        layer = self._layer
        if quick is None or layer is None:
            self._parallax_timer.stop()
            return
        try:
            running = bool(quick.property("animationRunning"))
        except RuntimeError:
            self._parallax_timer.stop()
            return
        layer.refresh_current_card()
        if not running:
            self._parallax_timer.stop()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        event_type = event.type()
        layer = self._layer
        if layer is None:
            return False

        if watched is self.viewport and event_type in {QEvent.Type.Resize, QEvent.Type.Show}:
            layer.resize_to_viewport()
        elif watched is self._frame and event_type in {
            QEvent.Type.Move,
            QEvent.Type.Resize,
            QEvent.Type.Show,
            QEvent.Type.Hide,
            QEvent.Type.LayoutRequest,
        }:
            layer.sync_card_geometry()
        return False


def install_scroll_local_glass(
    window: QMainWindow,
    visual: Any,
) -> ScrollLocalGlassController | None:
    """Install Product Source glass as a viewport-space QWidget compositor."""

    scroll = getattr(window, "_single_page_scroll", None)
    if not isinstance(scroll, QScrollArea):
        return None

    controller = ScrollLocalGlassController(window, visual, scroll)
    if controller.active_count <= 0:
        controller.deleteLater()
        return None

    window._scroll_local_glass = controller  # type: ignore[attr-defined]
    return controller
