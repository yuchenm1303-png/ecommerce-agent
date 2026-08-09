from __future__ import annotations

import base64
import math
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QImage,
    QPainter,
    QPainterPath,
    QPixmap,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsBlurEffect,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QMainWindow,
    QWidget,
)


# Static presentation layer only.
# Visual rules mirror the MIT-licensed imsyy/home / nekro.top design language.
# The wallpaper is a fixed local asset bundled with ecommerce-agent.
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
    """Build the blurred companion once; motion only changes the sampled rect."""

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
    scene.render(
        painter,
        QRectF(result.rect()),
        QRectF(source.rect()),
        Qt.IgnoreAspectRatio,
    )
    painter.end()
    return result


class BackgroundLayer(QWidget):
    """Simple sub-pixel wallpaper parallax shared with every glass surface."""

    scene_changed = Signal()
    transform_changed = Signal()

    _OVERSCAN = 1.06
    _TRAVEL = 0.9
    _MAX_ZOOM = 0.0
    _EASE = 0.12
    _SETTLE_PX = 0.02

    def __init__(self, window: QMainWindow) -> None:
        central = window.centralWidget()
        super().__init__(central)
        self.window = window
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.NoFocus)

        self._source = _load_wallpaper()
        self._cover = QPixmap()
        self._render = QPixmap()
        self._blurred = QPixmap()

        self._rebuild_timer = QTimer(self)
        self._rebuild_timer.setSingleShot(True)
        self._rebuild_timer.setInterval(140)
        self._rebuild_timer.timeout.connect(self._rebuild)

        self._offset = QPointF(0.0, 0.0)
        self._target = QPointF(0.0, 0.0)
        self._zoom = 1.0
        self._zoom_target = 1.0
        self._parallax_timer = QTimer(self)
        self._parallax_timer.setTimerType(Qt.PreciseTimer)
        self._parallax_timer.setInterval(16)
        self._parallax_timer.timeout.connect(self._parallax_tick)

        self.window.setMouseTracking(True)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        self.window.destroyed.connect(self._detach_parallax)

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

        cover_w = max(1, round(self.width() * self._OVERSCAN))
        cover_h = max(1, round(self.height() * self._OVERSCAN))
        scaled = self._source.scaled(
            QSize(cover_w, cover_h),
            Qt.KeepAspectRatioByExpanding,
            Qt.SmoothTransformation,
        )
        x = max(0, (scaled.width() - cover_w) // 2)
        y = max(0, (scaled.height() - cover_h) // 2)
        self._cover = scaled.copy(x, y, cover_w, cover_h)
        self._render = self._compose_vignette(self._cover)
        self._blurred = _blur_pixmap(self._cover, 10.0)

        self._offset = QPointF(0.0, 0.0)
        self._target = QPointF(0.0, 0.0)
        self._zoom = 1.0
        self._zoom_target = 1.0
        self.update()
        self.scene_changed.emit()
        self.transform_changed.emit()

    def blurred_scene(self) -> QPixmap:
        return self._blurred if not self._blurred.isNull() else self._cover

    def parallax_source_rect(self) -> QRectF:
        """Return the current fractional source rect for true sub-pixel motion."""

        if self._cover.isNull() or self.width() <= 0 or self.height() <= 0:
            return QRectF()

        width = float(self.width())
        height = float(self.height())
        zoom = max(1.0, float(self._zoom))
        source_w = width / zoom
        source_h = height / zoom
        center_x = self._cover.width() / 2.0 + self._offset.x()
        center_y = self._cover.height() / 2.0 + self._offset.y()
        source_x = max(0.0, min(self._cover.width() - source_w, center_x - source_w / 2.0))
        source_y = max(0.0, min(self._cover.height() - source_h, center_y - source_h / 2.0))
        return QRectF(source_x, source_y, source_w, source_h)

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

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        del watched
        if event.type() == QEvent.MouseMove and hasattr(event, "globalPosition"):
            self._update_target(event.globalPosition().toPoint())
            if not self._parallax_timer.isActive():
                self._parallax_timer.start()
        return False

    def _update_target(self, global_pos: QPoint) -> None:
        local = self.mapFromGlobal(global_pos)
        rect = self.rect()
        if not rect.contains(local) or self._cover.isNull():
            self._target = QPointF(0.0, 0.0)
            self._zoom_target = 1.0
            return

        half_w = max(1.0, rect.width() / 2.0)
        half_h = max(1.0, rect.height() / 2.0)
        nx = max(-1.0, min(1.0, (local.x() - half_w) / half_w))
        ny = max(-1.0, min(1.0, (local.y() - half_h) / half_h))
        travel_x = (self._cover.width() - rect.width()) / 2.0 * self._TRAVEL
        travel_y = (self._cover.height() - rect.height()) / 2.0 * self._TRAVEL
        self._target = QPointF(-nx * travel_x, -ny * travel_y)
        self._zoom_target = 1.0 + min(1.0, math.hypot(nx, ny)) * self._MAX_ZOOM

    def _parallax_tick(self) -> None:
        next_x = self._offset.x() + (self._target.x() - self._offset.x()) * self._EASE
        next_y = self._offset.y() + (self._target.y() - self._offset.y()) * self._EASE
        next_zoom = self._zoom + (self._zoom_target - self._zoom) * self._EASE

        changed = (
            abs(next_x - self._offset.x()) > 0.0001
            or abs(next_y - self._offset.y()) > 0.0001
            or abs(next_zoom - self._zoom) > 0.00001
        )
        self._offset = QPointF(next_x, next_y)
        self._zoom = next_zoom

        if changed:
            self.update()
            self.transform_changed.emit()

        remaining = math.hypot(
            self._target.x() - self._offset.x(),
            self._target.y() - self._offset.y(),
        )
        if remaining <= self._SETTLE_PX and abs(self._zoom_target - self._zoom) <= 0.0001:
            final_changed = (
                abs(self._target.x() - self._offset.x()) > 0.0001
                or abs(self._target.y() - self._offset.y()) > 0.0001
                or abs(self._zoom_target - self._zoom) > 0.00001
            )
            self._offset = QPointF(self._target)
            self._zoom = self._zoom_target
            if final_changed:
                self.update()
                self.transform_changed.emit()
            self._parallax_timer.stop()

    def _detach_parallax(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        self._parallax_timer.stop()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        del event
        if self._render.isNull():
            return

        source_rect = self.parallax_source_rect()
        if source_rect.isEmpty():
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.drawPixmap(QRectF(self.rect()), self._render, source_rect)
        painter.end()


class GlassBackdrop(QWidget):
    """Live glass surface sampling the current blurred wallpaper transform."""

    def __init__(self, frame: QFrame, background: BackgroundLayer) -> None:
        super().__init__(frame)
        self.frame = frame
        self.background = background
        self._surface_scale = 1.0
        self._overlay_alpha = 64.0

        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.background.scene_changed.connect(self.update)
        self.background.transform_changed.connect(self.update)
        self.sync_geometry()

    @property
    def surface_scale(self) -> float:
        return self._surface_scale

    @property
    def overlay_alpha(self) -> float:
        return self._overlay_alpha

    def set_interaction(self, *, scale: float, overlay_alpha: float) -> None:
        scale = max(0.94, min(1.0, float(scale)))
        overlay_alpha = max(0.0, min(255.0, float(overlay_alpha)))
        if (
            abs(scale - self._surface_scale) < 0.0001
            and abs(overlay_alpha - self._overlay_alpha) < 0.1
        ):
            return
        self._surface_scale = scale
        self._overlay_alpha = overlay_alpha
        self.update()

    def sync_geometry(self) -> None:
        self.setGeometry(self.frame.rect())
        self.lower()
        self.show()
        self.update()

    def _live_source_rect(self, scene: QPixmap) -> QRectF:
        if scene.isNull() or self.width() <= 0 or self.height() <= 0:
            return QRectF()

        global_top_left = self.frame.mapToGlobal(QPoint(0, 0))
        top_left = self.background.mapFromGlobal(global_top_left)
        background_src = self.background.parallax_source_rect()
        if background_src.isEmpty():
            return QRectF()

        scale_x = background_src.width() / max(1.0, float(self.background.width()))
        scale_y = background_src.height() / max(1.0, float(self.background.height()))
        sample_w = self.width() * scale_x
        sample_h = self.height() * scale_y
        sample_x = background_src.x() + top_left.x() * scale_x
        sample_y = background_src.y() + top_left.y() * scale_y
        sample_x = max(0.0, min(scene.width() - sample_w, sample_x))
        sample_y = max(0.0, min(scene.height() - sample_h, sample_y))
        return QRectF(sample_x, sample_y, sample_w, sample_h)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        target_rect = QRectF(self.rect())
        if self._surface_scale < 0.9999:
            inset_x = target_rect.width() * (1.0 - self._surface_scale) * 0.5
            inset_y = target_rect.height() * (1.0 - self._surface_scale) * 0.5
            target_rect.adjust(inset_x, inset_y, -inset_x, -inset_y)

        path = QPainterPath()
        path.addRoundedRect(target_rect, 6.0, 6.0)
        painter.setClipPath(path)

        scene = self.background.blurred_scene()
        source_rect = self._live_source_rect(scene)
        if not scene.isNull() and not source_rect.isEmpty():
            painter.drawPixmap(target_rect, scene, source_rect)

        painter.fillRect(target_rect, QColor(0, 0, 0, int(round(self._overlay_alpha))))
        painter.end()


class VisualStyleController(QObject):
    """Own background, glass surfaces and the static white-dot cursor."""

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self.window = window
        self.background = BackgroundLayer(window)
        self._glass: dict[QFrame, GlassBackdrop] = {}
        self._cursor_installed = False

        window.setStyleSheet(window.styleSheet() + "\n" + NEKRO_STYLE)
        for frame in window.findChildren(QFrame):
            if frame.objectName() in _GLASS_NAMES:
                frame.setAttribute(Qt.WA_StyledBackground, True)
                self._glass[frame] = GlassBackdrop(frame, self.background)

        self._install_cursor()
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
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
        for backdrop in self._glass.values():
            backdrop.sync_geometry()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        event_type = event.type()
        if watched is self.window and event_type in (QEvent.Resize, QEvent.Show):
            QTimer.singleShot(0, self.background.sync_geometry)

        if isinstance(watched, QFrame) and watched in self._glass:
            if event_type in (QEvent.Resize, QEvent.Show):
                QTimer.singleShot(0, self._glass[watched].sync_geometry)
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
