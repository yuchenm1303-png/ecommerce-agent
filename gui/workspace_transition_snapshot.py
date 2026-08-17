from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPixmap, QRegion
from PySide6.QtWidgets import QFrame, QStackedWidget, QWidget

from .native_background import _GLASS_RADIUS, _NORMAL_GLASS_ALPHA, _OVERSCAN


class WorkspaceTransitionSnapshotRenderer:
    """Build one coherent workspace frame from one authoritative geometry source.

    The live application renders its wallpaper/glass in a threaded QQuickWindow
    while all labels, controls and tables remain QWidget content.  Grabbing those
    two renderers independently during a QStackedWidget mode change can combine
    a new QWidget page with the previous Quick glass mask for one frame.

    Transition frames therefore never sample Quick pixels.  They reconstruct the
    same sharp wallpaper + preblurred glass directly from the current QWidget
    card geometry, then render the current page on top.  The transition timing and
    live Quick presentation are untouched; only the cached transition snapshot is
    made atomic.
    """

    def __init__(self, window: QWidget, visual: Any, stack: QStackedWidget) -> None:
        self.window = window
        self.visual = visual
        self.stack = stack
        self.root = window.centralWidget() if hasattr(window, "centralWidget") else None
        self.background = getattr(visual, "background", None)
        self._sharp = self._load_pixmap("_sharp_path")
        self._blur = self._load_pixmap("_blur_path")

    def _load_pixmap(self, attribute: str) -> QPixmap:
        path = getattr(self.background, attribute, None)
        if path is None:
            return QPixmap()
        try:
            return QPixmap(str(path))
        except RuntimeError:
            return QPixmap()

    def _empty_stack_frame(self) -> QPixmap:
        dpr = max(1.0, float(self.stack.devicePixelRatioF()))
        frame = QPixmap(
            max(1, int(round(self.stack.width() * dpr))),
            max(1, int(round(self.stack.height() * dpr))),
        )
        frame.setDevicePixelRatio(dpr)
        frame.fill(Qt.GlobalColor.transparent)
        return frame

    def _background_for_stack(self, source: QPixmap) -> QPixmap:
        root = self.root
        if (
            root is None
            or source.isNull()
            or root.width() <= 0
            or root.height() <= 0
            or self.stack.width() <= 0
            or self.stack.height() <= 0
        ):
            return QPixmap()

        dpr = max(1.0, float(root.devicePixelRatioF()))
        root_frame = QPixmap(
            max(1, int(round(root.width() * dpr))),
            max(1, int(round(root.height() * dpr))),
        )
        root_frame.setDevicePixelRatio(dpr)
        root_frame.fill(QColor(23, 38, 58))

        quick = getattr(self.background, "quick_window", None)
        root_w = float(root.width())
        root_h = float(root.height())
        if quick is not None:
            try:
                if quick.width() > 0 and quick.height() > 0:
                    root_w = float(quick.width())
                    root_h = float(quick.height())
            except RuntimeError:
                pass

        item_w = root_w * float(_OVERSCAN)
        item_h = root_h * float(_OVERSCAN)
        item_x = (root_w - item_w) * 0.5
        item_y = (root_h - item_h) * 0.5
        if quick is not None:
            try:
                item_x = float(quick.property("imageX"))
                item_y = float(quick.property("imageY"))
            except (RuntimeError, TypeError, ValueError):
                pass

        source_w = max(1.0, float(source.width()))
        source_h = max(1.0, float(source.height()))
        scale = max(item_w / source_w, item_h / source_h)
        visible_source_w = item_w / max(scale, 1e-9)
        visible_source_h = item_h / max(scale, 1e-9)
        source_rect = QRectF(
            (source_w - visible_source_w) * 0.5,
            (source_h - visible_source_h) * 0.5,
            visible_source_w,
            visible_source_h,
        )
        target_rect = QRectF(item_x, item_y, item_w, item_h)

        painter = QPainter(root_frame)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawPixmap(target_rect, source, source_rect)
        painter.end()

        stack_top_left = self.stack.mapTo(root, QPoint(0, 0))
        pixel_rect = QRectF(
            float(stack_top_left.x()) * dpr,
            float(stack_top_left.y()) * dpr,
            float(self.stack.width()) * dpr,
            float(self.stack.height()) * dpr,
        ).toAlignedRect()
        cropped = root_frame.copy(pixel_rect)
        cropped.setDevicePixelRatio(dpr)
        return cropped

    def capture_neutral(self) -> QPixmap:
        frame = self._background_for_stack(self._sharp)
        if not frame.isNull():
            return frame
        fallback = self._empty_stack_frame()
        fallback.fill(QColor(23, 38, 58))
        return fallback

    def _capture_blur(self) -> QPixmap:
        return self._background_for_stack(self._blur)

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

        frame = self._empty_stack_frame()
        target_offset = page.mapTo(self.stack, QPoint(0, 0))
        page.render(
            frame,
            target_offset,
            QRegion(),
            QWidget.RenderFlag.DrawChildren,
        )
        return frame

    @staticmethod
    def _scaled_rect(rect: QRectF, scale: float) -> QRectF:
        if abs(scale - 1.0) <= 1e-6:
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

    def _card_geometry(self, frame: QFrame) -> tuple[QRectF, QRectF] | None:
        try:
            if (
                not frame.isVisibleTo(self.window)
                or frame.width() <= 0
                or frame.height() <= 0
            ):
                return None

            stack_global = self.stack.mapToGlobal(QPoint(0, 0))
            frame_global = frame.mapToGlobal(QPoint(0, 0))
            card_rect = QRectF(
                float(frame_global.x() - stack_global.x()),
                float(frame_global.y() - stack_global.y()),
                float(frame.width()),
                float(frame.height()),
            )
            clip_rect = QRectF(
                0.0,
                0.0,
                float(self.stack.width()),
                float(self.stack.height()),
            )

            ancestor = frame.parentWidget()
            while ancestor is not None:
                if not ancestor.isVisibleTo(self.window):
                    return None
                ancestor_global = ancestor.mapToGlobal(QPoint(0, 0))
                ancestor_rect = QRectF(
                    float(ancestor_global.x() - stack_global.x()),
                    float(ancestor_global.y() - stack_global.y()),
                    float(ancestor.width()),
                    float(ancestor.height()),
                )
                clip_rect = clip_rect.intersected(ancestor_rect)
                if clip_rect.isEmpty() or ancestor is self.stack:
                    break
                ancestor = ancestor.parentWidget()

            if card_rect.intersected(clip_rect).isEmpty():
                return None
            return card_rect, clip_rect
        except RuntimeError:
            return None

    def _paint_glass(self, painter: QPainter, blur: QPixmap) -> None:
        glass = getattr(self.visual, "_glass", None)
        if not isinstance(glass, dict):
            return

        for frame, proxy in glass.items():
            if not isinstance(frame, QFrame):
                continue
            geometry = self._card_geometry(frame)
            if geometry is None:
                continue
            card_rect, clip_rect = geometry

            try:
                scale = float(getattr(proxy, "surface_scale", 1.0))
            except (RuntimeError, TypeError, ValueError):
                scale = 1.0
            scale = max(0.96, min(1.04, scale))
            target = self._scaled_rect(card_rect, scale)

            try:
                alpha = float(getattr(proxy, "overlay_alpha", _NORMAL_GLASS_ALPHA))
            except (RuntimeError, TypeError, ValueError):
                alpha = _NORMAL_GLASS_ALPHA
            alpha = max(_NORMAL_GLASS_ALPHA, min(255.0, alpha))

            painter.save()
            painter.setClipRect(clip_rect)
            path = QPainterPath()
            path.addRoundedRect(
                target,
                float(_GLASS_RADIUS) * scale,
                float(_GLASS_RADIUS) * scale,
            )
            painter.setClipPath(path, Qt.ClipOperation.IntersectClip)
            if not blur.isNull():
                # Draw the complete stack-aligned blurred wallpaper through the
                # authoritative card clip.  No source-rect math and no Quick
                # texture-mask frame can drift from the QWidget geometry.
                painter.drawPixmap(0, 0, blur)
            painter.fillRect(
                target,
                QColor(0, 0, 0, int(round(alpha))),
            )
            painter.restore()

    def capture_composite(self) -> QPixmap:
        neutral = self.capture_neutral()
        widget_frame = self._render_current_page()
        if neutral.isNull() and widget_frame.isNull():
            return QPixmap()

        result = self._empty_stack_frame()
        painter = QPainter(result)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        if neutral.isNull():
            painter.fillRect(QRectF(self.stack.rect()), QColor(23, 38, 58))
        else:
            painter.drawPixmap(0, 0, neutral)

        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        self._paint_glass(painter, self._capture_blur())
        if not widget_frame.isNull():
            painter.drawPixmap(0, 0, widget_frame)
        painter.end()
        return result


__all__ = ["WorkspaceTransitionSnapshotRenderer"]
