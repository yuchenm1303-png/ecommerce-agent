from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QFrame, QMainWindow, QWidget

from .native_background import NativeQuickBackground


_GLASS_NAMES = {"glassCard", "heroCard", "statusCard", "microCard"}

NEKRO_STYLE = r"""
QWidget#root {
    color: #ffffff;
    background: transparent;
    font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
    font-size: 13px;
}

QMainWindow,
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


class GlassBackdrop(QWidget):
    """Cheap per-card tint/border only; blur comes from the global Quick mask."""

    def __init__(self, frame: QFrame) -> None:
        super().__init__(frame)
        self.frame = frame
        self._surface_scale = 1.0
        self._overlay_alpha = 64.0
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
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
        if abs(scale - self._surface_scale) < 0.0001 and abs(overlay_alpha - self._overlay_alpha) < 0.1:
            return
        self._surface_scale = scale
        self._overlay_alpha = overlay_alpha
        self.update()

    def sync_geometry(self) -> None:
        self.setGeometry(self.frame.rect())
        self.lower()
        self.show()
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        target = QRectF(self.rect())
        if self._surface_scale < 0.9999:
            inset_x = target.width() * (1.0 - self._surface_scale) * 0.5
            inset_y = target.height() * (1.0 - self._surface_scale) * 0.5
            target.adjust(inset_x, inset_y, -inset_x, -inset_y)
        painter.setBrush(QColor(0, 0, 0, int(self._overlay_alpha + 0.5)))
        painter.setPen(QPen(QColor(255, 255, 255, 38), 1.0))
        painter.drawRoundedRect(target.adjusted(0.5, 0.5, -0.5, -0.5), 6.0, 6.0)
        painter.end()


class WindowFrameOverlay(QWidget):
    """Visible in-client frame for the translucent frameless Windows shell."""

    _NORMAL_EDGE = 4
    _MAXIMIZED_EDGE = 1

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self.window = window
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.sync_geometry()

    def sync_geometry(self) -> None:
        self.setGeometry(self.window.rect())
        self.raise_()
        self.show()
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        del event
        if self.width() <= 2 or self.height() <= 2:
            return

        maximized = self.window.isMaximized()
        edge = self._MAXIMIZED_EDGE if maximized else self._NORMAL_EDGE
        active = self.window.isActiveWindow()

        # Dark outer edge provides a stable silhouette against both the bright
        # Fuji sky and a dark desktop. Everything is drawn inside the client
        # rect, so it does not affect layout geometry or the Quick background.
        outer = QColor(10, 18, 30, 210 if active else 150)
        highlight = QColor(255, 255, 255, 125 if active else 72)
        inner_shadow = QColor(0, 0, 0, 72 if active else 46)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        rect = self.rect()

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(outer)
        painter.drawRect(0, 0, rect.width(), edge)
        painter.drawRect(0, rect.height() - edge, rect.width(), edge)
        painter.drawRect(0, 0, edge, rect.height())
        painter.drawRect(rect.width() - edge, 0, edge, rect.height())

        if not maximized and rect.width() > 8 and rect.height() > 8:
            # One bright keyline and one soft inner line make the edge readable
            # without introducing a title bar or moving the existing business UI.
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(highlight, 1.0))
            painter.drawRect(rect.adjusted(edge - 1, edge - 1, -edge, -edge))
            painter.setPen(QPen(inner_shadow, 1.0))
            painter.drawRect(rect.adjusted(edge, edge, -edge - 1, -edge - 1))

        painter.end()


class VisualStyleController(QObject):
    """Static QWidget presentation plus one native Quick background surface."""

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self.window = window
        self._glass: dict[QFrame, GlassBackdrop] = {}
        self._cursor_installed = False

        # Qt requires FramelessWindowHint for per-pixel translucent QWidget
        # top-levels on Windows. Keep the business widget tree unchanged and
        # restore a visible in-client edge instead of rewriting the layout.
        window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        window.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        window.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.central = window.centralWidget()
        central = self.central
        if central is not None:
            central.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            central.setAutoFillBackground(False)
            central.setProperty("nativeQuickBackground", True)

        window.setStyleSheet(window.styleSheet() + "\n" + NEKRO_STYLE)
        for frame in window.findChildren(QFrame):
            if frame.objectName() in _GLASS_NAMES:
                frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
                self._glass[frame] = GlassBackdrop(frame)

        self.background = NativeQuickBackground(window)
        self.window_frame = WindowFrameOverlay(window)
        self._install_cursor()
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        window.destroyed.connect(self._cleanup)
        QTimer.singleShot(0, self._sync_visual_layers)

    def surface_for(self, frame: QFrame) -> GlassBackdrop | None:
        return self._glass.get(frame)

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

    def _sync_visual_layers(self) -> None:
        for backdrop in self._glass.values():
            backdrop.sync_geometry()
        self.background.schedule_mask_update()
        self.window_frame.sync_geometry()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        # Suppress only the legacy AtmosphereWidget background paint. Child
        # widgets keep receiving their own paint events and stay unchanged.
        if watched is self.central and event.type() == QEvent.Type.Paint:
            return True
        if watched is self.window and event.type() in {
            QEvent.Type.Resize,
            QEvent.Type.Show,
            QEvent.Type.WindowActivate,
            QEvent.Type.WindowDeactivate,
            QEvent.Type.WindowStateChange,
        }:
            QTimer.singleShot(0, self.window_frame.sync_geometry)
        if isinstance(watched, QFrame) and watched in self._glass:
            if event.type() in {QEvent.Type.Resize, QEvent.Type.Show}:
                QTimer.singleShot(0, self._glass[watched].sync_geometry)
        return False

    def _cleanup(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        self.background.shutdown()
        if self._cursor_installed:
            QApplication.restoreOverrideCursor()
            self._cursor_installed = False


def install_visual_style(window: QMainWindow) -> VisualStyleController:
    controller = VisualStyleController(window)
    window._visual_style = controller  # type: ignore[attr-defined]
    return controller
