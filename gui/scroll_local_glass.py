"""Viewport-owned QWidget glass for every scrolling Single-page card.

All registered glass cards inside ``singlePageScrollContent`` leave the independent
Quick card scene. One transparent QWidget compositor is attached to
``QScrollArea.viewport()`` behind the scrolling page and paints every visible glass
shell in a single pass from the already-preblurred Fuji wallpaper.

The scrolling page continues to own all text, inputs, tables and buttons. Card
geometry, glass and content therefore share the QWidget backing store during scroll;
Quick is kept only for the Fuji wallpaper/parallax and any non-scrolling surfaces
outside this page. No per-scroll Quick geometry publication, mask upload or
``frameSwapped`` handoff participates in the hot path.
"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QEvent, QModelIndex, QObject, QPoint, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPixmap, QRegion
from PySide6.QtWidgets import QFrame, QMainWindow, QScrollArea, QWidget

from .native_background import _GLASS_RADIUS, _NORMAL_GLASS_ALPHA, _OVERSCAN
from .native_visual_style import _CardScaleEffect


_PARALLAX_REPAINT_MS = 16
_PARALLAX_OFFSET_EPSILON = 0.02
_DIRTY_PAD_PX = 8


class _ViewportContentScaleEffect(_CardScaleEffect):
    """Reuse the existing content-scale path and invalidate its local glass shell."""

    def __init__(
        self,
        frame: QFrame,
        invalidate: Callable[[QFrame], None],
    ) -> None:
        super().__init__(frame)
        self._frame = frame
        self._invalidate_glass = invalidate

    def set_scale(self, scale: float) -> None:
        super().set_scale(scale)
        # NativeGlassProxy stores alpha before calling set_scale(), so invoking the
        # invalidator unconditionally also republishes alpha-only hover/press changes.
        self._invalidate_glass(self._frame)


class _ViewportGlassLayer(QWidget):
    """One fixed viewport-space compositor for all scrolling glass cards."""

    def __init__(self, controller: "ScrollLocalGlassController", viewport: QWidget) -> None:
        super().__init__(viewport)
        self.controller = controller
        self._last_rects: dict[QFrame, QRectF] = {}
        self.setObjectName("singlePageViewportGlass")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setGeometry(viewport.rect())
        self.show()

    def _padded_region(self, rect: QRectF) -> QRegion:
        if rect.isEmpty():
            return QRegion()
        visible = rect.intersected(QRectF(self.rect()))
        if visible.isEmpty():
            return QRegion()
        padded = visible.adjusted(
            -_DIRTY_PAD_PX,
            -_DIRTY_PAD_PX,
            _DIRTY_PAD_PX,
            _DIRTY_PAD_PX,
        )
        return QRegion(padded.toAlignedRect())

    def sync_frame_geometry(self, frame: QFrame) -> None:
        current = self.controller.card_rect_in_viewport(frame)
        previous = self._last_rects.get(frame, QRectF())
        dirty = self._padded_region(previous).united(self._padded_region(current))
        if current.isEmpty():
            self._last_rects.pop(frame, None)
        else:
            self._last_rects[frame] = QRectF(current)
        if not dirty.isEmpty():
            self.update(dirty)

    def sync_all_geometry(self) -> None:
        current_rects: dict[QFrame, QRectF] = {}
        dirty = QRegion()
        for frame, _proxy in self.controller.targets:
            current = self.controller.card_rect_in_viewport(frame)
            previous = self._last_rects.get(frame, QRectF())
            dirty = dirty.united(self._padded_region(previous))
            dirty = dirty.united(self._padded_region(current))
            if not current.isEmpty():
                current_rects[frame] = QRectF(current)

        self._last_rects = current_rects
        if not dirty.isEmpty():
            self.update(dirty)

    def refresh_visible_cards(self) -> None:
        dirty = QRegion()
        for frame, _proxy in self.controller.targets:
            dirty = dirty.united(self._padded_region(self.controller.card_rect_in_viewport(frame)))
        if not dirty.isEmpty():
            self.update(dirty)

    def resize_to_viewport(self) -> None:
        viewport = self.parentWidget()
        if viewport is None:
            return
        self.setGeometry(viewport.rect())
        self.sync_all_geometry()

    def paintEvent(self, _event) -> None:  # noqa: ANN001, N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        self.controller.paint_glass(painter)
        painter.end()


class ScrollLocalGlassController(QObject):
    """Own every Single-page scrolling glass shell in one QWidget compositor."""

    def __init__(self, window: QMainWindow, visual: Any, scroll: QScrollArea) -> None:
        super().__init__(window)
        self.window = window
        self.visual = visual
        self.background = getattr(visual, "background", None)
        self.scroll = scroll
        self.viewport = scroll.viewport()
        self.page = scroll.widget()
        self.quick = getattr(self.background, "quick_window", None)
        self._source = QPixmap(str(getattr(self.background, "_blur_path", "")))
        self._scaled_item = QPixmap()
        self._scaled_key: tuple[int, int] | None = None
        self._targets: list[tuple[QFrame, Any]] = []
        self._target_frames: set[QFrame] = set()
        self._layer: _ViewportGlassLayer | None = None
        self._last_quick_offset: tuple[float, float] | None = None

        self._parallax_timer = QTimer(self)
        self._parallax_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._parallax_timer.setInterval(_PARALLAX_REPAINT_MS)
        self._parallax_timer.timeout.connect(self._parallax_tick)

        self._layout_sync_timer = QTimer(self)
        self._layout_sync_timer.setSingleShot(True)
        self._layout_sync_timer.setInterval(0)
        self._layout_sync_timer.timeout.connect(self._sync_all_geometry)

        if (
            self.background is None
            or self.quick is None
            or self._source.isNull()
            or not isinstance(self.page, QWidget)
        ):
            return

        targets = self._collect_targets()
        if not targets:
            return

        self._targets = targets
        self._target_frames = {frame for frame, _proxy in targets}
        self._detach_from_quick_model([frame for frame, _proxy in targets])

        layer = _ViewportGlassLayer(self, self.viewport)
        self._layer = layer
        if self.page.parentWidget() is self.viewport:
            # The transparent scrolling page remains above the glass compositor,
            # preserving normal child paint order, focus and hit testing.
            layer.stackUnder(self.page)
            self.page.raise_()

        for frame, proxy in targets:
            effect = _ViewportContentScaleEffect(frame, self._invalidate_frame)
            frame.setGraphicsEffect(effect)
            # NativeGlassProxy is deliberately duck-typed by the card FX controller.
            proxy._scale_effect = effect  # noqa: SLF001
            frame.installEventFilter(self)

        self.viewport.installEventFilter(self)
        self.page.installEventFilter(self)
        self.scroll.verticalScrollBar().valueChanged.connect(self._sync_scroll_position)
        self.quick.widthChanged.connect(self._invalidate_scene_cache)
        self.quick.heightChanged.connect(self._invalidate_scene_cache)

        animation_signal = getattr(self.quick, "animationRunningChanged", None)
        if animation_signal is not None and hasattr(animation_signal, "connect"):
            try:
                animation_signal.connect(self._on_parallax_state_changed)
            except (RuntimeError, TypeError):
                pass

        self._last_quick_offset = self._quick_offset()

        # Remove all migrated shells from the old Quick mask/Repeater once. After
        # this initial cleanup, outer-page scrolling never publishes card geometry
        # back to Quick.
        schedule_mask = getattr(self.background, "schedule_mask_update", None)
        if callable(schedule_mask):
            try:
                schedule_mask()
            except RuntimeError:
                pass

        QTimer.singleShot(0, self._sync_initial_state)

    @property
    def targets(self) -> tuple[tuple[QFrame, Any], ...]:
        return tuple(self._targets)

    @property
    def active_count(self) -> int:
        return len(self._targets) if self._layer is not None else 0

    @staticmethod
    def _is_descendant_of(widget: QWidget, ancestor: QWidget) -> bool:
        current: QWidget | None = widget
        while current is not None:
            if current is ancestor:
                return True
            current = current.parentWidget()
        return False

    def _collect_targets(self) -> list[tuple[QFrame, Any]]:
        page = self.page
        model = getattr(self.background, "card_model", None)
        cards = getattr(model, "cards", None)
        surface_for = getattr(self.visual, "surface_for", None)
        if (
            not isinstance(page, QWidget)
            or not isinstance(cards, list)
            or not callable(surface_for)
        ):
            return []

        targets: list[tuple[QFrame, Any]] = []
        for frame in list(cards):
            if not isinstance(frame, QFrame) or not self._is_descendant_of(frame, page):
                continue
            proxy = surface_for(frame)
            if proxy is not None:
                targets.append((frame, proxy))
        return targets

    def _detach_from_quick_model(self, frames: list[QFrame]) -> None:
        model = getattr(self.background, "card_model", None)
        cards = getattr(model, "cards", None)
        states = getattr(model, "_states", None)
        rows = getattr(model, "_rows", None)
        if (
            model is None
            or not isinstance(cards, list)
            or not isinstance(states, list)
            or not isinstance(rows, dict)
        ):
            return

        removal_rows = sorted(
            {int(rows[frame]) for frame in frames if frame in rows},
            reverse=True,
        )
        for row in removal_rows:
            if row < 0 or row >= len(cards):
                continue
            model.beginRemoveRows(QModelIndex(), row, row)
            try:
                del cards[row]
                del states[row]
            finally:
                model.endRemoveRows()

        model._rows = {frame: row for row, frame in enumerate(cards)}  # noqa: SLF001

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
            return (
                float(quick.property("offsetX") or 0.0),
                float(quick.property("offsetY") or 0.0),
            )
        except (RuntimeError, TypeError, ValueError):
            return 0.0, 0.0

    def _proxy_for(self, frame: QFrame) -> Any | None:
        for candidate, proxy in self._targets:
            if candidate is frame:
                return proxy
        return None

    def card_rect_in_viewport(self, frame: QFrame) -> QRectF:
        proxy = self._proxy_for(frame)
        if proxy is None:
            return QRectF()
        try:
            if (
                not frame.isVisibleTo(self.viewport)
                or frame.width() <= 0
                or frame.height() <= 0
            ):
                return QRectF()
            top_left = frame.mapTo(self.viewport, QPoint(0, 0))
            rect = QRectF(
                float(top_left.x()),
                float(top_left.y()),
                float(frame.width()),
                float(frame.height()),
            )
            scale = max(
                0.96,
                min(1.04, float(getattr(proxy, "surface_scale", 1.0))),
            )
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
        layer = self._layer
        if item is None or quick is None or layer is None:
            return

        try:
            quick_width = float(quick.width())
            quick_height = float(quick.height())
            offset_x, offset_y = self._quick_offset()
            image_x = (quick_width - float(item.width())) * 0.5 + offset_x
            image_y = (quick_height - float(item.height())) * 0.5 + offset_y
            viewport_origin = self.viewport.mapTo(self.window, QPoint(0, 0))
        except (RuntimeError, TypeError, ValueError):
            return

        viewport_rect = QRectF(layer.rect())
        for frame, proxy in self._targets:
            card_rect = self.card_rect_in_viewport(frame)
            if card_rect.isEmpty() or not card_rect.intersects(viewport_rect):
                continue
            try:
                overlay_alpha = float(
                    getattr(proxy, "overlay_alpha", _NORMAL_GLASS_ALPHA)
                )
            except (RuntimeError, TypeError, ValueError):
                overlay_alpha = _NORMAL_GLASS_ALPHA

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
        layer.sync_all_geometry()

    def _sync_scroll_position(self, _value: int) -> None:
        # QScrollArea has already moved its page when valueChanged fires. Publish
        # every old/new shell region to the SAME QWidget backing store in one pass.
        # No Quick card geometry, mask upload or render-thread handoff is involved.
        layer = self._layer
        if layer is not None:
            layer.sync_all_geometry()

    def _invalidate_frame(self, frame: QFrame) -> None:
        layer = self._layer
        if layer is not None:
            layer.sync_frame_geometry(frame)

    def _sync_all_geometry(self) -> None:
        layer = self._layer
        if layer is not None:
            layer.sync_all_geometry()

    def _queue_layout_sync(self) -> None:
        if not self._layout_sync_timer.isActive():
            self._layout_sync_timer.start()

    def _invalidate_scene_cache(self, *_args: object) -> None:
        self._scaled_item = QPixmap()
        self._scaled_key = None
        self._last_quick_offset = None
        layer = self._layer
        if layer is not None:
            layer.resize_to_viewport()
            layer.refresh_visible_cards()

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
            self._refresh_for_parallax(force=True)

    def _refresh_for_parallax(self, *, force: bool = False) -> None:
        layer = self._layer
        if layer is None:
            return
        offset = self._quick_offset()
        previous = self._last_quick_offset
        if (
            not force
            and previous is not None
            and abs(previous[0] - offset[0]) < _PARALLAX_OFFSET_EPSILON
            and abs(previous[1] - offset[1]) < _PARALLAX_OFFSET_EPSILON
        ):
            return
        self._last_quick_offset = offset
        layer.refresh_visible_cards()

    def _parallax_tick(self) -> None:
        quick = self.quick
        if quick is None:
            self._parallax_timer.stop()
            return
        self._refresh_for_parallax()
        try:
            running = bool(quick.property("animationRunning"))
        except RuntimeError:
            self._parallax_timer.stop()
            return
        if not running:
            self._parallax_timer.stop()
            self._refresh_for_parallax(force=True)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        event_type = event.type()
        layer = self._layer
        if layer is None:
            return False

        if watched is self.viewport and event_type in {
            QEvent.Type.Move,
            QEvent.Type.Resize,
            QEvent.Type.Show,
        }:
            layer.resize_to_viewport()
        elif watched is self.page and event_type in {
            QEvent.Type.Resize,
            QEvent.Type.LayoutRequest,
        }:
            self._queue_layout_sync()
        elif watched in self._target_frames and event_type in {
            QEvent.Type.Move,
            QEvent.Type.Resize,
            QEvent.Type.Show,
            QEvent.Type.Hide,
            QEvent.Type.LayoutRequest,
            QEvent.Type.ParentChange,
        }:
            self._invalidate_frame(watched)  # type: ignore[arg-type]
        return False


def install_scroll_local_glass(
    window: QMainWindow,
    visual: Any,
) -> ScrollLocalGlassController | None:
    """Install one viewport compositor for every Single-page scrolling glass card."""

    existing = getattr(window, "_scroll_local_glass", None)
    if isinstance(existing, ScrollLocalGlassController):
        return existing

    scroll = getattr(window, "_single_page_scroll", None)
    if not isinstance(scroll, QScrollArea):
        return None

    controller = ScrollLocalGlassController(window, visual, scroll)
    if controller.active_count <= 0:
        controller.deleteLater()
        return None

    window._scroll_local_glass = controller  # type: ignore[attr-defined]
    return controller
