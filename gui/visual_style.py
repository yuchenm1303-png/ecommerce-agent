from __future__ import annotations

import math

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QLinearGradient,
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
# Visual rules mirror the MIT-licensed imsyy/home / nekro.top design language,
# while the wallpaper itself is local to ecommerce-agent.
_GLASS_NAMES = {"glassCard", "heroCard", "statusCard", "microCard"}


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


def _radial_fill(
    painter: QPainter,
    canvas: QPixmap,
    x: float,
    y: float,
    radius: float,
    center: QColor,
) -> None:
    gradient = QRadialGradient(
        QPointF(canvas.width() * x, canvas.height() * y),
        max(canvas.width(), canvas.height()) * radius,
    )
    gradient.setColorAt(0.0, center)
    fade = QColor(center)
    fade.setAlpha(0)
    gradient.setColorAt(1.0, fade)
    painter.fillRect(canvas.rect(), gradient)


def build_wallpaper(width: int = 1920, height: int = 1080) -> QPixmap:
    """Build the local mist-blue / lilac / pink scene once."""

    width = max(1280, int(width))
    height = max(720, int(height))
    canvas = QPixmap(width, height)
    canvas.fill(QColor("#9FB5E7"))

    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.Antialiasing, True)

    base = QLinearGradient(0, 0, width, height)
    base.setColorAt(0.00, QColor("#7798CF"))
    base.setColorAt(0.28, QColor("#9A9FD1"))
    base.setColorAt(0.56, QColor("#C39FC7"))
    base.setColorAt(0.80, QColor("#E2ABC0"))
    base.setColorAt(1.00, QColor("#E8B9B0"))
    painter.fillRect(canvas.rect(), base)

    _radial_fill(painter, canvas, 0.14, 0.16, 0.43, QColor(184, 218, 255, 170))
    _radial_fill(painter, canvas, 0.76, 0.12, 0.34, QColor(229, 205, 255, 128))
    _radial_fill(painter, canvas, 0.90, 0.68, 0.38, QColor(255, 190, 206, 128))
    _radial_fill(painter, canvas, 0.18, 0.88, 0.34, QColor(177, 232, 225, 72))
    _radial_fill(painter, canvas, 0.50, 0.48, 0.42, QColor(244, 224, 239, 58))

    veil = QLinearGradient(width * 0.22, 0, width * 0.78, 0)
    veil.setColorAt(0.0, QColor(255, 255, 255, 0))
    veil.setColorAt(0.45, QColor(245, 238, 250, 28))
    veil.setColorAt(0.55, QColor(245, 238, 250, 28))
    veil.setColorAt(1.0, QColor(255, 255, 255, 0))
    painter.fillRect(canvas.rect(), veil)

    horizon = QLinearGradient(0, height * 0.52, 0, height)
    horizon.setColorAt(0.0, QColor(255, 255, 255, 0))
    horizon.setColorAt(0.72, QColor(255, 224, 232, 18))
    horizon.setColorAt(1.0, QColor(243, 207, 218, 38))
    painter.fillRect(canvas.rect(), horizon)
    painter.end()
    return canvas


def _blur_pixmap(source: QPixmap, radius: float = 10.0) -> QPixmap:
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
    scene_changed = Signal()

    def __init__(self, window: QMainWindow) -> None:
        central = window.centralWidget()
        super().__init__(central)
        self.window = window
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.NoFocus)

        self._source = build_wallpaper()
        self._cover = QPixmap()
        self._blurred = QPixmap()
        self._rebuild_timer = QTimer(self)
        self._rebuild_timer.setSingleShot(True)
        self._rebuild_timer.setInterval(140)
        self._rebuild_timer.timeout.connect(self._rebuild)

    def sync_geometry(self) -> None:
        central = self.window.centralWidget()
        if central is None:
            return
        self.setGeometry(central.rect())
        self.lower()
        self.show()
        self._rebuild_timer.start()

    def _rebuild(self) -> None:
        if self.width() <= 0 or self.height() <= 0:
            return
        scaled = self._source.scaled(
            self.size(),
            Qt.KeepAspectRatioByExpanding,
            Qt.SmoothTransformation,
        )
        x = max(0, (scaled.width() - self.width()) // 2)
        y = max(0, (scaled.height() - self.height()) // 2)
        self._cover = scaled.copy(x, y, self.width(), self.height())
        self._blurred = _blur_pixmap(self._cover, 10.0)
        self.update()
        self.scene_changed.emit()

    def blurred_scene(self) -> QPixmap:
        return self._blurred if not self._blurred.isNull() else self._cover

    def paintEvent(self, event) -> None:  # type: ignore[override]
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        rect = self.rect()
        painter.fillRect(rect, QColor("#8FA4D0"))
        if not self._cover.isNull():
            painter.drawPixmap(rect, self._cover)

        center = QPointF(rect.center())
        radius = max(1.0, math.hypot(rect.width() / 2.0, rect.height() / 2.0))
        outer = QRadialGradient(center, radius)
        outer.setColorAt(0.0, QColor(0, 0, 0, 0))
        outer.setColorAt(1.0, QColor(0, 0, 0, 128))
        painter.fillRect(rect, outer)

        inner = QRadialGradient(center, radius * 1.66)
        inner.setColorAt(0.33, QColor(0, 0, 0, 0))
        inner.setColorAt(1.0, QColor(0, 0, 0, 76))
        painter.fillRect(rect, inner)
        painter.end()


class GlassBackdrop(QWidget):
    """Cached glass surface; interaction animation repaints this widget only."""

    def __init__(self, frame: QFrame, background: BackgroundLayer) -> None:
        super().__init__(frame)
        self.frame = frame
        self.background = background
        self._surface_cache = QPixmap()
        self._surface_scale = 1.0
        self._overlay_alpha = 64.0

        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.background.scene_changed.connect(self.refresh_cache)
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
        self.refresh_cache()

    def refresh_cache(self) -> None:
        scene = self.background.blurred_scene()
        if scene.isNull() or self.width() <= 0 or self.height() <= 0:
            self._surface_cache = QPixmap()
            self.update()
            return

        top_left = self.frame.mapTo(self.background, QPoint(0, 0))
        self._surface_cache = scene.copy(
            int(top_left.x()),
            int(top_left.y()),
            int(self.width()),
            int(self.height()),
        )
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        rect = QRectF(self.rect())
        if self._surface_scale < 0.9999:
            inset_x = rect.width() * (1.0 - self._surface_scale) * 0.5
            inset_y = rect.height() * (1.0 - self._surface_scale) * 0.5
            rect.adjust(inset_x, inset_y, -inset_x, -inset_y)

        path = QPainterPath()
        path.addRoundedRect(rect, 6.0, 6.0)
        painter.setClipPath(path)

        if not self._surface_cache.isNull():
            painter.drawPixmap(rect, self._surface_cache, QRectF(self._surface_cache.rect()))

        painter.fillRect(rect, QColor(0, 0, 0, int(round(self._overlay_alpha))))
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
