from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QStackedWidget, QWidget

from .native_background import _OVERSCAN


class WorkspaceTransitionSnapshotRenderer:
    """Render only the neutral Fuji transition backdrop.

    Workspace cards, labels, controls and Quick glass are never captured or
    reconstructed here. The live renderers remain authoritative for both the
    outgoing and incoming workspace. This helper exists only to keep the Fuji
    wallpaper aligned with the live Quick background while the real workspace is
    fully covered during the atomic mode handoff.
    """

    def __init__(self, window: QWidget, visual: Any, stack: QStackedWidget) -> None:
        self.window = window
        self.visual = visual
        self.stack = stack
        self.root = window.centralWidget() if hasattr(window, "centralWidget") else None
        self.background = getattr(visual, "background", None)
        self._wallpaper = self._load_wallpaper()

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

    def capture_neutral(self) -> QPixmap:
        root = self.root
        quick = getattr(self.background, "quick_window", None)
        wallpaper = self._wallpaper
        if (
            root is None
            or wallpaper.isNull()
            or root.width() <= 0
            or root.height() <= 0
            or self.stack.width() <= 0
            or self.stack.height() <= 0
        ):
            frame = self._empty_frame(self.stack)
            frame.fill(QColor(23, 38, 58))
            return frame

        root_frame = self._empty_frame(root)
        root_frame.fill(QColor(23, 38, 58))

        root_w = float(root.width())
        root_h = float(root.height())
        item_x = (root_w - root_w * float(_OVERSCAN)) * 0.5
        item_y = (root_h - root_h * float(_OVERSCAN)) * 0.5
        item_w = root_w * float(_OVERSCAN)
        item_h = root_h * float(_OVERSCAN)

        if quick is not None:
            try:
                if quick.width() > 0 and quick.height() > 0:
                    root_w = float(quick.width())
                    root_h = float(quick.height())
                    item_w = root_w * float(_OVERSCAN)
                    item_h = root_h * float(_OVERSCAN)
                    item_x = float(quick.property("imageX"))
                    item_y = float(quick.property("imageY"))
            except (RuntimeError, TypeError, ValueError):
                item_w = root_w * float(_OVERSCAN)
                item_h = root_h * float(_OVERSCAN)
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

        painter = QPainter(root_frame)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawPixmap(
            QRectF(item_x, item_y, item_w, item_h),
            wallpaper,
            source_rect,
        )
        painter.end()

        top_left = self.stack.mapTo(root, QPoint(0, 0))
        dpr = max(1.0, float(root_frame.devicePixelRatio()))
        pixel_rect = QRectF(
            float(top_left.x()) * dpr,
            float(top_left.y()) * dpr,
            float(self.stack.width()) * dpr,
            float(self.stack.height()) * dpr,
        ).toAlignedRect()
        cropped = root_frame.copy(pixel_rect)
        cropped.setDevicePixelRatio(dpr)
        return self._fit_frame(cropped, self.stack)


__all__ = ["WorkspaceTransitionSnapshotRenderer"]
