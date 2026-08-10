from __future__ import annotations

from PySide6.QtCore import QObject, QTimer
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtWidgets import QMainWindow

from .modal_interaction import GlassModalInteractionController


_BIND_RETRIES = 4


class ModalOverlayZOrderController(QObject):
    """Map the reusable Quick transition surface only while it is animating.

    The QQuickWindow/QML scene is created once and retained for the process
    lifetime, but the native transition window is hidden whenever the transition
    item is inactive. This keeps it completely out of Windows hit testing while
    the real QWidget modal owns interaction. The main QWidget child HWND is never
    hidden, recreated, or reordered.
    """

    def __init__(
        self,
        window: QMainWindow,
        modal: GlassModalInteractionController,
    ) -> None:
        super().__init__(window)
        self.window = window
        self.modal = modal
        self._bound_item: QQuickItem | None = None
        self._bind_attempts = 0
        QTimer.singleShot(0, self._bind_after_modal_prime)
        window.destroyed.connect(self.cleanup)

    def _bind_after_modal_prime(self) -> None:
        item = self.modal.transition_item
        surface = self.modal.transition_window
        if not isinstance(item, QQuickItem) or not isinstance(surface, QQuickWindow):
            self._bind_attempts += 1
            if self._bind_attempts < _BIND_RETRIES:
                QTimer.singleShot(0, self._bind_after_modal_prime)
            return

        self._bind_attempts = _BIND_RETRIES
        if self._bound_item is item:
            self._sync_surface_visibility()
            return

        active_changed = getattr(item, "activeChanged", None)
        if active_changed is None:
            surface.hide()
            return

        active_changed.connect(self._sync_surface_visibility)
        self._bound_item = item
        self._sync_surface_visibility()

    def _sync_surface_visibility(self) -> None:
        item = self.modal.transition_item
        surface = self.modal.transition_window
        if not isinstance(item, QQuickItem) or not isinstance(surface, QQuickWindow):
            return

        if bool(item.property("active")):
            surface.show()
            surface.raise_()
            surface.requestUpdate()
            return

        surface.hide()

    def cleanup(self) -> None:
        self._bind_attempts = _BIND_RETRIES
        item = self._bound_item
        self._bound_item = None
        if item is None:
            return
        active_changed = getattr(item, "activeChanged", None)
        if active_changed is not None:
            try:
                active_changed.disconnect(self._sync_surface_visibility)
            except (RuntimeError, TypeError):
                pass


def install_modal_overlay_zorder(
    window: QMainWindow,
    modal: GlassModalInteractionController,
) -> ModalOverlayZOrderController:
    controller = ModalOverlayZOrderController(window, modal)
    window._modal_overlay_zorder = controller  # type: ignore[attr-defined]
    return controller
