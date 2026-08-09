from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QApplication, QFrame, QMainWindow, QWidget

from .native_background import NativeQuickBackground
from .visual_style import NEKRO_STYLE


_GLASS_NAMES = {"glassCard", "heroCard", "statusCard", "microCard"}


class NativeGlassBackdrop(QWidget):
    """Baseline glass tint/interaction surface; blur is composed once in Quick.

    The public interaction contract intentionally matches baseline GlassBackdrop
    so gui.nekro_card_fx stays byte-for-byte unchanged.
    """

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
        # Preserve the exact baseline API. Baseline card FX always supplies
        # scale=1.0 and animates only overlay alpha.
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

    def paintEvent(self, event) -> None:  # type: ignore[override]
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        target = QRectF(self.rect())
        if self._surface_scale < 0.9999:
            inset_x = target.width() * (1.0 - self._surface_scale) * 0.5
            inset_y = target.height() * (1.0 - self._surface_scale) * 0.5
            target.adjust(inset_x, inset_y, -inset_x, -inset_y)

        # Match the baseline composition exactly: rounded glass clip followed by
        # the animated black tint. The blurred wallpaper already exists directly
        # underneath this surface in the single global Quick mask.
        path = QPainterPath()
        path.addRoundedRect(target, 6.0, 6.0)
        painter.setClipPath(path)
        painter.fillRect(target, QColor(0, 0, 0, int(round(self._overlay_alpha))))
        painter.end()


class NativeVisualStyleController(QObject):
    """Performance adapter around the untouched baseline presentation layer."""

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self.window = window
        self._glass: dict[QFrame, NativeGlassBackdrop] = {}
        self._cursor_installed = False

        # The QWidget tree remains the baseline UI. It becomes only a translucent
        # native client surface so the Quick renderer can present underneath it.
        window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        window.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        window.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.central = window.centralWidget()
        if self.central is not None:
            self.central.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.central.setAutoFillBackground(False)

        # Reuse the baseline stylesheet verbatim; do not fork visual constants.
        window.setStyleSheet(window.styleSheet() + "\n" + NEKRO_STYLE)
        for frame in window.findChildren(QFrame):
            if frame.objectName() in _GLASS_NAMES:
                frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
                self._glass[frame] = NativeGlassBackdrop(frame)

        self.background = NativeQuickBackground(window)
        self._install_cursor()

        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        window.destroyed.connect(self._cleanup)
        QTimer.singleShot(0, self._sync_glass)

    def surface_for(self, frame: QFrame) -> NativeGlassBackdrop | None:
        return self._glass.get(frame)

    def _install_cursor(self) -> None:
        # Byte-for-byte visual equivalent of the baseline white-dot cursor.
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

    def _sync_glass(self) -> None:
        for backdrop in self._glass.values():
            backdrop.sync_geometry()
        self.background.schedule_mask_update()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        # Prevent only the baseline AtmosphereWidget full-window paint. All child
        # controls, card FX, petals and business widgets remain baseline code.
        if watched is self.central and event.type() == QEvent.Type.Paint:
            return True

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


def install_native_visual_style(window: QMainWindow) -> NativeVisualStyleController:
    controller = NativeVisualStyleController(window)
    window._visual_style = controller  # type: ignore[attr-defined]
    return controller
