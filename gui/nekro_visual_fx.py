from __future__ import annotations

import math
import random
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPixmap,
    QRadialGradient,
)
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsBlurEffect,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QMainWindow,
    QWidget,
)


# Presentation-only reuse of the MIT-licensed imsyy/home visual system.
# Upstream: https://github.com/imsyy/home
# Copyright (c) 2022 imsyy — MIT License.
#
# The user's captured nekro.top production bundle identifies imsyy/home v4.1.4
# as its upstream implementation and uses the same core visual rules:
#   .cards { background:#00000040; backdrop-filter:blur(10px); radius:6px }
#   hover scale(1.01), active scale(.98)
#   #cursor 18px white / opacity .25, active .5 + scale(.5)
#   scrollbar 6px / #eee
#   random background1.jpg ... background10.jpg
#
# Layout and data widgets remain ecommerce-agent-specific.  This module only
# supplies the original visual language around them.

_GLASS_NAMES = {"glassCard", "heroCard", "statusCard", "microCard"}
_UPSTREAM_BACKGROUND_COUNT = 10
_UPSTREAM_BACKGROUND_TEMPLATE = (
    "https://raw.githubusercontent.com/imsyy/home/dev/public/images/background{}.jpg"
)


# Keep the Qt widgets visually inside the same design system instead of
# inventing a separate pink/purple application theme.  QFrame card surfaces are
# transparent here because GlassBackdrop paints the actual blurred background
# plus the original #00000040 overlay underneath each card's child widgets.
NEKRO_STYLE_OVERRIDES = r"""
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

/* Source site: 6px transparent-track scrollbar with #eee thumb. */
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


def _blur_pixmap(source: QPixmap, radius: float = 10.0) -> QPixmap:
    """Create a cached Qt equivalent of CSS backdrop-filter: blur(10px)."""

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


class NekroBackground(QWidget):
    """Reuse the original imsyy/home random-wallpaper presentation model.

    The upstream homepage selects one of background1.jpg..background10.jpg.
    We request the same MIT-licensed upstream assets asynchronously so the GUI
    never blocks while a wallpaper is loading.  If networking is unavailable,
    the original site's #333 fallback remains visible.
    """

    scene_changed = Signal()

    def __init__(self, window: QMainWindow) -> None:
        central = window.centralWidget()
        super().__init__(central)
        self.window = window
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setFocusPolicy(Qt.NoFocus)

        self._source = QPixmap()
        self._cover = QPixmap()
        self._blurred = QPixmap()
        self._network = QNetworkAccessManager(self)
        self._rebuild_timer = QTimer(self)
        self._rebuild_timer.setSingleShot(True)
        self._rebuild_timer.setInterval(120)
        self._rebuild_timer.timeout.connect(self._rebuild)

        background_number = random.randint(1, _UPSTREAM_BACKGROUND_COUNT)
        request = QNetworkRequest(
            QUrl(_UPSTREAM_BACKGROUND_TEMPLATE.format(background_number))
        )
        reply = self._network.get(request)
        reply.finished.connect(lambda reply=reply: self._wallpaper_finished(reply))

    def sync_geometry(self) -> None:
        central = self.window.centralWidget()
        if central is None:
            return
        self.setGeometry(central.rect())
        self.lower()
        self.show()
        self._schedule_rebuild()

    def _wallpaper_finished(self, reply) -> None:
        try:
            if reply.error():
                return
            pixmap = QPixmap()
            if not pixmap.loadFromData(bytes(reply.readAll())):
                return
            self._source = pixmap
            self._rebuild()
        finally:
            reply.deleteLater()

    def _schedule_rebuild(self) -> None:
        if not self._source.isNull():
            self._rebuild_timer.start()
        else:
            self.update()

    def _rebuild(self) -> None:
        if self._source.isNull() or self.width() <= 0 or self.height() <= 0:
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

        # Same fallback as upstream html/body.
        painter.fillRect(rect, QColor("#333333"))
        if not self._cover.isNull():
            painter.drawPixmap(rect, self._cover)

        # Qt has no literal CSS radial-gradient parser; these two centered
        # gradients carry the exact source endpoints: transparent -> .5 black,
        # then transparent -> .3 black.  They reproduce the source vignette
        # without introducing a new palette.
        center = QPointF(rect.center())
        radius = math.hypot(rect.width() / 2.0, rect.height() / 2.0)

        outer = QRadialGradient(center, max(1.0, radius))
        outer.setColorAt(0.0, QColor(0, 0, 0, 0))
        outer.setColorAt(1.0, QColor(0, 0, 0, 128))
        painter.fillRect(rect, outer)

        inner = QRadialGradient(center, max(1.0, radius * 1.66))
        inner.setColorAt(0.33, QColor(0, 0, 0, 0))
        inner.setColorAt(1.0, QColor(0, 0, 0, 76))
        painter.fillRect(rect, inner)
        painter.end()


class GlassBackdrop(QWidget):
    """Paint the original 10px backdrop blur + #00000040 card surface."""

    def __init__(self, frame: QFrame, background: NekroBackground) -> None:
        super().__init__(frame)
        self.frame = frame
        self.background = background
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.background.scene_changed.connect(self.update)
        self.sync_geometry()

    def sync_geometry(self) -> None:
        self.setGeometry(self.frame.rect())
        self.lower()
        self.show()
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 6.0, 6.0)
        painter.setClipPath(path)

        scene = self.background.blurred_scene()
        if not scene.isNull():
            top_left = self.frame.mapTo(self.background, QPoint(0, 0))
            source_rect = QRectF(
                float(top_left.x()),
                float(top_left.y()),
                float(self.width()),
                float(self.height()),
            )
            painter.drawPixmap(QRectF(self.rect()), scene, source_rect)

        # Exact source card surface: #00000040 (25% black).
        painter.fillRect(self.rect(), QColor(0, 0, 0, 64))
        painter.end()


@dataclass(slots=True)
class _Petal:
    x: float
    y: float
    size: float
    rotation: float
    dx: float
    dy: float
    dr: float
    alpha: int


class NekroOverlay(QWidget):
    """Production-faithful cursor follower plus low-cost sakura overlay."""

    PETAL_COUNT = 50

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self.window = window
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.NoFocus)

        self.target: QPointF | None = None
        self.current: QPointF | None = None
        self.cursor_visible = False
        self.cursor_pressed = False
        self.petals: list[_Petal] = []
        self._seed_petals()

        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

    def sync_geometry(self) -> None:
        central = self.window.centralWidget()
        if central is None:
            return
        self.setGeometry(central.geometry())
        self.raise_()
        self.show()

    def set_target(self, point: QPointF) -> None:
        self.target = QPointF(point)
        if self.current is None:
            self.current = QPointF(point)
        self.cursor_visible = True

    def hide_cursor(self) -> None:
        self.cursor_visible = False

    def set_pressed(self, pressed: bool) -> None:
        self.cursor_pressed = pressed

    def _seed_petals(self) -> None:
        width = max(1180, self.window.width())
        height = max(760, self.window.height())
        self.petals = [
            self._new_petal(width, height, initial=True)
            for _ in range(self.PETAL_COUNT)
        ]

    def _new_petal(self, width: int, height: int, *, initial: bool = False) -> _Petal:
        # Captured production bundle: 50 particles drifting left/down while
        # rotating.  Keep its motion parameters, draw our own vector petal.
        size = max(4.0, 40.0 * random.random())
        if initial:
            x = random.uniform(0.0, float(width))
            y = random.uniform(0.0, float(height))
        elif random.random() > 0.4:
            x = random.uniform(0.0, float(width))
            y = -size
        else:
            x = float(width) + size
            y = random.uniform(0.0, float(height))
        return _Petal(
            x=x,
            y=y,
            size=size,
            rotation=random.uniform(0.0, 2.0 * math.pi),
            dx=random.uniform(-1.95, -1.45),
            dy=random.uniform(1.5, 2.2),
            dr=random.uniform(0.0, 0.03),
            alpha=random.randint(128, 220),
        )

    def _tick(self) -> None:
        if self.target is not None:
            if self.current is None:
                self.current = QPointF(self.target)
            else:
                # Exact source interpolation factor: 0.35.
                self.current = QPointF(
                    self.current.x()
                    + (self.target.x() - self.current.x()) * 0.35,
                    self.current.y()
                    + (self.target.y() - self.current.y()) * 0.35,
                )

        width = max(1, self.width())
        height = max(1, self.height())
        for index, petal in enumerate(self.petals):
            petal.x += petal.dx
            petal.y += petal.dy
            petal.rotation += petal.dr
            margin = max(8.0, petal.size)
            if petal.x < -margin or petal.y > height + margin:
                self.petals[index] = self._new_petal(width, height, initial=False)

        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)

        # nekro.top's canvas_sakura is fixed above the page and ignores input.
        for petal in self.petals:
            painter.save()
            painter.translate(petal.x, petal.y)
            painter.rotate(math.degrees(petal.rotation))
            painter.setBrush(QColor(255, 222, 237, petal.alpha))
            painter.drawEllipse(
                QRectF(
                    -petal.size * 0.52,
                    -petal.size * 0.18,
                    petal.size * 1.04,
                    petal.size * 0.36,
                )
            )
            painter.restore()

        if self.cursor_visible and self.current is not None:
            # Source CSS: 18x18 white / opacity .25; active opacity .5 + .5 scale.
            radius = 4.5 if self.cursor_pressed else 9.0
            alpha = 128 if self.cursor_pressed else 64
            painter.setBrush(QColor(255, 255, 255, alpha))
            painter.drawEllipse(self.current, radius, radius)

        painter.end()


class NekroInteractionController(QObject):
    """Install source visual design without touching GUI/business semantics."""

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self.window = window
        self.background = NekroBackground(window)
        self.overlay = NekroOverlay(window)
        self._cursor_override_installed = False
        self._glass_backdrops: dict[QFrame, GlassBackdrop] = {}

        # The source skin is deliberately appended after the existing dev GUI
        # stylesheet, replacing its custom pink rounded-card design.
        window.setStyleSheet(window.styleSheet() + "\n" + NEKRO_STYLE_OVERRIDES)
        self._install_glass_backdrops()
        self._install_white_dot_cursor()

        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

        window.destroyed.connect(self._cleanup)
        QTimer.singleShot(0, self._sync_layers)

    def _install_glass_backdrops(self) -> None:
        for frame in self.window.findChildren(QFrame):
            if frame.objectName() not in _GLASS_NAMES:
                continue
            frame.setAttribute(Qt.WA_StyledBackground, True)
            backdrop = GlassBackdrop(frame, self.background)
            self._glass_backdrops[frame] = backdrop

    def _install_white_dot_cursor(self) -> None:
        # Source cursor is an inline 10px SVG white circle with a 4/4 hotspot.
        pixmap = QPixmap(10, 10)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(Qt.white)
        painter.drawEllipse(QRectF(1.0, 1.0, 8.0, 8.0))
        painter.end()
        QApplication.setOverrideCursor(QCursor(pixmap, 4, 4))
        self._cursor_override_installed = True

    def _sync_layers(self) -> None:
        self.background.sync_geometry()
        for backdrop in self._glass_backdrops.values():
            backdrop.sync_geometry()
        self.overlay.sync_geometry()

    def _point_in_central(self, global_point: QPointF) -> QPointF | None:
        central = self.window.centralWidget()
        if central is None or not self.window.isVisible():
            return None
        local = central.mapFromGlobal(global_point.toPoint())
        if not central.rect().contains(local):
            return None
        return QPointF(local)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        event_type = event.type()

        if event_type == QEvent.Resize and watched is self.window:
            QTimer.singleShot(0, self._sync_layers)

        if isinstance(watched, QFrame) and watched in self._glass_backdrops:
            if event_type in (QEvent.Resize, QEvent.Show):
                QTimer.singleShot(0, self._glass_backdrops[watched].sync_geometry)

        if isinstance(event, QMouseEvent):
            local = self._point_in_central(event.globalPosition())
            if local is not None:
                if event_type == QEvent.MouseMove:
                    self.overlay.set_target(local)
                elif event_type == QEvent.MouseButtonPress:
                    self.overlay.set_target(local)
                    self.overlay.set_pressed(True)
                elif event_type == QEvent.MouseButtonRelease:
                    self.overlay.set_target(local)
                    self.overlay.set_pressed(False)
            elif event_type == QEvent.MouseMove:
                self.overlay.hide_cursor()

        if event_type == QEvent.Leave and watched is self.window:
            self.overlay.hide_cursor()
        elif event_type == QEvent.Enter and watched is self.window:
            self.overlay.cursor_visible = True

        return False

    def _cleanup(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        if self._cursor_override_installed:
            QApplication.restoreOverrideCursor()
            self._cursor_override_installed = False


def install_nekro_visual_fx(window: QMainWindow) -> NekroInteractionController:
    """Attach the source-reused imsyy/nekro presentation layer to the GUI."""

    controller = NekroInteractionController(window)
    window._nekro_visual_fx = controller  # type: ignore[attr-defined]
    return controller
