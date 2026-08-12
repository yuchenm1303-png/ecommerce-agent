"""WWidget-local glass prototype for the first scrolling Single-page cards.

This is deliberately narrow: Product Source and the top status cards move their
blur/tint shell out of the independent Quick card scene while the rest of the GUI
keeps the proven native Quick glass path. The experiment lets Windows validate
whether one-render-domain ownership removes continuous-scroll lag before any
broader migration is considered.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QEvent, QObject, QPoint, QRectF, Qt, Slot
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QFrame, QMainWindow, QScrollArea, QWidget

from .native_background import _GLASS_RADIUS, _NORMAL_GLASS_ALPHA, _OVERSCAN


_LOCAL_LAYER_NAME = "localScrollableGlassLayer"


def _ancestor_card(widget: QWidget | None, object_name: str) -> QFrame | None:
    current = widget
    while current is not None:
        if isinstance(current, QFrame) and current.objectName() == object_name:
            return current
        current = current.parentWidget()
    return None


class _LocalGlassLayer(QWidget):
    """Cheap blurred-wallpaper crop painted inside one moving QWidget card."""

    def __init__(self, frame: QFrame, controller: "ScrollLocalGlassController") -> None:
        super().__init__(frame)
        self.frame = frame
        self.controller = controller
        self._overlay_alpha = _NORMAL_GLASS_ALPHA
        self.setObjectName(_LOCAL_LAYER_NAME)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setGeometry(frame.rect())
        self.lower()
        self.show()

    def set_overlay_alpha(self, alpha: float) -> None:
        alpha = max(_NORMAL_GLASS_ALPHA, min(255.0, float(alpha)))
        if abs(alpha - self._overlay_alpha) < 0.1:
            return
        self._overlay_alpha = alpha
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: ANN001, N802
        if self.width() <= 0 or self.height() <= 0:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        bounds = QRectF(self.rect())
        clip = QPainterPath()
        clip.addRoundedRect(bounds, _GLASS_RADIUS, _GLASS_RADIUS)
        painter.setClipPath(clip)

        self.controller.paint_blurred_wallpaper(painter, self.frame, bounds)
        painter.fillRect(bounds, QColor(0, 0, 0, round(self._overlay_alpha)))
        painter.end()


class ScrollLocalGlassController(QObject):
    """Own the first local-glass cards and keep their crop aligned to the window."""

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
        self._last_quick_offset: tuple[float, float] | None = None
        self._layers: dict[QFrame, _LocalGlassLayer] = {}

        if self.background is None or self.quick is None or self._source.isNull():
            return

        frames = self._resolve_frames()
        if not frames:
            return

        self._detach_from_quick_model(frames)
        for frame in frames:
            layer = _LocalGlassLayer(frame, self)
            use_local = getattr(self.visual, "use_local_glass", None)
            if not callable(use_local) or not bool(use_local(frame, layer)):
                layer.deleteLater()
                continue
            self._layers[frame] = layer
            frame.installEventFilter(self)

        if not self._layers:
            return

        self.scroll.verticalScrollBar().valueChanged.connect(self._repaint_for_scroll)
        self.quick.widthChanged.connect(self._invalidate_scaled_item)
        self.quick.heightChanged.connect(self._invalidate_scaled_item)
        self.quick.frameSwapped.connect(
            self._sync_parallax_crop,
            Qt.ConnectionType.QueuedConnection,
        )

        # Remove the old Quick blur/tint shell once, then keep all local cards out
        # of the model permanently. Other cards continue using the existing path.
        try:
            self.background.card_model.sync_geometry()
            self.background.schedule_mask_update()
        except RuntimeError:
            pass

        self._repaint_visible_layers(sync=True)

    @property
    def active_count(self) -> int:
        return len(self._layers)

    def _resolve_frames(self) -> list[QFrame]:
        resolved: list[QFrame] = []

        url_input = getattr(self.window, "url_input", None)
        hero = _ancestor_card(url_input if isinstance(url_input, QWidget) else None, "heroCard")
        if hero is not None:
            resolved.append(hero)

        status_host = self.window.findChild(QWidget, "statusRowHost")
        if status_host is not None:
            for frame in status_host.findChildren(QFrame):
                if frame.objectName() == "statusCard" and frame not in resolved:
                    resolved.append(frame)

        return resolved

    def _detach_from_quick_model(self, frames: list[QFrame]) -> None:
        """Remove only the prototype cards from the Quick repeater/mask model."""

        model = getattr(self.background, "card_model", None)
        cards = getattr(model, "cards", None)
        states = getattr(model, "_states", None)
        rows = getattr(model, "_rows", None)
        if model is None or not isinstance(cards, list) or not isinstance(states, list) or not isinstance(rows, dict):
            return

        removal_rows = sorted(
            {int(rows[frame]) for frame in frames if frame in rows},
            reverse=True,
        )
        for row in removal_rows:
            if row < 0 or row >= len(cards):
                continue
            model.beginRemoveRows(model.index(-1, -1), row, row)
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
            return float(quick.property("offsetX") or 0.0), float(quick.property("offsetY") or 0.0)
        except (RuntimeError, TypeError, ValueError):
            return 0.0, 0.0

    def paint_blurred_wallpaper(
        self,
        painter: QPainter,
        frame: QFrame,
        target: QRectF,
    ) -> None:
        item = self._ensure_scaled_item()
        quick = self.quick
        if item is None or quick is None:
            return

        try:
            quick_width = float(quick.width())
            quick_height = float(quick.height())
            offset_x, offset_y = self._quick_offset()
            image_x = (quick_width - float(item.width())) * 0.5 + offset_x
            image_y = (quick_height - float(item.height())) * 0.5 + offset_y
            top_left = frame.mapTo(self.window, QPoint(0, 0))
        except RuntimeError:
            return

        source = QRectF(
            float(top_left.x()) - image_x,
            float(top_left.y()) - image_y,
            float(frame.width()),
            float(frame.height()),
        )
        painter.drawPixmap(target, item, source)

    def _repaint_visible_layers(self, *, sync: bool) -> None:
        for layer in self._layers.values():
            try:
                if not layer.isVisible() or layer.visibleRegion().isEmpty():
                    continue
                if sync:
                    layer.repaint()
                else:
                    layer.update()
            except RuntimeError:
                continue

    def _repaint_for_scroll(self, _value: int) -> None:
        # This is intentionally synchronous, but only for the five small prototype
        # layers. Their glass pixels and QWidget contents therefore belong to the
        # same scroll/backing-store turn instead of two independent render loops.
        self._repaint_visible_layers(sync=True)

    def _invalidate_scaled_item(self, *_args: object) -> None:
        self._scaled_item = QPixmap()
        self._scaled_key = None
        self._repaint_visible_layers(sync=False)

    @Slot()
    def _sync_parallax_crop(self) -> None:
        offset = self._quick_offset()
        previous = self._last_quick_offset
        if previous is not None and abs(previous[0] - offset[0]) < 0.02 and abs(previous[1] - offset[1]) < 0.02:
            return
        self._last_quick_offset = offset
        self._repaint_visible_layers(sync=False)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if not isinstance(watched, QFrame):
            return False
        layer = self._layers.get(watched)
        if layer is None:
            return False

        event_type = event.type()
        if event_type == QEvent.Type.Resize:
            layer.setGeometry(watched.rect())
            layer.lower()
            layer.update()
        elif event_type in {QEvent.Type.Move, QEvent.Type.Show}:
            layer.lower()
            layer.update()
        return False


def install_scroll_local_glass(window: QMainWindow, visual: Any) -> ScrollLocalGlassController | None:
    """Install the narrow Product Source/status local-glass A/B experiment."""

    scroll = getattr(window, "_single_page_scroll", None)
    if not isinstance(scroll, QScrollArea):
        return None

    controller = ScrollLocalGlassController(window, visual, scroll)
    if controller.active_count <= 0:
        controller.deleteLater()
        return None

    window._scroll_local_glass = controller  # type: ignore[attr-defined]
    return controller
