from __future__ import annotations

from PySide6.QtCore import QEvent, QModelIndex, QObject, QRectF, Qt, QTimer
from PySide6.QtGui import QCursor, QPainter, QPixmap
from PySide6.QtWidgets import QAbstractScrollArea, QApplication, QFrame, QMainWindow

from .native_background import NativeQuickBackground
from .visual_style import NEKRO_STYLE


_GLASS_NAMES = {"glassCard", "heroCard", "statusCard", "microCard"}
_NORMAL_GLASS_ALPHA = 64.0


class NativeGlassProxy(QObject):
    """Baseline GlassBackdrop API with all glass pixels rendered in Quick."""

    def __init__(self, frame: QFrame, background: NativeQuickBackground) -> None:
        super().__init__(frame)
        self.frame = frame
        self.background = background
        self._surface_scale = 1.0
        self._overlay_alpha = _NORMAL_GLASS_ALPHA

    @property
    def surface_scale(self) -> float:
        return self._surface_scale

    @property
    def overlay_alpha(self) -> float:
        return self._overlay_alpha

    def set_interaction(self, *, scale: float, overlay_alpha: float) -> None:
        # Baseline card FX supplies scale=1.0 and alpha-only interaction states.
        # Publish the model role and explicitly request one Quick frame so hover
        # and press feedback is not left waiting behind unrelated GUI events.
        scale = max(0.94, min(1.0, float(scale)))
        overlay_alpha = max(0.0, min(255.0, float(overlay_alpha)))
        if (
            abs(scale - self._surface_scale) < 0.0001
            and abs(overlay_alpha - self._overlay_alpha) < 0.1
        ):
            return
        self._surface_scale = scale
        self._overlay_alpha = overlay_alpha
        self.background.set_card_alpha(self.frame, overlay_alpha)
        quick = self.background.quick_window
        if quick is not None:
            try:
                quick.requestUpdate()
            except RuntimeError:
                pass

    def sync_geometry(self) -> None:
        self.background.schedule_mask_update()


class NativeVisualStyleController(QObject):
    """Baseline presentation adapter with only wallpaper/glass moved to Quick."""

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self.window = window
        self._glass: dict[QFrame, NativeGlassProxy] = {}
        self._cursor_installed = False
        self._mode_stack_glass_connected = False

        # The QWidget tree remains the baseline UI. Only the top-level client
        # surface is translucent so the native Quick scene can present below it.
        window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        window.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        window.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.central = window.centralWidget()
        if self.central is not None:
            self.central.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.central.setAutoFillBackground(False)
            # This filter only suppresses AtmosphereWidget's legacy wallpaper
            # paint. Installing it globally made every Qt event cross Python.
            self.central.installEventFilter(self)

        # Reuse baseline style constants verbatim. No replacement card border,
        # tint or hover CSS is introduced here.
        window.setStyleSheet(window.styleSheet() + "\n" + NEKRO_STYLE)

        self.background = NativeQuickBackground(window)
        for frame in window.findChildren(QFrame):
            if frame.objectName() in _GLASS_NAMES:
                frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
                self._glass[frame] = NativeGlassProxy(frame, self.background)

        self._install_cursor()
        window.destroyed.connect(self._cleanup)
        QTimer.singleShot(0, self._sync_glass)

    def surface_for(self, frame: QFrame) -> NativeGlassProxy | None:
        return self._glass.get(frame)

    def refresh_glass_frames(self) -> int:
        """Register glass cards created after the native Quick scene started.

        Batch Workspace is intentionally constructed only after the proven Single
        UI plugins finish installing. The Quick background therefore cannot see
        those later QFrames during its initial one-shot scan. Append only the new
        glass frames to the existing model so Single and Batch share the exact
        same blur mask/tint renderer instead of introducing a second glass path.
        """

        new_frames = [
            frame
            for frame in self.window.findChildren(QFrame)
            if frame.objectName() in _GLASS_NAMES and frame not in self._glass
        ]
        if not new_frames:
            self.background.schedule_mask_update()
            return 0

        model = self.background.card_model
        first_row = len(model.cards)
        last_row = first_row + len(new_frames) - 1
        model.beginInsertRows(QModelIndex(), first_row, last_row)
        try:
            for frame in new_frames:
                row = len(model.cards)
                frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
                model.cards.append(frame)
                model._rows[frame] = row
                model._states.append(
                    {
                        "cardX": 0.0,
                        "cardY": 0.0,
                        "cardW": 0.0,
                        "cardH": 0.0,
                        "clipX": 0.0,
                        "clipY": 0.0,
                        "clipW": 0.0,
                        "clipH": 0.0,
                        "cardAlpha": _NORMAL_GLASS_ALPHA,
                        "cardVisible": False,
                    }
                )
                self._glass[frame] = NativeGlassProxy(frame, self.background)
        finally:
            model.endInsertRows()

        # NativeQuickBackground intentionally watches only relevant widgets, not
        # the whole QApplication. Extend that same scoped watch set to Batch card
        # ancestors so stack changes, layout changes and resizes refresh the mask.
        for frame in new_frames:
            current = frame
            while current is not None:
                if current not in self.background._geometry_watch:
                    self.background._geometry_watch.add(current)
                    current.installEventFilter(self.background)
                if current is self.window:
                    break
                current = current.parentWidget()

        # Batch owns its own tables/scroll areas, created after background init.
        # Connect only scroll areas under the newly registered cards.
        scroll_areas: set[QAbstractScrollArea] = set()
        for frame in new_frames:
            scroll_areas.update(frame.findChildren(QAbstractScrollArea))
        for area in scroll_areas:
            area.verticalScrollBar().valueChanged.connect(self.background.schedule_mask_update)
            area.horizontalScrollBar().valueChanged.connect(self.background.schedule_mask_update)

        mode_stack = getattr(self.window, "mode_stack", None)
        if mode_stack is not None and not self._mode_stack_glass_connected:
            mode_stack.currentChanged.connect(
                lambda *_: QTimer.singleShot(0, self.background.schedule_mask_update)
            )
            self._mode_stack_glass_connected = True

        QTimer.singleShot(0, self.background.schedule_mask_update)
        return len(new_frames)

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

    def _sync_glass(self) -> None:
        self.background.schedule_mask_update()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        # Suppress only the baseline AtmosphereWidget wallpaper paint. Card
        # content/controls remain the original QWidget implementation.
        if watched is self.central and event.type() == QEvent.Type.Paint:
            return True
        return False

    def _cleanup(self) -> None:
        if self.central is not None:
            try:
                self.central.removeEventFilter(self)
            except RuntimeError:
                pass
        self.background.shutdown()
        if self._cursor_installed:
            QApplication.restoreOverrideCursor()
            self._cursor_installed = False


def install_native_visual_style(window: QMainWindow) -> NativeVisualStyleController:
    controller = NativeVisualStyleController(window)
    window._visual_style = controller  # type: ignore[attr-defined]
    return controller
