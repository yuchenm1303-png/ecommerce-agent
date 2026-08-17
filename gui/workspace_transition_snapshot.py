from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPoint, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap, QRegion
from PySide6.QtWidgets import QStackedWidget, QWidget

from .native_background import _OVERSCAN


class WorkspaceTransitionSnapshotRenderer:
    """Freeze the already-presented UI instead of rebuilding a second UI.

    The live application has two real renderers: the QQuickWindow owns wallpaper
    and glass, while the embedded QWidget tree owns labels, controls and tables.
    Transition frames must sample those exact renderers. Reconstructing glass from
    QWidget geometry creates a visually different "transition UI" and can expose
    a one-frame shape/layout jump before the fade even begins.

    ``prime_live_frame`` is called directly from the mode-switch click path before
    any transition preparation can change presentation state. The transition then
    consumes that exact frozen outgoing frame. Incoming capture happens only after
    the new Quick frame has been presented, so both sides of the animation use the
    same production renderers as the live application.
    """

    def __init__(self, window: QWidget, visual: Any, stack: QStackedWidget) -> None:
        self.window = window
        self.visual = visual
        self.stack = stack
        self.root = window.centralWidget() if hasattr(window, "centralWidget") else None
        self.background = getattr(visual, "background", None)
        self._wallpaper = self._load_wallpaper()
        self._primed_neutral = QPixmap()
        self._primed_composite = QPixmap()

    def _load_wallpaper(self) -> QPixmap:
        path = getattr(self.background, "_sharp_path", None)
        if path is None:
            return QPixmap()
        try:
            return QPixmap(str(path))
        except RuntimeError:
            return QPixmap()

    @staticmethod
    def _empty_frame(widget: QWidget) -> QPixmap:
        dpr = max(1.0, float(widget.devicePixelRatioF()))
        frame = QPixmap(
            max(1, int(round(widget.width() * dpr))),
            max(1, int(round(widget.height() * dpr))),
        )
        frame.setDevicePixelRatio(dpr)
        frame.fill(Qt.GlobalColor.transparent)
        return frame

    @staticmethod
    def _fit_frame(source: QPixmap, widget: QWidget) -> QPixmap:
        if source.isNull() or widget.width() <= 0 or widget.height() <= 0:
            return QPixmap(source)

        dpr = max(1.0, float(widget.devicePixelRatioF()))
        target_width = max(1, int(round(widget.width() * dpr)))
        target_height = max(1, int(round(widget.height() * dpr)))
        same_pixels = source.width() == target_width and source.height() == target_height
        same_dpr = abs(float(source.devicePixelRatio()) - dpr) <= 1e-3
        if same_pixels and same_dpr:
            return QPixmap(source)

        fitted = source.scaled(
            target_width,
            target_height,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        fitted.setDevicePixelRatio(dpr)
        return fitted

    def _capture_quick_for_stack(self) -> QPixmap:
        root = self.root
        quick = getattr(self.background, "quick_window", None)
        if root is None or quick is None:
            return QPixmap()
        try:
            image = quick.grabWindow()
        except RuntimeError:
            return QPixmap()
        if image.isNull():
            return QPixmap()

        # NativeWindowShell keeps the embedded QWidget child exactly fitted to the
        # Quick owner client, so both renderers share the same logical root space.
        full = self._fit_frame(QPixmap.fromImage(image), root)
        if full.isNull():
            return QPixmap()

        top_left = self.stack.mapTo(root, QPoint(0, 0))
        dpr = max(1.0, float(full.devicePixelRatio()))
        pixel_rect = QRect(
            int(round(top_left.x() * dpr)),
            int(round(top_left.y() * dpr)),
            max(1, int(round(self.stack.width() * dpr))),
            max(1, int(round(self.stack.height() * dpr))),
        )
        cropped = full.copy(pixel_rect)
        cropped.setDevicePixelRatio(dpr)
        return self._fit_frame(cropped, self.stack)

    def _render_current_page(self) -> QPixmap:
        page = self.stack.currentWidget()
        if (
            page is None
            or self.stack.width() <= 0
            or self.stack.height() <= 0
            or page.width() <= 0
            or page.height() <= 0
        ):
            return QPixmap()

        frame = self._empty_frame(self.stack)
        target_offset = page.mapTo(self.stack, QPoint(0, 0))
        page.render(
            frame,
            target_offset,
            QRegion(),
            QWidget.RenderFlag.DrawChildren,
        )
        return frame

    def _capture_composite_live(self) -> QPixmap:
        quick_frame = self._capture_quick_for_stack()
        widget_frame = self._render_current_page()
        if quick_frame.isNull():
            return self._fit_frame(widget_frame, self.stack)
        if widget_frame.isNull():
            return self._fit_frame(quick_frame, self.stack)

        result = self._empty_frame(self.stack)
        painter = QPainter(result)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.drawPixmap(0, 0, self._fit_frame(quick_frame, self.stack))
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.drawPixmap(0, 0, self._fit_frame(widget_frame, self.stack))
        painter.end()
        return result

    def _capture_neutral_live_position(self) -> QPixmap:
        root = self.root
        quick = getattr(self.background, "quick_window", None)
        wallpaper = self._wallpaper
        if (
            root is None
            or quick is None
            or wallpaper.isNull()
            or quick.width() <= 0
            or quick.height() <= 0
            or root.width() <= 0
            or root.height() <= 0
        ):
            return self._capture_quick_for_stack()

        # Neutral is intentionally only the Fuji wallpaper. It is not a second
        # card renderer; it simply preserves the original fade-through backdrop.
        root_frame = self._empty_frame(root)
        root_frame.fill(QColor(23, 38, 58))
        painter = QPainter(root_frame)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        root_w = float(quick.width())
        root_h = float(quick.height())
        item_w = root_w * float(_OVERSCAN)
        item_h = root_h * float(_OVERSCAN)
        try:
            item_x = float(quick.property("imageX"))
            item_y = float(quick.property("imageY"))
        except (RuntimeError, TypeError, ValueError):
            item_x = (root_w - item_w) * 0.5
            item_y = (root_h - item_h) * 0.5

        source_w = max(1.0, float(wallpaper.width()))
        source_h = max(1.0, float(wallpaper.height()))
        scale = max(item_w / source_w, item_h / source_h)
        visible_source_w = item_w / max(scale, 1e-9)
        visible_source_h = item_h / max(scale, 1e-9)
        source_rect = QRectF(
            (source_w - visible_source_w) * 0.5,
            (source_h - visible_source_h) * 0.5,
            visible_source_w,
            visible_source_h,
        )
        painter.drawPixmap(QRectF(item_x, item_y, item_w, item_h), wallpaper, source_rect)
        painter.end()

        top_left = self.stack.mapTo(root, QPoint(0, 0))
        dpr = max(1.0, float(root_frame.devicePixelRatio()))
        pixel_rect = QRect(
            int(round(top_left.x() * dpr)),
            int(round(top_left.y() * dpr)),
            max(1, int(round(self.stack.width() * dpr))),
            max(1, int(round(self.stack.height() * dpr))),
        )
        cropped = root_frame.copy(pixel_rect)
        cropped.setDevicePixelRatio(dpr)
        return self._fit_frame(cropped, self.stack)

    def prime_live_frame(self) -> bool:
        """Freeze exactly what is on screen before transition preparation starts."""

        composite = self._capture_composite_live()
        if composite.isNull():
            self._primed_composite = QPixmap()
            self._primed_neutral = QPixmap()
            return False
        self._primed_composite = composite
        neutral = self._capture_neutral_live_position()
        self._primed_neutral = neutral if not neutral.isNull() else QPixmap(composite)
        return True

    def capture_neutral(self) -> QPixmap:
        if not self._primed_neutral.isNull():
            return QPixmap(self._primed_neutral)
        return self._capture_neutral_live_position()

    def capture_composite(self) -> QPixmap:
        if not self._primed_composite.isNull():
            result = QPixmap(self._primed_composite)
            self._primed_composite = QPixmap()
            self._primed_neutral = QPixmap()
            return result
        return self._capture_composite_live()


__all__ = ["WorkspaceTransitionSnapshotRenderer"]
