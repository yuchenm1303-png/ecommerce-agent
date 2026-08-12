"""Exact-visual fast path for Single-page glass scrolling.

The formal GUI deliberately keeps the proven NativeQuickBackground rendering
pipeline unchanged. This controller only replaces the hot path used while the
outer Single QScrollArea moves:

* cache QWidget/page geometry when layout actually changes;
* derive every scrolling card's window rect by a numeric Y translation;
* keep the existing QAbstractListModel/QML presentation contract;
* render the exact same ARGB32/QPainter rounded-rect mask into reusable buffers;
* publish that mask immediately through the already-installed in-memory provider.

There are no visual-policy constants here: blur source, tint, alpha, radius,
hover scale, clipping and parallax stay owned by the existing renderer.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MethodType
from typing import Any

from PySide6.QtCore import QEvent, QObject, QPoint, QRectF, QTimer, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QFrame, QMainWindow, QScrollArea, QWidget


_MASK_BUFFER_COUNT = 3


@dataclass(slots=True)
class _CachedCard:
    frame: QFrame
    row: int
    card_base: QRectF
    moving_clip_base: QRectF
    fixed_clip: QRectF
    structurally_visible: bool


class SingleScrollGlassFastPath(QObject):
    """Move Single-page glass at scroll rate without QWidget geometry walks."""

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
        self.scroll = scroll
        self.page = page
        self.background = getattr(visual, "background", None)
        self.model = getattr(self.background, "card_model", None)
        self._cache: list[_CachedCard] = []
        self._cache_dirty = True
        self._rebuild_queued = False
        self._mask_size: tuple[int, int] = (0, 0)
        self._mask_buffers: list[QImage] = []
        self._mask_buffer_index = -1
        self._original_render_mask = getattr(self.model, "render_mask", None)

        if self.background is None or self.model is None or not callable(self._original_render_mask):
            return

        self._install_reusable_mask_renderer()
        self._install_geometry_watchers()

        bar = self.scroll.verticalScrollBar()
        bar.valueChanged.connect(self._on_scroll)
        bar.rangeChanged.connect(self._on_scroll_range_changed)
        self.scroll.destroyed.connect(self._cleanup)

        # Build once after all page reparenting/layout requests from the installer
        # have settled. The normal background timer remains authoritative for the
        # first visual frame; no extra mask publication is required here.
        QTimer.singleShot(0, self._rebuild_cache)

    def _install_reusable_mask_renderer(self) -> None:
        controller = self

        def render_mask(_model, width: int, height: int) -> QImage:  # noqa: ANN001
            return controller._render_mask(width, height)

        self.model.render_mask = MethodType(render_mask, self.model)  # type: ignore[method-assign]

    def _ensure_mask_buffers(self, width: int, height: int) -> None:
        size = (max(1, int(width)), max(1, int(height)))
        if size == self._mask_size and len(self._mask_buffers) == _MASK_BUFFER_COUNT:
            return
        self._mask_size = size
        self._mask_buffers = [
            QImage(size[0], size[1], QImage.Format.Format_ARGB32_Premultiplied)
            for _ in range(_MASK_BUFFER_COUNT)
        ]
        for image in self._mask_buffers:
            image.fill(Qt.GlobalColor.transparent)
        self._mask_buffer_index = -1

    def _render_mask(self, width: int, height: int) -> QImage:
        """Render the same mask pixels as GlassCardModel.render_mask, allocation-free."""

        self._ensure_mask_buffers(width, height)
        self._mask_buffer_index = (self._mask_buffer_index + 1) % len(self._mask_buffers)
        image = self._mask_buffers[self._mask_buffer_index]
        image.fill(Qt.GlobalColor.transparent)

        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(Qt.GlobalColor.white)

        # Keep the renderer's existing radius rather than creating a second visual
        # token. NativeQuickBackground currently owns this value as _GLASS_RADIUS.
        from . import native_background as native_background_module

        radius = float(getattr(native_background_module, "_GLASS_RADIUS", 6.0))
        try:
            states = self.model._states  # noqa: SLF001
            for state in states:
                if not bool(state["cardVisible"]):
                    continue
                clip = QRectF(
                    float(state["clipX"]),
                    float(state["clipY"]),
                    float(state["clipW"]),
                    float(state["clipH"]),
                )
                card = QRectF(
                    float(state["cardX"]),
                    float(state["cardY"]),
                    float(state["cardW"]),
                    float(state["cardH"]),
                )
                if clip.isEmpty() or card.isEmpty():
                    continue
                painter.save()
                painter.setClipRect(clip)
                painter.drawRoundedRect(card, radius, radius)
                painter.restore()
        finally:
            painter.end()
        return image

    def _install_geometry_watchers(self) -> None:
        watched: set[QObject] = {self.window, self.page, self.scroll.viewport()}
        try:
            cards = tuple(self.model.cards)
        except (AttributeError, RuntimeError):
            cards = ()
        for frame in cards:
            try:
                if frame is self.page or self.page.isAncestorOf(frame):
                    watched.add(frame)
            except RuntimeError:
                continue
        self._watched = watched
        for obj in watched:
            try:
                obj.installEventFilter(self)
            except RuntimeError:
                pass

    @staticmethod
    def _rect(widget: QWidget, window: QMainWindow, *, y_offset: float = 0.0) -> QRectF:
        top_left = widget.mapTo(window, QPoint(0, 0))
        return QRectF(
            float(top_left.x()),
            float(top_left.y()) + float(y_offset),
            float(widget.width()),
            float(widget.height()),
        )

    def _is_moving_ancestor(self, widget: QWidget) -> bool:
        return widget is self.page or self.page.isAncestorOf(widget)

    def _rebuild_cache(self) -> None:
        self._rebuild_queued = False
        if self.background is None or self.model is None:
            return
        try:
            scroll_y = float(self.scroll.verticalScrollBar().value())
            window_rect = QRectF(
                0.0,
                0.0,
                float(self.window.width()),
                float(self.window.height()),
            )
            cards = tuple(self.model.cards)
        except (AttributeError, RuntimeError):
            return

        rebuilt: list[_CachedCard] = []
        for row, frame in enumerate(cards):
            try:
                if frame is not self.page and not self.page.isAncestorOf(frame):
                    continue
                card_base = self._rect(frame, self.window, y_offset=scroll_y)
                moving_clip: QRectF | None = None
                fixed_clip = QRectF(window_rect)
                structurally_visible = frame.isVisibleTo(self.window)

                ancestor = frame.parentWidget()
                while ancestor is not None:
                    if not ancestor.isVisibleTo(self.window):
                        structurally_visible = False
                        break
                    if self._is_moving_ancestor(ancestor):
                        rect = self._rect(ancestor, self.window, y_offset=scroll_y)
                        moving_clip = (
                            QRectF(rect)
                            if moving_clip is None
                            else moving_clip.intersected(rect)
                        )
                    else:
                        rect = self._rect(ancestor, self.window)
                        fixed_clip = fixed_clip.intersected(rect)
                    if ancestor is self.window:
                        break
                    ancestor = ancestor.parentWidget()

                if moving_clip is None:
                    moving_clip = QRectF(card_base)
                rebuilt.append(
                    _CachedCard(
                        frame=frame,
                        row=row,
                        card_base=card_base,
                        moving_clip_base=moving_clip,
                        fixed_clip=fixed_clip,
                        structurally_visible=structurally_visible,
                    )
                )
            except RuntimeError:
                continue

        self._cache = rebuilt
        self._cache_dirty = False
        # Make the cached representation agree with whatever scroll position won
        # while the zero-time rebuild was queued, without publishing another mask.
        self._apply_cached_scroll(float(self.scroll.verticalScrollBar().value()), publish=False)

    def _queue_rebuild(self) -> None:
        self._cache_dirty = True
        if self._rebuild_queued:
            return
        self._rebuild_queued = True
        QTimer.singleShot(0, self._rebuild_cache)

    def _apply_cached_scroll(self, value: float, *, publish: bool) -> None:
        if self.background is None or self.model is None:
            return
        if self._cache_dirty:
            self._rebuild_cache()
        if not self._cache:
            return

        changed_rows: list[int] = []
        try:
            states = self.model._states  # noqa: SLF001
        except (AttributeError, RuntimeError):
            return

        for cached in self._cache:
            if cached.row < 0 or cached.row >= len(states):
                continue
            card = QRectF(cached.card_base)
            card.translate(0.0, -value)
            moving_clip = QRectF(cached.moving_clip_base)
            moving_clip.translate(0.0, -value)
            clip = cached.fixed_clip.intersected(moving_clip)
            visible = bool(
                cached.structurally_visible
                and not clip.isEmpty()
                and not card.intersected(clip).isEmpty()
            )

            state = states[cached.row]
            next_values = {
                "cardX": card.x(),
                "cardY": card.y(),
                "cardW": card.width(),
                "cardH": card.height(),
                "clipX": clip.x() if visible else 0.0,
                "clipY": clip.y() if visible else 0.0,
                "clipW": clip.width() if visible else 0.0,
                "clipH": clip.height() if visible else 0.0,
                "cardVisible": visible,
            }
            changed = False
            for key, next_value in next_values.items():
                previous = state.get(key)
                if isinstance(next_value, bool):
                    different = bool(previous) != next_value
                else:
                    try:
                        different = abs(float(previous) - float(next_value)) > 0.01
                    except (TypeError, ValueError):
                        different = previous != next_value
                if different:
                    state[key] = next_value
                    changed = True
            if changed:
                changed_rows.append(cached.row)

        if changed_rows:
            roles = list(getattr(self.model, "_GEOMETRY_ROLES", ()))  # noqa: SLF001
            # Rows are few and normally contiguous; one range signal avoids Python
            # signal churn while preserving the exact existing model contract.
            self.model.dataChanged.emit(
                self.model.index(min(changed_rows), 0),
                self.model.index(max(changed_rows), 0),
                roles,
            )

        if publish:
            self._publish_current_mask()

    def _publish_current_mask(self) -> None:
        update_mask = getattr(self.background, "_update_mask_texture", None)
        if not callable(update_mask):
            return
        try:
            # By the time user input is possible, ui_runtime_optimizations has
            # replaced this method with the in-memory provider implementation.
            # The PNG fallback remains available if that optimization is absent.
            update_mask()
            quick = getattr(self.background, "quick_window", None)
            if quick is not None:
                quick.update()
        except RuntimeError:
            return

    def _on_scroll(self, value: int) -> None:
        # This is the entire continuous-scroll hot path: no QWidget mapTo(), no
        # ancestor traversal, no 24 ms geometry timer and no new QImage allocation.
        self._apply_cached_scroll(float(value), publish=True)

    def _on_scroll_range_changed(self, _minimum: int, _maximum: int) -> None:
        self._queue_rebuild()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        event_type = event.type()
        if watched is self.page:
            # QScrollArea scrolling physically moves the page. That Move event is
            # precisely what this fast path replaces and must never invalidate it.
            if event_type in {
                QEvent.Type.Resize,
                QEvent.Type.LayoutRequest,
                QEvent.Type.Show,
                QEvent.Type.Hide,
                QEvent.Type.ParentChange,
            }:
                self._queue_rebuild()
            return False

        if event_type in {
            QEvent.Type.Move,
            QEvent.Type.Resize,
            QEvent.Type.LayoutRequest,
            QEvent.Type.Show,
            QEvent.Type.Hide,
            QEvent.Type.ParentChange,
        }:
            self._queue_rebuild()
        return False

    def _cleanup(self) -> None:
        for obj in tuple(getattr(self, "_watched", ())):
            try:
                obj.removeEventFilter(self)
            except RuntimeError:
                pass


def install_single_scroll_glass_fastpath(
    window: QMainWindow,
    visual: Any,
    scroll: QScrollArea,
    page: QWidget,
) -> SingleScrollGlassFastPath:
    existing = getattr(window, "_single_scroll_glass_fastpath", None)
    if isinstance(existing, SingleScrollGlassFastPath):
        return existing
    controller = SingleScrollGlassFastPath(window, visual, scroll, page)
    window._single_scroll_glass_fastpath = controller  # type: ignore[attr-defined]
    return controller
