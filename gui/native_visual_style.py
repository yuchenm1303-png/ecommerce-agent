from __future__ import annotations

from PySide6.QtCore import QEvent, QModelIndex, QObject, QRectF, Qt, QTimer
from PySide6.QtGui import QCursor, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QApplication,
    QFrame,
    QMainWindow,
)

from .card_gpu_snapshot import CardGpuSnapshotPool
from .native_background import NativeQuickBackground
from .nekro_style import NEKRO_STYLE


_GLASS_NAMES = {"glassCard", "heroCard", "statusCard", "microCard"}
_NORMAL_GLASS_ALPHA = 64.0


class NativeGlassProxy(QObject):
    """Publish one card interaction to Quick glass and the GPU snapshot surface."""

    def __init__(
        self,
        frame: QFrame,
        background: NativeQuickBackground,
        snapshots: CardGpuSnapshotPool,
    ) -> None:
        super().__init__(frame)
        self.frame = frame
        self.background = background
        self.snapshots = snapshots
        self._surface_scale = 1.0
        self._overlay_alpha = _NORMAL_GLASS_ALPHA

    @property
    def surface_scale(self) -> float:
        return self._surface_scale

    @property
    def overlay_alpha(self) -> float:
        return self._overlay_alpha

    def set_content_frozen(self, frozen: bool) -> None:
        if frozen:
            self.snapshots.capture(self.frame, scale=self._surface_scale)
        else:
            self.snapshots.release(self.frame)

    def set_interaction(self, *, scale: float, overlay_alpha: float) -> None:
        scale = max(0.96, min(1.04, float(scale)))
        overlay_alpha = max(_NORMAL_GLASS_ALPHA, min(255.0, float(overlay_alpha)))
        if (
            abs(scale - self._surface_scale) < 0.0001
            and abs(overlay_alpha - self._overlay_alpha) < 0.1
        ):
            return
        self._surface_scale = scale
        self._overlay_alpha = overlay_alpha
        self.background.set_card_presentation(
            self.frame,
            scale=scale,
            alpha=overlay_alpha,
        )
        self.snapshots.set_scale(self.frame, scale)

    def sync_geometry(self) -> None:
        self.background.schedule_mask_update()

    def cleanup(self) -> None:
        try:
            self.snapshots.release(self.frame)
            self.background.set_card_presentation(
                self.frame,
                scale=1.0,
                alpha=_NORMAL_GLASS_ALPHA,
            )
        except RuntimeError:
            pass


class NativeVisualStyleController(QObject):
    """The single formal QWidget/Quick visual bridge."""

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self.window = window
        self._glass: dict[QFrame, NativeGlassProxy] = {}
        self._cursor_installed = False
        self._mode_stack_glass_connected = False

        window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        window.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        window.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.central = window.centralWidget()
        if self.central is not None:
            self.central.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.central.setAutoFillBackground(False)
            self.central.installEventFilter(self)

        window.setStyleSheet(window.styleSheet() + "\n" + NEKRO_STYLE)
        self.background = NativeQuickBackground(window)
        self.card_snapshots = CardGpuSnapshotPool(window)
        for frame in window.findChildren(QFrame):
            if frame.objectName() in _GLASS_NAMES:
                frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
                self._glass[frame] = NativeGlassProxy(
                    frame,
                    self.background,
                    self.card_snapshots,
                )

        self._install_cursor()
        window.destroyed.connect(self._cleanup)
        QTimer.singleShot(0, self._sync_glass)

    def surface_for(self, frame: QFrame) -> NativeGlassProxy | None:
        return self._glass.get(frame)

    def refresh_glass_frames(self) -> int:
        """Register cards created after the Quick scene was constructed."""

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
                model._states.append(model.default_state())
                self._glass[frame] = NativeGlassProxy(
                    frame,
                    self.background,
                    self.card_snapshots,
                )
        finally:
            model.endInsertRows()

        for frame in new_frames:
            current = frame
            while current is not None:
                if current not in self.background._geometry_watch:
                    self.background._geometry_watch.add(current)
                    current.installEventFilter(self.background)
                if current is self.window:
                    break
                current = current.parentWidget()

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
        self.card_snapshots.cleanup()
        self.background.shutdown()
        if self._cursor_installed:
            QApplication.restoreOverrideCursor()
            self._cursor_installed = False


def install_native_visual_style(window: QMainWindow) -> NativeVisualStyleController:
    controller = NativeVisualStyleController(window)
    window._visual_style = controller  # type: ignore[attr-defined]
    return controller


__all__ = ["NativeGlassProxy", "NativeVisualStyleController", "install_native_visual_style"]
