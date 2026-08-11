from __future__ import annotations

from PySide6.QtCore import QEvent, QModelIndex, QObject, QPoint, QRectF, Qt, QTimer
from PySide6.QtGui import QCursor, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QApplication,
    QFrame,
    QGraphicsEffect,
    QMainWindow,
)

from .native_background import NativeQuickBackground
from .visual_style import NEKRO_STYLE


_GLASS_NAMES = {"glassCard", "heroCard", "statusCard", "microCard"}
_NORMAL_GLASS_ALPHA = 64.0
_EFFECT_BOUND_SCALE = 1.04


class _CardScaleEffect(QGraphicsEffect):
    """Scale one cached QWidget subtree image around the card center.

    Quick owns the live glass shell. QWidget content is rasterized once when a
    scale interaction becomes active, then the same pixmap is transformed for the
    remaining 300 ms motion. If the real source changes, Qt calls sourceChanged()
    and the cache is rebuilt on the next draw. This matches browser compositor
    behavior much more closely than repainting every child on every scale tick.
    """

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self._scale = 1.0
        self._cached_source: QPixmap | None = None
        self._cached_offset = QPoint()
        self.setEnabled(False)

    @property
    def scale(self) -> float:
        return self._scale

    def _clear_source_cache(self) -> None:
        self._cached_source = None
        self._cached_offset = QPoint()

    def sourceChanged(self, flags) -> None:  # noqa: ANN001, N802
        # QGraphicsEffect explicitly requires custom caches to be purged whenever
        # the source appearance or bounds change. Animation-only update() calls do
        # not invalidate the source, so steady transform frames keep the pixmap.
        self._clear_source_cache()
        super().sourceChanged(flags)

    def set_scale(self, scale: float) -> None:
        scale = max(0.96, min(_EFFECT_BOUND_SCALE, float(scale)))
        if abs(scale - self._scale) <= 1e-5:
            return
        self._scale = scale
        active = abs(scale - 1.0) > 1e-4
        if self.isEnabled() != active:
            if active:
                self._clear_source_cache()
            self.setEnabled(active)
            # The effect uses one fixed maximum bounding rect for its whole active
            # lifetime. Intermediate hover frames invalidate pixels only.
            self.updateBoundingRect()
            if not active:
                self._clear_source_cache()
        self.update()

    def boundingRectFor(self, source_rect: QRectF) -> QRectF:  # noqa: N802
        if not self.isEnabled():
            return QRectF(source_rect)
        center = source_rect.center()
        half_w = source_rect.width() * _EFFECT_BOUND_SCALE * 0.5
        half_h = source_rect.height() * _EFFECT_BOUND_SCALE * 0.5
        return QRectF(
            center.x() - half_w,
            center.y() - half_h,
            half_w * 2.0,
            half_h * 2.0,
        )

    def _source_snapshot(self) -> tuple[QPixmap | None, QPoint]:
        pixmap = self._cached_source
        if pixmap is not None and not pixmap.isNull():
            return pixmap, self._cached_offset

        offset = QPoint()
        pixmap = self.sourcePixmap(
            Qt.CoordinateSystem.LogicalCoordinates,
            offset,
            QGraphicsEffect.PixmapPadMode.NoPad,
        )
        if pixmap.isNull():
            return None, QPoint()

        self._cached_source = pixmap
        self._cached_offset = QPoint(offset)
        return pixmap, self._cached_offset

    def draw(self, painter: QPainter) -> None:  # type: ignore[override]
        scale = self._scale
        if abs(scale - 1.0) <= 1e-4:
            self.drawSource(painter)
            return

        pixmap, offset = self._source_snapshot()
        if pixmap is None:
            self.drawSource(painter)
            return

        source_rect = self.sourceBoundingRect(Qt.CoordinateSystem.LogicalCoordinates)
        center = source_rect.center()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.translate(center)
        painter.scale(scale, scale)
        painter.translate(-center)
        painter.drawPixmap(offset, pixmap)
        painter.restore()


class NativeGlassProxy(QObject):
    """Synchronize one reference-web interaction across Quick glass and QWidget content."""

    def __init__(self, frame: QFrame, background: NativeQuickBackground) -> None:
        super().__init__(frame)
        self.frame = frame
        self.background = background
        self._surface_scale = 1.0
        self._overlay_alpha = _NORMAL_GLASS_ALPHA
        self._scale_effect = _CardScaleEffect(frame)
        frame.setGraphicsEffect(self._scale_effect)

    @property
    def surface_scale(self) -> float:
        return self._surface_scale

    @property
    def overlay_alpha(self) -> float:
        return self._overlay_alpha

    def set_interaction(self, *, scale: float, overlay_alpha: float) -> None:
        scale = max(0.96, min(1.04, float(scale)))
        overlay_alpha = max(_NORMAL_GLASS_ALPHA, min(255.0, float(overlay_alpha)))
        if (
            abs(scale - self._surface_scale) < 0.0001
            and abs(overlay_alpha - self._overlay_alpha) < 0.1
        ):
            return
        self._surface_scale = scale
        self._overlay_alpha = overlay_alpha

        # One state drives both visual owners. Quick transforms the actual glass
        # shell/tint, while QWidget transforms every title/icon/control above it.
        self.background.set_card_presentation(
            self.frame,
            scale=scale,
            alpha=overlay_alpha,
        )
        self._scale_effect.set_scale(scale)

    def sync_geometry(self) -> None:
        self.background.schedule_mask_update()

    def cleanup(self) -> None:
        try:
            self.background.set_card_presentation(
                self.frame,
                scale=1.0,
                alpha=_NORMAL_GLASS_ALPHA,
            )
            self._scale_effect.set_scale(1.0)
            self.frame.setGraphicsEffect(None)
        except RuntimeError:
            pass


class NativeVisualStyleController(QObject):
    """Native Quick background/base glass with QWidget interaction feedback."""

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self.window = window
        self._glass: dict[QFrame, NativeGlassProxy] = {}
        self._cursor_installed = False
        self._mode_stack_glass_connected = False

        # The QWidget tree remains the baseline UI. Only the top-level client
        # surface is translucent so the native Quick scene can present below it.
        window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        window.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        window.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.central = window.centralWidget()
        if self.central is not None:
            self.central.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.central.setAutoFillBackground(False)
            # This filter only suppresses AtmosphereWidget's legacy wallpaper
            # paint. Installing it globally made every Qt event cross Python.
            self.central.installEventFilter(self)

        # Reuse baseline style constants verbatim. No replacement card border is
        # introduced; only the reference site's interaction presentation differs.
        window.setStyleSheet(window.styleSheet() + "\n" + NEKRO_STYLE)

        self.background = NativeQuickBackground(window)
        for frame in window.findChildren(QFrame):
            if frame.objectName() in _GLASS_NAMES:
                frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
                self._glass[frame] = NativeGlassProxy(frame, self.background)

        self._install_cursor()
        window.destroyed.connect(self._cleanup)
        QTimer.singleShot(0, self._sync_glass)

    def surface_for(self, frame: QFrame) -> NativeGlassProxy | None:
        return self._glass.get(frame)

    def refresh_glass_frames(self) -> int:
        """Register glass cards created after the native Quick scene started."""

        new_frames = [
            frame
            for frame in self.window.findChildren(QFrame)
            if frame.objectName() in _GLASS_NAMES and frame not in self._glass
        ]
        if not new_frames:
            self.background.schedule_mask_update()
            return 0

        model = self.background.card_model
        first_row = len(model.cards)
        last_row = first_row + len(new_frames) - 1
        model.beginInsertRows(QModelIndex(), first_row, last_row)
        try:
            for frame in new_frames:
                row = len(model.cards)
                frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
                model.cards.append(frame)
                model._rows[frame] = row
                model._states.append(
                    {
                        "cardX": 0.0,
                        "cardY": 0.0,
                        "cardW": 0.0,
                        "cardH": 0.0,
                        "clipX": 0.0,
                        "clipY": 0.0,
                        "clipW": 0.0,
                        "clipH": 0.0,
                        "cardAlpha": _NORMAL_GLASS_ALPHA,
                        "cardScale": 1.0,
                        "cardVisible": False,
                    }
                )
                self._glass[frame] = NativeGlassProxy(frame, self.background)
        finally:
            model.endInsertRows()

        # NativeQuickBackground intentionally watches only relevant widgets, not
        # the whole QApplication. Extend that same scoped watch set to Batch card
        # ancestors so stack changes, layout changes and resizes refresh the mask.
        for frame in new_frames:
            current = frame
            while current is not None:
                if current not in self.background._geometry_watch:
                    self.background._geometry_watch.add(current)
                    current.installEventFilter(self.background)
                if current is self.window:
                    break
                current = current.parentWidget()

        # Batch owns its own tables/scroll areas, created after background init.
        # Connect only scroll areas under the newly registered cards.
        scroll_areas: set[QAbstractScrollArea] = set()
        for frame in new_frames:
            scroll_areas.update(frame.findChildren(QAbstractScrollArea))
        for area in scroll_areas:
            area.verticalScrollBar().valueChanged.connect(self.background.schedule_mask_update)
            area.horizontalScrollBar().valueChanged.connect(self.background.schedule_mask_update)

        mode_stack = getattr(self.window, "mode_stack", None)
        if mode_stack is not None and not self._mode_stack_glass_connected:
            mode_stack.currentChanged.connect(
                lambda *_: QTimer.singleShot(0, self.background.schedule_mask_update)
            )
            self._mode_stack_glass_connected = True

        QTimer.singleShot(0, self.background.schedule_mask_update)
        return len(new_frames)

    def _install_cursor(self) -> None:
        pixmap = QPixmap(10, 10)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(Qt.GlobalColor.white)
        painter.drawEllipse(QRectF(1.0, 1.0, 8.0, 8.0))
        painter.end()
        QApplication.setOverrideCursor(QCursor(pixmap, 4, 4))
        self._cursor_installed = True

    def _sync_glass(self) -> None:
        for surface in self._glass.values():
            surface.sync_geometry()
        self.background.schedule_mask_update()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        # Suppress only the baseline AtmosphereWidget wallpaper paint. Card
        # content/controls remain the original QWidget implementation.
        if watched is self.central and event.type() == QEvent.Type.Paint:
            return True
        return False

    def _cleanup(self) -> None:
        if self.central is not None:
            try:
                self.central.removeEventFilter(self)
            except RuntimeError:
                pass
        for surface in tuple(self._glass.values()):
            try:
                surface.cleanup()
            except RuntimeError:
                pass
        self.background.shutdown()
        if self._cursor_installed:
            QApplication.restoreOverrideCursor()
            self._cursor_installed = False


def install_native_visual_style(window: QMainWindow) -> NativeVisualStyleController:
    controller = NativeVisualStyleController(window)
    window._visual_style = controller  # type: ignore[attr-defined]
    return controller
