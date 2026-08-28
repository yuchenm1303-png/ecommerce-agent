from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QStackedWidget, QWidget

from .native_background import _OVERSCAN


class WorkspaceTransitionBackdropRenderer:
    """Capture only the geometry-independent wallpaper behind ``modeStack``.

    Older transition code rendered the current QWidget page into an off-screen
    pixmap and separately rebuilt its glass. That duplicate presentation path was
    able to observe stale splitter geometry and produce a visibly different layout
    the instant the mode toggle was clicked. The transition now needs only a neutral
    cover, so this renderer deliberately has no page/card rendering capability.
    """

    def __init__(self, window: QWidget, visual: Any, stack: QStackedWidget) -> None:
        self.window = window
        self.visual = visual
        self.stack = stack
        self.root = window.centralWidget() if hasattr(window, "centralWidget") else None
        self.background = getattr(visual, "background", None)
        self._sharp = self._load_pixmap("_sharp_path")

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
        frame.fill(QColor(23, 38, 58))
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
        return self._empty_stack_frame()


__all__ = ["WorkspaceTransitionBackdropRenderer"]
