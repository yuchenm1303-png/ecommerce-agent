from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QTimer, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtQuick import QQuickWindow
from PySide6.QtWidgets import QLabel, QMainWindow, QWidget


_RELEASE_DELAY_MS = 48


class RestoreSnapshotController(QObject):
    """Hide native QWidget backing-store rebuild behind the last complete frame.

    Minimize does not destroy the real QWidget tree. Windows/Qt can still discard
    the pixels backing that tree, which makes the first restore paints visible as
    a card-by-card rebuild. Keep one transparent QWidget snapshot in memory while
    minimized, place it above the live central widget on restore, and remove it
    only after the live surface has had a short repaint window underneath.

    The snapshot has no animation and no repeating timer. The only steady-state
    cost is one cached QPixmap; CPU work happens once when minimizing and once when
    restoring.
    """

    def __init__(self, window: QMainWindow, quick_window: QQuickWindow) -> None:
        super().__init__(window)
        self.window = window
        self.quick_window = quick_window
        self.central: QWidget | None = window.centralWidget()
        self._armed = False
        self._release_generation = 0

        self.snapshot = QLabel(self.central)
        self.snapshot.setObjectName("restoreFrameSnapshot")
        self.snapshot.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.snapshot.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.snapshot.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.snapshot.setScaledContents(False)
        self.snapshot.setStyleSheet("background: transparent; border: none;")
        self.snapshot.hide()

        quick_window.installEventFilter(self)
        window.installEventFilter(self)
        if self.central is not None:
            self.central.installEventFilter(self)

    def _is_minimized(self) -> bool:
        try:
            return bool(self.quick_window.windowState() & Qt.WindowState.WindowMinimized)
        except RuntimeError:
            return False

    def _capture(self) -> None:
        central = self.central
        if central is None or central.width() <= 0 or central.height() <= 0:
            return

        # Never recursively capture the previous snapshot.
        self.snapshot.hide()
        pixmap: QPixmap = central.grab()
        if pixmap.isNull():
            return

        self.snapshot.setPixmap(pixmap)
        self.snapshot.setGeometry(central.rect())
        self.snapshot.show()
        self.snapshot.raise_()
        self._armed = True

    def _show_cached_frame(self) -> None:
        if not self._armed or self.central is None or self._is_minimized():
            return

        self._release_generation += 1
        generation = self._release_generation
        self.snapshot.setGeometry(self.central.rect())
        self.snapshot.show()
        self.snapshot.raise_()

        # Let the real QWidget backing store rebuild while the exact previous
        # frame remains on top. No fade is used, so the visual design is unchanged.
        self.central.update()
        self.window.update()
        QTimer.singleShot(
            _RELEASE_DELAY_MS,
            lambda generation=generation: self._release_if_current(generation),
        )

    def _release_if_current(self, generation: int) -> None:
        if generation != self._release_generation or self._is_minimized():
            return
        self.snapshot.hide()
        self.snapshot.clear()
        self._armed = False

    def _resize_snapshot(self) -> None:
        if self.central is not None and self.snapshot.isVisible():
            self.snapshot.setGeometry(self.central.rect())

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        event_type = event.type()

        if watched is self.quick_window and event_type == QEvent.Type.WindowStateChange:
            if self._is_minimized():
                self._release_generation += 1
                self._capture()
            elif self._armed:
                QTimer.singleShot(0, self._show_cached_frame)

        elif watched is self.quick_window and event_type in {
            QEvent.Type.Show,
            QEvent.Type.Expose,
        }:
            if self._armed and not self._is_minimized():
                QTimer.singleShot(0, self._show_cached_frame)

        elif watched is self.central and event_type == QEvent.Type.Resize:
            self._resize_snapshot()

        elif watched is self.window and event_type == QEvent.Type.Close:
            self._release_generation += 1
            self.snapshot.hide()
            self.snapshot.clear()
            self._armed = False

        return False


def install_restore_snapshot(
    window: QMainWindow,
    quick_window: QQuickWindow,
) -> RestoreSnapshotController:
    existing = getattr(window, "_restore_snapshot_controller", None)
    if isinstance(existing, RestoreSnapshotController):
        return existing
    controller = RestoreSnapshotController(window, quick_window)
    window._restore_snapshot_controller = controller  # type: ignore[attr-defined]
    return controller
