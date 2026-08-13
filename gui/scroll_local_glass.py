"""One page-owned QWidget glass compositor for scrolling Single-page cards.

The native Quick window keeps the Fuji wallpaper/parallax. Glass shells that live
inside ``singlePageScrollContent`` are removed from Quick's card model and painted
by ONE transparent child of the scrolling page. The page, the shared glass layer,
and all real controls therefore move in the same QWidget backing-store transaction.

Continuous outer scrolling never republishes per-card geometry and never rebuilds
the Quick glass mask. Card/ancestor rectangles are cached in page-local coordinates
only when layout changes. A scroll tick merely invalidates the exposed viewport
region of the single shared layer so its pre-blurred wallpaper sample stays fixed to
the window while QScrollArea moves the page.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MethodType
from typing import Any

from PySide6.QtCore import QEvent, QModelIndex, QObject, QPoint, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPixmap, QRegion
from PySide6.QtWidgets import QFrame, QMainWindow, QScrollArea, QWidget

from .native_background import _GLASS_NAMES, _GLASS_RADIUS, _NORMAL_GLASS_ALPHA, _OVERSCAN


_LAYOUT_SYNC_MS = 0
_PARALLAX_OFFSET_EPSILON = 0.02
_DIRTY_PAD_PX = 8


@dataclass(slots=True)
class _GlassRecord:
    frame: QFrame
    rect: QRectF
    clip: QRectF


class _SinglePageGlassLayer(QWidget):
    """Single parent-drawn glass surface that scrolls with the real page."""

    def __init__(self, controller: "ScrollLocalGlassController") -> None:
        super().__init__(controller.page)
        self.controller = controller
        self.setObjectName("singlePageSharedGlass")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setGeometry(controller.page.rect())
        self.show()
        self.lower()

    def sync_to_page(self) -> None:
        try:
            self.setGeometry(self.controller.page.rect())
            self.lower()
            self.update_visible_region()
        except RuntimeError:
            return

    def _visible_rect(self) -> QRectF:
        """Derive the exposed page slice directly from the outer scrollbar."""

        try:
            viewport = self.controller.viewport
            scroll_y = float(self.controller.scroll.verticalScrollBar().value())
            return QRectF(
                0.0,
                scroll_y,
                float(viewport.width()),
                float(viewport.height()),
            ).intersected(QRectF(self.rect()))
        except RuntimeError:
            return QRectF()

    def update_visible_region(self) -> None:
        visible = self._visible_rect()
        if visible.isEmpty():
            return
        padded = visible.adjusted(
            -_DIRTY_PAD_PX,
            -_DIRTY_PAD_PX,
            _DIRTY_PAD_PX,
            _DIRTY_PAD_PX,
        ).intersected(QRectF(self.rect()))
        if not padded.isEmpty():
            self.update(QRegion(padded.toAlignedRect()))

    def update_record(self, record: _GlassRecord | None) -> None:
        if record is None:
            self.update_visible_region()
            return
        # Always invalidate the maximum allowed hover footprint so a shrinking
        # card cannot leave pixels from its previous larger shell behind.
        dirty = self.controller.scaled_rect(record.rect, 1.04).adjusted(
            -_DIRTY_PAD_PX,
            -_DIRTY_PAD_PX,
            _DIRTY_PAD_PX,
            _DIRTY_PAD_PX,
        ).intersected(QRectF(self.rect()))
        if not dirty.isEmpty():
            self.update(QRegion(dirty.toAlignedRect()))

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        # This widget is transparent and repaints partial dirty regions. Explicitly
        # clear the incoming region so layout/hover shrink cannot leave stale glass.
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(event.rect(), Qt.GlobalColor.transparent)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        self.controller.paint_glass(painter)
        painter.end()


class ScrollLocalGlassController(QObject):
    """Move Single-page card shells from native Quick into one page parent layer."""

    def __init__(
        self,
        window: QMainWindow,
        visual: Any,
        scroll: QScrollArea,
        page: QWidget,
    ) -> None:
        super().__init__(window)
        self.window = window
        self.visual = visual
        self.background = getattr(visual, "background", None)
        self.quick = getattr(self.background, "quick_window", None)
        self.scroll = scroll
        self.viewport = scroll.viewport()
        self.page = page
        self._source = QPixmap(str(getattr(self.background, "_blur_path", "")))
        self._cover = QPixmap()
        self._cover_key: tuple[int, int] | None = None
        self._records: list[_GlassRecord] = []
        self._record_by_frame: dict[QFrame, _GlassRecord] = {}
        self._hooked_proxies: set[QObject] = set()
        self._last_quick_offset: tuple[float, float] | None = None
        self._original_refresh = getattr(visual, "refresh_glass_frames", None)

        self._layout_timer = QTimer(self)
        self._layout_timer.setSingleShot(True)
        self._layout_timer.setInterval(_LAYOUT_SYNC_MS)
        self._layout_timer.timeout.connect(self._rebuild_records)

        self._layer: _SinglePageGlassLayer | None = None
        if self.background is None or self.quick is None or self._source.isNull():
            return

        self._layer = _SinglePageGlassLayer(self)
        self.page.installEventFilter(self)
        self.viewport.installEventFilter(self)
        self.scroll.verticalScrollBar().valueChanged.connect(self._on_scroll)

        try:
            self.quick.widthChanged.connect(self._invalidate_scene_cache)
            self.quick.heightChanged.connect(self._invalidate_scene_cache)
            self.quick.frameSwapped.connect(self._on_quick_frame)
        except (AttributeError, RuntimeError, TypeError):
            pass

        self._migrate_existing_page_cards()
        self._wrap_refresh_glass_frames()
        self._last_quick_offset = self._quick_offset()
        QTimer.singleShot(0, self._rebuild_records)

    @property
    def active_count(self) -> int:
        return len(self._records) if self._layer is not None else 0

    @staticmethod
    def _is_descendant(widget: QWidget, ancestor: QWidget) -> bool:
        current: QWidget | None = widget
        while current is not None:
            if current is ancestor:
                return True
            current = current.parentWidget()
        return False

    @staticmethod
    def scaled_rect(rect: QRectF, scale: float) -> QRectF:
        if rect.isEmpty() or abs(scale - 1.0) <= 1e-5:
            return QRectF(rect)
        center = rect.center()
        width = rect.width() * scale
        height = rect.height() * scale
        return QRectF(
            center.x() - width * 0.5,
            center.y() - height * 0.5,
            width,
            height,
        )

    def scale_for(self, frame: QFrame) -> float:
        proxy = getattr(self.visual, "_glass", {}).get(frame)
        try:
            return max(0.96, min(1.04, float(proxy.surface_scale)))
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return 1.0

    def alpha_for(self, frame: QFrame) -> float:
        proxy = getattr(self.visual, "_glass", {}).get(frame)
        try:
            return max(
                _NORMAL_GLASS_ALPHA,
                min(255.0, float(proxy.overlay_alpha)),
            )
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return _NORMAL_GLASS_ALPHA

    def _page_glass_frames(self) -> list[QFrame]:
        glass = getattr(self.visual, "_glass", {})
        if not isinstance(glass, dict):
            return []
        frames: list[QFrame] = []
        for frame in glass:
            if not isinstance(frame, QFrame) or frame.objectName() not in _GLASS_NAMES:
                continue
            try:
                if self._is_descendant(frame, self.page):
                    frames.append(frame)
            except RuntimeError:
                continue
        return frames

    def _detach_quick_rows(self, frames: list[QFrame]) -> None:
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

        if removal_rows:
            schedule = getattr(self.background, "schedule_mask_update", None)
            if callable(schedule):
                schedule()

    def _hook_proxy(self, frame: QFrame) -> None:
        proxy = getattr(self.visual, "_glass", {}).get(frame)
        if proxy is None or proxy in self._hooked_proxies:
            return
        original = getattr(proxy, "set_interaction", None)
        if not callable(original):
            return
        controller = self

        def set_interaction(surface, *, scale: float, overlay_alpha: float) -> None:  # noqa: ANN001
            original(scale=scale, overlay_alpha=overlay_alpha)
            layer = controller._layer
            if layer is not None:
                layer.update_record(controller._record_by_frame.get(frame))

        proxy.set_interaction = MethodType(set_interaction, proxy)
        self._hooked_proxies.add(proxy)

    def _migrate_existing_page_cards(self) -> None:
        frames = self._page_glass_frames()
        self._detach_quick_rows(frames)
        for frame in frames:
            self._hook_proxy(frame)
            frame.installEventFilter(self)

    def _wrap_refresh_glass_frames(self) -> None:
        original = self._original_refresh
        if not callable(original):
            return
        controller = self

        def refresh(owner) -> int:  # noqa: ANN001
            added = int(original())
            frames = controller._page_glass_frames()
            controller._detach_quick_rows(frames)
            for frame in frames:
                controller._hook_proxy(frame)
                frame.installEventFilter(controller)
            controller._queue_layout_sync()
            return added

        self.visual.refresh_glass_frames = MethodType(refresh, self.visual)

    def _queue_layout_sync(self) -> None:
        if not self._layout_timer.isActive():
            self._layout_timer.start()

    def _page_rect(self, widget: QWidget) -> QRectF:
        top_left = widget.mapTo(self.page, QPoint(0, 0))
        return QRectF(
            float(top_left.x()),
            float(top_left.y()),
            float(widget.width()),
            float(widget.height()),
        )

    def _record_for(self, frame: QFrame) -> _GlassRecord | None:
        try:
            if frame.width() <= 0 or frame.height() <= 0:
                return None
            rect = self._page_rect(frame)
            clip = QRectF(self.page.rect())
            ancestor = frame.parentWidget()
            while ancestor is not None and ancestor is not self.page:
                if not self._is_descendant(ancestor, self.page):
                    break
                clip = clip.intersected(self._page_rect(ancestor))
                if clip.isEmpty():
                    break
                ancestor = ancestor.parentWidget()
            return _GlassRecord(frame=frame, rect=rect, clip=clip)
        except RuntimeError:
            return None

    def _rebuild_records(self) -> None:
        records: list[_GlassRecord] = []
        for frame in self._page_glass_frames():
            record = self._record_for(frame)
            if record is not None:
                records.append(record)
        self._records = records
        self._record_by_frame = {record.frame: record for record in records}
        layer = self._layer
        if layer is not None:
            layer.sync_to_page()

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

    def _ensure_cover(self) -> QPixmap | None:
        quick = self.quick
        if quick is None:
            return None
        try:
            width = max(1, round(float(quick.width()) * _OVERSCAN))
            height = max(1, round(float(quick.height()) * _OVERSCAN))
        except RuntimeError:
            return None
        key = (width, height)
        if self._cover_key == key and not self._cover.isNull():
            return self._cover

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
        self._cover = scaled.copy(crop_x, crop_y, width, height)
        self._cover_key = key
        return self._cover

    def paint_glass(self, painter: QPainter) -> None:
        layer = self._layer
        cover = self._ensure_cover()
        quick = self.quick
        if layer is None or cover is None or quick is None:
            return
        try:
            viewport_origin = self.viewport.mapTo(self.window, QPoint(0, 0))
            scroll_y = float(self.scroll.verticalScrollBar().value())
            quick_width = float(quick.width())
            quick_height = float(quick.height())
            offset_x, offset_y = self._quick_offset()
            image_x = (quick_width - float(cover.width())) * 0.5 + offset_x
            image_y = (quick_height - float(cover.height())) * 0.5 + offset_y
        except (RuntimeError, TypeError, ValueError):
            return

        layer_rect = QRectF(layer.rect())
        for record in self._records:
            frame = record.frame
            try:
                if not frame.isVisibleTo(self.viewport):
                    continue
            except RuntimeError:
                continue

            scale = self.scale_for(frame)
            target = self.scaled_rect(record.rect, scale)
            if target.isEmpty() or not target.intersects(layer_rect):
                continue

            source = QRectF(
                float(viewport_origin.x()) + target.x() - image_x,
                float(viewport_origin.y()) + target.y() - scroll_y - image_y,
                target.width(),
                target.height(),
            )

            painter.save()
            # Resting shells respect the real inner QWidget clip. Hover scaling
            # keeps the existing overflow-visible visual contract; the outer
            # QScrollArea viewport still clips the whole page naturally.
            if abs(scale - 1.0) <= 1e-5 and not record.clip.isEmpty():
                painter.setClipRect(record.clip)
            path = QPainterPath()
            path.addRoundedRect(target, _GLASS_RADIUS * scale, _GLASS_RADIUS * scale)
            painter.setClipPath(path, Qt.ClipOperation.IntersectClip)
            painter.drawPixmap(target, cover, source)
            painter.fillRect(target, QColor(0, 0, 0, round(self.alpha_for(frame))))
            painter.restore()

    def _on_scroll(self, _value: int) -> None:
        # Deliberately O(1) with respect to card count. QScrollArea already moved
        # the page, its children, and this shared layer in one QWidget transaction.
        layer = self._layer
        if layer is not None:
            layer.update_visible_region()

    def _invalidate_scene_cache(self, *_args: object) -> None:
        self._cover = QPixmap()
        self._cover_key = None
        self._last_quick_offset = None
        layer = self._layer
        if layer is not None:
            layer.update_visible_region()

    def _on_quick_frame(self) -> None:
        # Parallax is intentionally independent of scrolling. Repaint the one
        # visible shared layer only when the actually-presented Quick frame changed
        # its wallpaper offset; never walk card geometry here.
        offset = self._quick_offset()
        previous = self._last_quick_offset
        if (
            previous is not None
            and abs(previous[0] - offset[0]) < _PARALLAX_OFFSET_EPSILON
            and abs(previous[1] - offset[1]) < _PARALLAX_OFFSET_EPSILON
        ):
            return
        self._last_quick_offset = offset
        layer = self._layer
        if layer is not None:
            layer.update_visible_region()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        event_type = event.type()
        layer = self._layer
        if layer is None:
            return False

        if watched is self.page:
            # QScrollArea scrolling moves the page but does not change page-local
            # card geometry. A page Move is therefore explicitly NOT a cache miss.
            if event_type in {
                QEvent.Type.Resize,
                QEvent.Type.LayoutRequest,
                QEvent.Type.Show,
            }:
                self._queue_layout_sync()
        elif watched is self.viewport and event_type in {
            QEvent.Type.Resize,
            QEvent.Type.Show,
        }:
            layer.update_visible_region()
        elif isinstance(watched, QFrame) and watched in self._record_by_frame:
            if event_type in {
                QEvent.Type.Move,
                QEvent.Type.Resize,
                QEvent.Type.Show,
                QEvent.Type.Hide,
                QEvent.Type.LayoutRequest,
                QEvent.Type.ParentChange,
            }:
                self._queue_layout_sync()
        return False


def install_scroll_local_glass(
    window: QMainWindow,
    visual: Any,
    scroll: QScrollArea,
    page: QWidget,
) -> ScrollLocalGlassController | None:
    """Install one page-owned parent compositor for all Single scrolling glass."""

    existing = getattr(window, "_scroll_local_glass", None)
    if isinstance(existing, ScrollLocalGlassController):
        return existing

    controller = ScrollLocalGlassController(window, visual, scroll, page)
    if controller._layer is None:  # noqa: SLF001
        controller.deleteLater()
        return None

    window._scroll_local_glass = controller  # type: ignore[attr-defined]
    return controller
