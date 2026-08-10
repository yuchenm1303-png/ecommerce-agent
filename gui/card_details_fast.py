from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QFrame, QMainWindow

from .card_details import CardDetailController
from .overlay_sheet_motion import ClipSheetMotion


_GEOMETRY_COALESCE_MS = 32


class FastCardDetailController(CardDetailController):
    """Right-side detail sheet whose live body never changes animation size."""

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)

        # Remove the old QWidget opacity/geometry animation surfaces. The drawer
        # itself stays at final size inside a clipping viewport.
        self.drawer.setGraphicsEffect(None)
        self.drawer_effect = None  # type: ignore[assignment]
        self.ghost.setGraphicsEffect(None)
        self.ghost_effect = None  # type: ignore[assignment]
        self.ghost.hide()

        self._motion = ClipSheetMotion(
            self.root,
            self.drawer,
            self._drawer_rect,
            edge="right",
            duration_ms=158,
        )
        self._motion.opened.connect(self._finish_open)
        self._motion.closed.connect(self._finish_close)

        self._geometry_timer = QTimer(self)
        self._geometry_timer.setSingleShot(True)
        self._geometry_timer.setInterval(_GEOMETRY_COALESCE_MS)
        self._geometry_timer.timeout.connect(self._sync_geometry)

    def _schedule_geometry(self) -> None:
        if not self._geometry_timer.isActive():
            self._geometry_timer.start()

    def _sync_geometry(self) -> None:
        self.scrim.setGeometry(self.root.rect())
        for frame in self._installed_cards:
            self._position_button(frame)
        self._motion.sync_geometry()

    def _stop_animation(self) -> None:
        animation = self._animation
        self._animation = None
        if animation is not None:
            animation.stop()
            animation.deleteLater()
        self._motion.stop()
        self.ghost.hide()

    def open(self, frame: QFrame) -> None:
        if frame not in self._buttons:
            return

        anchored = getattr(self.window, "_anchored_sheets", None)
        if anchored is not None and hasattr(anchored, "close_all"):
            anchored.close_all()

        self._stop_animation()
        self._selected = frame
        self._populate(frame)
        self.body_layout.activate()
        if self.drawer.layout() is not None:
            self.drawer.layout().activate()
        self.scroll.verticalScrollBar().setValue(0)

        self.scrim.setGeometry(self.root.rect())
        self.scrim.show()
        self.scrim.raise_()
        self._motion.open()
        self._motion.viewport.raise_()
        self.ghost.hide()

    def _finish_open(self) -> None:
        self._motion.viewport.raise_()
        self.close_button.setFocus(Qt.FocusReason.OtherFocusReason)

    def close(self) -> None:
        if self._selected is None and not self.scrim.isVisible():
            return
        self._motion.close()

    def _finish_close(self) -> None:
        self.scrim.hide()
        self.ghost.hide()
        self._selected = None

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        event_type = event.type()
        if watched is self.root:
            if event_type in {QEvent.Type.Resize, QEvent.Type.Show}:
                self._schedule_geometry()
            elif event_type == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
                if event.key() == Qt.Key.Key_Escape and self.scrim.isVisible():
                    self.close()
                    return True
        elif isinstance(watched, QFrame) and watched in self._buttons:
            if event_type in {QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.LayoutRequest}:
                self._schedule_geometry()
        return False

    def _cleanup(self) -> None:
        self._geometry_timer.stop()
        self._motion.cleanup()
        super()._cleanup()


def install_card_details(window: QMainWindow) -> FastCardDetailController:
    controller = FastCardDetailController(window)
    window._card_details = controller  # type: ignore[attr-defined]
    window.destroyed.connect(controller._cleanup)
    return controller
