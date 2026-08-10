from __future__ import annotations

from PySide6.QtCore import QEvent, QModelIndex, QObject, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QApplication,
    QFrame,
    QMainWindow,
    QWidget,
)

from .native_background import NativeQuickBackground
from .visual_style import NEKRO_STYLE


_GLASS_NAMES = {"glassCard", "heroCard", "statusCard", "microCard"}
_NORMAL_GLASS_ALPHA = 64.0
_GLASS_RADIUS = 6.0


def _interaction_overlay_alpha(target_alpha: float) -> int:
    """Return the local black overlay that composes base 64 to target alpha.

    Quick owns the stable base glass tint (64). Interactive hover/press tint is
    painted above that base in QWidget. Alpha composition is solved exactly so
    target 90/110 keeps the same visual darkness as the former Quick role update.
    """

    target = max(_NORMAL_GLASS_ALPHA, min(255.0, float(target_alpha)))
    if target <= _NORMAL_GLASS_ALPHA:
        return 0
    denominator = 255.0 - _NORMAL_GLASS_ALPHA
    return max(
        0,
        min(
            255,
            int(round(255.0 * (target - _NORMAL_GLASS_ALPHA) / denominator)),
        ),
    )


class _CardInteractionTint(QWidget):
    """Tiny synchronous hover/press layer inside one QWidget card.

    This layer is deliberately kept in the same renderer that receives mouse
    input. It sits below the card's labels/controls, above the native Quick glass,
    never accepts input, and repaints only the card rectangle.
    """

    def __init__(self, frame: QFrame) -> None:
        super().__init__(frame)
        self.frame = frame
        self._alpha = 0
        self.setObjectName("nativeCardInteractionTint")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.sync_geometry()
        self.show()
        self.lower()
        frame.installEventFilter(self)

    def set_target_alpha(self, target_alpha: float) -> None:
        alpha = _interaction_overlay_alpha(target_alpha)
        if alpha == self._alpha:
            return
        self._alpha = alpha
        # This is intentionally synchronous. Hover/press is a tiny card-local
        # paint and should be visible in the same GUI turn as the mouse event.
        self.repaint()

    def sync_geometry(self) -> None:
        geometry = self.frame.rect()
        if self.geometry() != geometry:
            self.setGeometry(geometry)
        self.lower()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self.frame and event.type() in {
            QEvent.Type.Resize,
            QEvent.Type.Show,
        }:
            self.sync_geometry()
        return False

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        if self._alpha <= 0 or self.width() <= 0 or self.height() <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, self._alpha))
        painter.drawRoundedRect(QRectF(self.rect()), _GLASS_RADIUS, _GLASS_RADIUS)
        painter.end()

    def cleanup(self) -> None:
        try:
            self.frame.removeEventFilter(self)
        except RuntimeError:
            pass


class NativeGlassProxy(QObject):
    """Stable Quick glass plus immediate QWidget interaction tint."""

    def __init__(self, frame: QFrame, background: NativeQuickBackground) -> None:
        super().__init__(frame)
        self.frame = frame
        self.background = background
        self._surface_scale = 1.0
        self._overlay_alpha = _NORMAL_GLASS_ALPHA
        self._interaction_tint = _CardInteractionTint(frame)

    @property
    def surface_scale(self) -> float:
        return self._surface_scale

    @property
    def overlay_alpha(self) -> float:
        return self._overlay_alpha

    def set_interaction(self, *, scale: float, overlay_alpha: float) -> None:
        # High-frequency input feedback stays entirely in QWidget. Crossing from
        # QWidget input -> QAbstractListModel -> QML -> threaded Quick present was
        # the source of inconsistent hover/click latency. Quick now remains at the
        # stable 64-alpha glass baseline while this tiny local tint composes the
        # exact target 90/110 darkness synchronously.
        scale = max(0.94, min(1.0, float(scale)))
        overlay_alpha = max(0.0, min(255.0, float(overlay_alpha)))
        if (
            abs(scale - self._surface_scale) < 0.0001
            and abs(overlay_alpha - self._overlay_alpha) < 0.1
        ):
            return
        self._surface_scale = scale
        self._overlay_alpha = overlay_alpha
        self._interaction_tint.set_target_alpha(overlay_alpha)

    def sync_geometry(self) -> None:
        self._interaction_tint.sync_geometry()
        self.background.schedule_mask_update()

    def cleanup(self) -> None:
        self._interaction_tint.cleanup()


class NativeVisualStyleController(QObject):
    """Native Quick background/base glass with QWidget interaction feedback."""

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
        UI plugins finish installing. Append new glass frames to the same stable
        Quick base-glass model and give each one the same local interaction tint.
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
        for surface in self._glass.values():
            surface.sync_geometry()
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
        for surface in tuple(self._glass.values()):
            try:
                surface.cleanup()
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
