from __future__ import annotations

import math
import random
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QMouseEvent, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMainWindow, QWidget


# Visual rules adapted from the MIT-licensed imsyy/home project and verified
# against the user's captured nekro.top production CSS/JS bundle.
# Upstream: https://github.com/imsyy/home
# Copyright (c) 2022 imsyy — MIT License.

NEKRO_STYLE_OVERRIDES = r"""
QWidget#root {
    color: #ffffff;
    background: transparent;
}

/* nekro/imsyy core card rule: #00000040 + 6px radius.
   Qt stylesheets do not implement CSS backdrop-filter, so the translucent
   surface sits over the already-soft atmospheric background instead. */
QFrame#glassCard,
QFrame#heroCard,
QFrame#statusCard,
QFrame#microCard {
    background-color: rgba(0, 0, 0, 64);
    border: 0;
    border-radius: 6px;
}

QLabel#phaseBadge {
    padding: 8px 13px;
    border-radius: 6px;
    background-color: rgba(0, 0, 0, 64);
    border: 0;
    color: #efefef;
}

QLineEdit,
QSpinBox {
    min-height: 38px;
    padding: 0 11px;
    color: #ffffff;
    background-color: rgba(0, 0, 0, 48);
    border: 1px solid rgba(255, 255, 255, 24);
    border-radius: 6px;
    selection-background-color: rgba(255, 255, 255, 62);
}

QLineEdit:hover,
QSpinBox:hover {
    background-color: rgba(0, 0, 0, 58);
    border-color: rgba(255, 255, 255, 34);
}

QLineEdit:focus,
QSpinBox:focus {
    background-color: rgba(0, 0, 0, 64);
    border-color: rgba(255, 255, 255, 82);
}

QPushButton {
    min-height: 37px;
    padding: 0 16px;
    border-radius: 6px;
    border: 0;
    color: #ffffff;
    background-color: rgba(0, 0, 0, 48);
}

QPushButton:hover {
    background-color: rgba(0, 0, 0, 102);
}

QPushButton:pressed {
    background-color: rgba(0, 0, 0, 64);
}

QPushButton#primaryButton {
    min-width: 140px;
    font-weight: 700;
    color: #ffffff;
    background-color: rgba(255, 255, 255, 46);
    border: 0;
}

QPushButton#primaryButton:hover {
    background-color: rgba(255, 255, 255, 70);
}

QPushButton#dangerButton {
    background-color: rgba(92, 22, 39, 92);
}

QPushButton#dangerButton:hover {
    background-color: rgba(112, 29, 49, 126);
}

QPushButton#quietButton {
    background-color: rgba(0, 0, 0, 48);
}

QPushButton:disabled {
    color: rgba(255, 255, 255, 82);
    background-color: rgba(0, 0, 0, 24);
}

QCheckBox {
    color: rgba(255, 255, 255, 220);
}

QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1px solid rgba(255, 255, 255, 72);
    background-color: rgba(0, 0, 0, 48);
}

QCheckBox::indicator:checked {
    background-color: rgba(255, 255, 255, 120);
    border-color: rgba(255, 255, 255, 190);
}

QTableWidget {
    color: #ffffff;
    background-color: rgba(0, 0, 0, 36);
    alternate-background-color: rgba(255, 255, 255, 10);
    border: 0;
    border-radius: 6px;
    gridline-color: rgba(255, 255, 255, 14);
    selection-background-color: rgba(255, 255, 255, 42);
    selection-color: #ffffff;
}

QTableWidget::item {
    padding: 7px 8px;
    border-bottom: 1px solid rgba(255, 255, 255, 12);
}

QHeaderView::section {
    padding: 8px 8px;
    color: rgba(255, 255, 255, 220);
    background-color: rgba(255, 255, 255, 24);
    border: 0;
    border-bottom: 1px solid rgba(255, 255, 255, 20);
    font-weight: 650;
}

QPlainTextEdit {
    color: #efefef;
    background-color: rgba(0, 0, 0, 64);
    border: 0;
    border-radius: 6px;
    padding: 9px;
    selection-background-color: rgba(255, 255, 255, 54);
    font-family: "Cascadia Mono", "Consolas", monospace;
    font-size: 12px;
}

/* Source site uses a thin 6px transparent-track scrollbar. */
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
"""


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
    """Production-faithful cursor follower plus low-cost sakura overlay.

    Cursor behavior mirrors imsyy/home: a tiny white system cursor, an 18px
    translucent follower, 0.35 interpolation, and a half-scale pressed state.
    Sakura motion mirrors the captured nekro.top bundle's 50-particle canvas
    direction/speed while drawing a local vector petal instead of copying its
    embedded bitmap asset.
    """

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
        self.petals = [self._new_petal(width, height, initial=True) for _ in range(self.PETAL_COUNT)]

    def _new_petal(self, width: int, height: int, *, initial: bool = False) -> _Petal:
        # Captured bundle: s=random(), x/y random viewport, x drifts roughly
        # -1.95..-1.45 px/frame, y +1.5..+2.2 px/frame, r += 0..0.03 rad.
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
                    self.current.x() + (self.target.x() - self.current.x()) * 0.35,
                    self.current.y() + (self.target.y() - self.current.y()) * 0.35,
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

        # Nekro's sakura canvas is above the wallpaper/content and ignores input.
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
            # Source CSS: 18x18 white, opacity .25; active => opacity .5,
            # transform scale(.5).
            radius = 4.5 if self.cursor_pressed else 9.0
            alpha = 128 if self.cursor_pressed else 64
            painter.setBrush(QColor(255, 255, 255, alpha))
            painter.drawEllipse(self.current, radius, radius)

        painter.end()


class NekroInteractionController(QObject):
    """Presentation-only controller; does not touch test/runtime semantics."""

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self.window = window
        self.overlay = NekroOverlay(window)
        self._cursor_override_installed = False

        # Apply production-derived surface values after the base GUI stylesheet.
        window.setStyleSheet(window.styleSheet() + "\n" + NEKRO_STYLE_OVERRIDES)
        self._install_white_dot_cursor()

        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

        window.destroyed.connect(self._cleanup)
        QTimer.singleShot(0, self.overlay.sync_geometry)

    def _install_white_dot_cursor(self) -> None:
        # Production cursor is an inline 10px SVG white circle with 4/4 hotspot.
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
            QTimer.singleShot(0, self.overlay.sync_geometry)

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
    """Attach source-grounded nekro/imsyy presentation effects to the GUI."""

    controller = NekroInteractionController(window)
    window._nekro_visual_fx = controller  # type: ignore[attr-defined]
    return controller
