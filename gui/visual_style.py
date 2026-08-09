from __future__ import annotations

import base64
import math
import time
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QImage, QPainter, QPainterPath, QPixmap, QRadialGradient, QRegion
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsBlurEffect,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QMainWindow,
    QScrollBar,
    QWidget,
)


_GLASS_NAMES = {"glassCard", "heroCard", "statusCard", "microCard"}
_WALLPAPER_ASSET = Path(__file__).resolve().parent / "assets" / "fuji_sakura_wallpaper.jpg.b64"


NEKRO_STYLE = r"""
QWidget#root {
    color: #ffffff;
    background: transparent;
    font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
    font-size: 13px;
}

QWidget#workspaceHost,
QWidget#sideHost,
QScrollArea,
QScrollArea > QWidget > QWidget {
    background: transparent;
    border: 0;
}

QFrame#glassCard,
QFrame#heroCard,
QFrame#statusCard,
QFrame#microCard {
    background: transparent;
    border: 0;
    border-radius: 6px;
}

QLabel#brandMark {
    color: rgba(255,255,255,165);
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 1px;
}

QLabel#appTitle {
    color: #ffffff;
    font-size: 31px;
    font-weight: 700;
}

QLabel#subtle,
QLabel#cardHint {
    color: rgba(255,255,255,165);
}

QLabel#cardTitle {
    color: #ffffff;
    font-size: 14px;
    font-weight: 700;
}

QLabel#sectionEyebrow {
    color: rgba(255,255,255,140);
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 1px;
}

QLabel#phaseBadge {
    padding: 8px 13px;
    color: #efefef;
    background-color: rgba(0,0,0,64);
    border: 0;
    border-radius: 6px;
    font-weight: 600;
}

QLineEdit,
QSpinBox {
    min-height: 38px;
    padding: 0 11px;
    color: #ffffff;
    background-color: rgba(0,0,0,64);
    border: 1px solid rgba(255,255,255,28);
    border-radius: 6px;
    selection-background-color: rgba(255,255,255,64);
    selection-color: #ffffff;
}

QLineEdit:hover,
QSpinBox:hover {
    background-color: rgba(0,0,0,78);
    border-color: rgba(255,255,255,42);
}

QLineEdit:focus,
QSpinBox:focus {
    background-color: rgba(0,0,0,86);
    border-color: rgba(255,255,255,96);
}

QPushButton {
    min-height: 38px;
    padding: 0 16px;
    color: #ffffff;
    background-color: rgba(0,0,0,64);
    border: 0;
    border-radius: 6px;
    font-weight: 600;
}

QPushButton:hover {
    background-color: rgba(0,0,0,102);
}

QPushButton:pressed {
    background-color: rgba(0,0,0,64);
}

QPushButton#primaryButton {
    min-width: 140px;
    color: #ffffff;
    background-color: rgba(255,255,255,48);
    border: 0;
    font-weight: 700;
}

QPushButton#primaryButton:hover {
    background-color: rgba(255,255,255,72);
}

QPushButton#dangerButton {
    background-color: rgba(70,0,18,86);
}

QPushButton#dangerButton:hover {
    background-color: rgba(92,0,24,118);
}

QPushButton#quietButton {
    background-color: rgba(0,0,0,64);
}

QPushButton:disabled {
    color: rgba(255,255,255,76);
    background-color: rgba(0,0,0,34);
}

QCheckBox {
    spacing: 8px;
    color: rgba(255,255,255,210);
}

QCheckBox::indicator {
    width: 16px;
    height: 16px;
    background-color: rgba(0,0,0,64);
    border: 1px solid rgba(255,255,255,72);
    border-radius: 4px;
}

QCheckBox::indicator:checked {
    background-color: rgba(255,255,255,118);
    border-color: rgba(255,255,255,190);
}

QTableWidget {
    color: #ffffff;
    background-color: rgba(0,0,0,42);
    alternate-background-color: rgba(255,255,255,9);
    border: 0;
    border-radius: 6px;
    gridline-color: rgba(255,255,255,14);
    selection-background-color: rgba(255,255,255,42);
    selection-color: #ffffff;
}

QTableWidget::item {
    padding: 7px 8px;
    border-bottom: 1px solid rgba(255,255,255,12);
}

QHeaderView::section {
    padding: 8px 8px;
    color: rgba(255,255,255,220);
    background-color: rgba(255,255,255,24);
    border: 0;
    border-bottom: 1px solid rgba(255,255,255,20);
    font-weight: 650;
}

QPlainTextEdit {
    color: #efefef;
    background-color: rgba(0,0,0,64);
    border: 0;
    border-radius: 6px;
    padding: 9px;
    selection-background-color: rgba(255,255,255,54);
    font-family: "Cascadia Mono", "Consolas", monospace;
    font-size: 12px;
}

QScrollBar:vertical {
    width: 6px;
    background: transparent;
    margin: 0;
}

QScrollBar::handle:vertical {
    min-height: 24px;
    border-radius: 3px;
    background: #eeeeee;
}

QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical,
QScrollBar::sub-page:vertical {
    height: 0;
    background: transparent;
}

QScrollBar:horizontal {
    height: 6px;
    background: transparent;
    margin: 0;
}

QScrollBar::handle:horizontal {
    min-width: 24px;
    border-radius: 3px;
    background: #eeeeee;
}

QScrollBar::add-line:horizontal,
QScrollBar::sub-line:horizontal,
QScrollBar::add-page:horizontal,
QScrollBar::sub-page:horizontal {
    width: 0;
    background: transparent;
}

QSplitter::handle {
    background: transparent;
    width: 12px;
    height: 12px;
}
"""


def _load_wallpaper() -> QPixmap:
    """Decode and validate the bundled Fuji/sakura wallpaper once at startup."""

    try:
        encoded = _WALLPAPER_ASSET.read_text(encoding="ascii")
        compact = "".join(encoded.split())
        data = base64.b64decode(compact, validate=True)
    except OSError as exc:
        raise RuntimeError(f"Wallpaper asset cannot be read: {_WALLPAPER_ASSET}") from exc
    except ValueError as exc:
        raise RuntimeError(f"Wallpaper asset is not valid base64: {_WALLPAPER_ASSET}") from exc

    if len(data) <= 100_000 or not data.startswith(b"\xff\xd8\xff") or not data.endswith(b"\xff\xd9"):
        raise RuntimeError(
            "Wallpaper asset is not a complete JPEG: "
            f"path={_WALLPAPER_ASSET}, bytes={len(data)}"
        )

    image = QImage.fromData(data)
    if image.isNull():
        raise RuntimeError(
            "Qt could not decode the bundled wallpaper image: "
            f"path={_WALLPAPER_ASSET}, bytes={len(data)}"
        )

    pixmap = QPixmap.fromImage(image)
    if pixmap.isNull():
        raise RuntimeError(f"Qt could not create a wallpaper pixmap: {_WALLPAPER_ASSET}")
    return pixmap


def _blur_pixmap(source: QPixmap, radius: float = 10.0) -> QPixmap:
    """Blur once per wallpaper rebuild, never during parallax animation."""

    if source.isNull():
        return QPixmap()
    result = QPixmap(source.size())
    result.fill(Qt.transparent)
    scene = QGraphicsScene()
    item = QGraphicsPixmapItem(source)
    blur = QGraphicsBlurEffect()
    blur.setBlurRadius(radius)
    item.setGraphicsEffect(blur)
    scene.addItem(item)
    painter = QPainter(result)
    scene.render(painter, QRectF(result.rect()), QRectF(source.rect()), Qt.IgnoreAspectRatio)
    painter.end()
    return result


class BackgroundLayer(QWidget):
    """One lightweight wallpaper transform shared by the background and glass.

    The wallpaper is overscanned and pre-rendered once. Runtime animation is
    integer 1:1 source cropping only: no per-frame resize, pixmap allocation or
    blur. Glass consumes the exact same source rect, so it can never drift away
    from the visible wallpaper.
    """

    scene_changed = Signal()
    transform_changed = Signal()

    _OVERSCAN = 1.065
    _TRAVEL = 0.86
    _FRAME_MS = 16
    _EASE_TAU = 0.090
    _SETTLE_PX = 0.35

    def __init__(self, window: QMainWindow) -> None:
        central = window.centralWidget()
        super().__init__(central)
        self.window = window
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setFocusPolicy(Qt.NoFocus)

        self._source = _load_wallpaper()
        self._cover = QPixmap()
        self._render = QPixmap()
        self._blurred = QPixmap()
        self._source_rect = QRect()

        self._offset = QPointF(0.0, 0.0)
        self._target = QPointF(0.0, 0.0)
        self._pending_pointer: QPoint | None = None
        self._last_tick = 0.0

        self._rebuild_timer = QTimer(self)
        self._rebuild_timer.setSingleShot(True)
        self._rebuild_timer.setInterval(160)
        self._rebuild_timer.timeout.connect(self._rebuild)

        self._motion_timer = QTimer(self)
        self._motion_timer.setTimerType(Qt.PreciseTimer)
        self._motion_timer.setInterval(self._FRAME_MS)
        self._motion_timer.timeout.connect(self._motion_tick)

        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        window.destroyed.connect(self._detach)

    def sync_geometry(self) -> None:
        central = self.window.centralWidget()
        if central is None:
            return
        self.setGeometry(central.rect())
        self.lower()
        self.show()
        if self._cover.isNull():
            self._rebuild()
        else:
            self._rebuild_timer.start()

    def _rebuild(self) -> None:
        if self.width() <= 0 or self.height() <= 0:
            return

        cover_w = max(self.width() + 2, round(self.width() * self._OVERSCAN))
        cover_h = max(self.height() + 2, round(self.height() * self._OVERSCAN))
        scaled = self._source.scaled(
            QSize(cover_w, cover_h),
            Qt.KeepAspectRatioByExpanding,
            Qt.SmoothTransformation,
        )
        x = max(0, (scaled.width() - cover_w) // 2)
        y = max(0, (scaled.height() - cover_h) // 2)
        self._cover = scaled.copy(x, y, cover_w, cover_h)
        self._render = self._compose_vignette(self._cover)
        # Blur the same rendered field used by the wallpaper. This makes glass
        # tone/vignette and parallax sampling exactly match the visible scene.
        self._blurred = _blur_pixmap(self._render, 10.0)

        self._offset = QPointF(0.0, 0.0)
        self._target = QPointF(0.0, 0.0)
        self._pending_pointer = None
        self._source_rect = self._rect_for_offset(self._offset)
        self.update()
        self.scene_changed.emit()
        self.transform_changed.emit()

    def _compose_vignette(self, cover: QPixmap) -> QPixmap:
        composed = cover.copy()
        painter = QPainter(composed)
        center = QPointF(composed.width() / 2.0, composed.height() / 2.0)
        radius = max(1.0, math.hypot(composed.width() / 2.0, composed.height() / 2.0))
        outer = QRadialGradient(center, radius)
        outer.setColorAt(0.0, QColor(0, 0, 0, 0))
        outer.setColorAt(1.0, QColor(0, 0, 0, 92))
        painter.fillRect(composed.rect(), outer)
        painter.end()
        return composed

    def blurred_scene(self) -> QPixmap:
        return self._blurred if not self._blurred.isNull() else self._render

    def source_rect(self) -> QRect:
        return QRect(self._source_rect)

    def _rect_for_offset(self, offset: QPointF) -> QRect:
        if self._render.isNull() or self.width() <= 0 or self.height() <= 0:
            return QRect()
        max_x = max(0, self._render.width() - self.width())
        max_y = max(0, self._render.height() - self.height())
        center_x = max_x / 2.0
        center_y = max_y / 2.0
        sx = round(max(0.0, min(float(max_x), center_x + offset.x())))
        sy = round(max(0.0, min(float(max_y), center_y + offset.y())))
        return QRect(sx, sy, self.width(), self.height())

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        event_type = event.type()
        if event_type == QEvent.MouseMove and hasattr(event, "globalPosition"):
            # High-rate mouse events only update one point. Target calculation,
            # easing and repaint are capped by the single 60fps timer.
            self._pending_pointer = event.globalPosition().toPoint()
            self._ensure_motion_timer()
        elif watched is self.window and event_type == QEvent.Leave:
            self._pending_pointer = None
            self._target = QPointF(0.0, 0.0)
            self._ensure_motion_timer()
        return False

    def _ensure_motion_timer(self) -> None:
        if not self._motion_timer.isActive():
            self._last_tick = time.monotonic()
            self._motion_timer.start()

    def _update_target(self, global_pos: QPoint) -> None:
        local = self.mapFromGlobal(global_pos)
        rect = self.rect()
        if not rect.contains(local) or self._render.isNull():
            self._target = QPointF(0.0, 0.0)
            return

        half_w = max(1.0, rect.width() / 2.0)
        half_h = max(1.0, rect.height() / 2.0)
        nx = max(-1.0, min(1.0, (local.x() - half_w) / half_w))
        ny = max(-1.0, min(1.0, (local.y() - half_h) / half_h))
        travel_x = max(0.0, (self._render.width() - rect.width()) / 2.0 * self._TRAVEL)
        travel_y = max(0.0, (self._render.height() - rect.height()) / 2.0 * self._TRAVEL)
        self._target = QPointF(-nx * travel_x, -ny * travel_y)

    def _motion_tick(self) -> None:
        now = time.monotonic()
        dt = min(0.050, max(0.001, now - self._last_tick))
        self._last_tick = now

        if self._pending_pointer is not None:
            point = self._pending_pointer
            self._pending_pointer = None
            self._update_target(point)

        # Time-based exponential easing remains visually stable if one frame is
        # late; it does not perform a large catch-up jump on the next frame.
        alpha = 1.0 - math.exp(-dt / self._EASE_TAU)
        dx = self._target.x() - self._offset.x()
        dy = self._target.y() - self._offset.y()
        self._offset = QPointF(
            self._offset.x() + dx * alpha,
            self._offset.y() + dy * alpha,
        )

        next_rect = self._rect_for_offset(self._offset)
        if next_rect != self._source_rect:
            self._source_rect = next_rect
            self.update()
            self.transform_changed.emit()

        remaining = math.hypot(self._target.x() - self._offset.x(), self._target.y() - self._offset.y())
        if self._pending_pointer is None and remaining <= self._SETTLE_PX:
            self._offset = QPointF(self._target)
            final_rect = self._rect_for_offset(self._offset)
            if final_rect != self._source_rect:
                self._source_rect = final_rect
                self.update()
                self.transform_changed.emit()
            self._motion_timer.stop()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        if self._render.isNull() or self._source_rect.isEmpty():
            return
        painter = QPainter(self)
        src = self._source_rect
        region = event.region() if event is not None else QRegion(self.rect())
        for target in region:
            source = QRect(
                src.x() + target.x(),
                src.y() + target.y(),
                target.width(),
                target.height(),
            )
            painter.drawPixmap(target, self._render, source)
        painter.end()

    def _detach(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        self._motion_timer.stop()


class GlassBackdrop:
    """Tiny interaction handle; the shared GlassLayer owns all actual painting."""

    def __init__(self, frame: QFrame, layer: "GlassLayer") -> None:
        self.frame = frame
        self.layer = layer
        self._surface_scale = 1.0
        self._overlay_alpha = 64.0

    @property
    def surface_scale(self) -> float:
        return self._surface_scale

    @property
    def overlay_alpha(self) -> float:
        return self._overlay_alpha

    def set_interaction(self, *, scale: float, overlay_alpha: float) -> None:
        scale = max(0.94, min(1.0, float(scale)))
        overlay_alpha = max(0.0, min(255.0, float(overlay_alpha)))
        if abs(scale - self._surface_scale) < 0.0001 and abs(overlay_alpha - self._overlay_alpha) < 0.1:
            return
        self._surface_scale = scale
        self._overlay_alpha = overlay_alpha
        self.layer.update_frame(self.frame)


class GlassLayer(QWidget):
    """One shared live backdrop-filter plane for every glass card.

    No card owns a copied background pixmap. Each paint samples the pre-blurred
    overscan using BackgroundLayer.source_rect(), so parallax and glass always
    use the identical transform. Background movement updates only the union of
    card rectangles, not a stack of per-card widgets/caches.
    """

    def __init__(self, window: QMainWindow, background: BackgroundLayer) -> None:
        central = window.centralWidget()
        super().__init__(central)
        self.window = window
        self.background = background
        self.surfaces: dict[QFrame, GlassBackdrop] = {}
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setFocusPolicy(Qt.NoFocus)

        background.scene_changed.connect(self.update_all)
        background.transform_changed.connect(self.update_transform)
        self.sync_geometry()

    def add_frame(self, frame: QFrame) -> GlassBackdrop:
        surface = GlassBackdrop(frame, self)
        self.surfaces[frame] = surface
        return surface

    def sync_geometry(self) -> None:
        central = self.window.centralWidget()
        if central is None:
            return
        self.setGeometry(central.rect())
        self.show()
        # Exact z-order: wallpaper -> shared glass -> normal UI/effects.
        self.lower()
        self.background.lower()
        self.update()

    def _visible_frame_rect(self, frame: QFrame) -> QRect:
        if not frame.isVisible() or frame.width() <= 0 or frame.height() <= 0:
            return QRect()

        global_rect = QRect(frame.mapToGlobal(QPoint(0, 0)), frame.size())
        ancestor = frame.parentWidget()
        central = self.window.centralWidget()
        while ancestor is not None and ancestor is not central:
            if not ancestor.isVisible():
                return QRect()
            ancestor_rect = QRect(ancestor.mapToGlobal(QPoint(0, 0)), ancestor.size())
            global_rect = global_rect.intersected(ancestor_rect)
            if global_rect.isEmpty():
                return QRect()
            ancestor = ancestor.parentWidget()

        local_top_left = self.mapFromGlobal(global_rect.topLeft())
        return QRect(local_top_left, global_rect.size()).intersected(self.rect())

    def _glass_region(self) -> QRegion:
        region = QRegion()
        for frame in self.surfaces:
            rect = self._visible_frame_rect(frame)
            if not rect.isEmpty():
                region = region.united(QRegion(rect.adjusted(-1, -1, 1, 1)))
        return region

    def update_transform(self) -> None:
        region = self._glass_region()
        if not region.isEmpty():
            self.update(region)

    def update_all(self) -> None:
        self.update()

    def update_frame(self, frame: QFrame) -> None:
        rect = self._visible_frame_rect(frame)
        if rect.isEmpty():
            self.update()
        else:
            self.update(rect.adjusted(-2, -2, 2, 2))

    def paintEvent(self, event) -> None:  # type: ignore[override]
        scene = self.background.blurred_scene()
        src = self.background.source_rect()
        if scene.isNull() or src.isEmpty():
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        event_region = event.region() if event is not None else QRegion(self.rect())

        for frame, surface in self.surfaces.items():
            rect = self._visible_frame_rect(frame)
            if rect.isEmpty() or not event_region.intersects(rect):
                continue

            sample = QRect(
                src.x() + rect.x(),
                src.y() + rect.y(),
                rect.width(),
                rect.height(),
            ).intersected(scene.rect())
            if sample.isEmpty():
                continue

            target = QRectF(rect)
            if surface.surface_scale < 0.9999:
                inset_x = target.width() * (1.0 - surface.surface_scale) * 0.5
                inset_y = target.height() * (1.0 - surface.surface_scale) * 0.5
                target.adjust(inset_x, inset_y, -inset_x, -inset_y)

            path = QPainterPath()
            path.addRoundedRect(target, 6.0, 6.0)
            painter.save()
            painter.setClipPath(path)
            painter.drawPixmap(target, scene, QRectF(sample))
            painter.fillPath(path, QColor(0, 0, 0, int(round(surface.overlay_alpha))))
            painter.restore()

        painter.end()


class VisualStyleController(QObject):
    """Own one wallpaper transform, one shared glass plane and the dot cursor."""

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self.window = window
        self.background = BackgroundLayer(window)
        self.glass_layer = GlassLayer(window, self.background)
        self._glass: dict[QFrame, GlassBackdrop] = {}
        self._cursor_installed = False

        window.setStyleSheet(window.styleSheet() + "\n" + NEKRO_STYLE)
        for frame in window.findChildren(QFrame):
            if frame.objectName() in _GLASS_NAMES:
                frame.setAttribute(Qt.WA_StyledBackground, True)
                self._glass[frame] = self.glass_layer.add_frame(frame)

        self._install_cursor()

        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

        # Side-panel scrolling changes card screen coordinates without resizing
        # the cards. One shared repaint keeps glass aligned while scrolling.
        for bar in window.findChildren(QScrollBar):
            bar.valueChanged.connect(self.glass_layer.update_all)

        window.destroyed.connect(self._cleanup)
        QTimer.singleShot(0, self._sync_all)

    def surface_for(self, frame: QFrame) -> GlassBackdrop | None:
        return self._glass.get(frame)

    def _install_cursor(self) -> None:
        pixmap = QPixmap(10, 10)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(Qt.white)
        painter.drawEllipse(QRectF(1.0, 1.0, 8.0, 8.0))
        painter.end()
        QApplication.setOverrideCursor(QCursor(pixmap, 4, 4))
        self._cursor_installed = True

    def _sync_all(self) -> None:
        self.background.sync_geometry()
        self.glass_layer.sync_geometry()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        event_type = event.type()
        if watched is self.window and event_type in (QEvent.Resize, QEvent.Show):
            QTimer.singleShot(0, self._sync_all)

        if isinstance(watched, QFrame) and watched in self._glass:
            if event_type in (QEvent.Resize, QEvent.Move, QEvent.Show, QEvent.Hide):
                self.glass_layer.update_all()
        return False

    def _cleanup(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        if self._cursor_installed:
            QApplication.restoreOverrideCursor()
            self._cursor_installed = False


def install_visual_style(window: QMainWindow) -> VisualStyleController:
    controller = VisualStyleController(window)
    window._visual_style = controller  # type: ignore[attr-defined]
    return controller
