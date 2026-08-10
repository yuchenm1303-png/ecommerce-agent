from __future__ import annotations

from PySide6.QtCore import QObject, QTimer
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtWidgets import QMainWindow

from .modal_interaction import GlassModalInteractionController


class ModalOverlayZOrderController(QObject):
    """Keep the permanent Quick overlay mapped without blocking QWidget input.

    The transition QQuickWindow stays alive for the process lifetime, but it is
    only above the QWidget child while a scene-graph transition is actually
    running. When idle or when the real modal is open, it is lowered below the
    QWidget surface so close buttons, scrim clicks and keyboard focus remain
    owned by the real application UI.
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
        QTimer.singleShot(0, self._bind_after_modal_prime)
        window.destroyed.connect(self.cleanup)

    def _bind_after_modal_prime(self) -> None:
        item = self.modal.transition_item
        surface = self.modal.transition_window
        if not isinstance(item, QQuickItem) or not isinstance(surface, QQuickWindow):
            return
        if self._bound_item is item:
            surface.lower()
            return

        command_changed = getattr(item, "commandChanged", None)
        transition_finished = getattr(item, "transitionFinished", None)
        if command_changed is None or transition_finished is None:
            surface.lower()
            return

        command_changed.connect(self._raise_for_transition)
        transition_finished.connect(self._lower_after_transition)
        self._bound_item = item
        surface.lower()

    def _raise_for_transition(self) -> None:
        surface = self.modal.transition_window
        if isinstance(surface, QQuickWindow):
            surface.raise_()

    def _lower_after_transition(self, *_args: object) -> None:
        surface = self.modal.transition_window
        if isinstance(surface, QQuickWindow):
            surface.lower()

    def cleanup(self) -> None:
        item = self._bound_item
        self._bound_item = None
        if item is None:
            return
        command_changed = getattr(item, "commandChanged", None)
        transition_finished = getattr(item, "transitionFinished", None)
        if command_changed is not None:
            try:
                command_changed.disconnect(self._raise_for_transition)
            except (RuntimeError, TypeError):
                pass
        if transition_finished is not None:
            try:
                transition_finished.disconnect(self._lower_after_transition)
            except (RuntimeError, TypeError):
                pass


def install_modal_overlay_zorder(
    window: QMainWindow,
    modal: GlassModalInteractionController,
) -> ModalOverlayZOrderController:
    controller = ModalOverlayZOrderController(window, modal)
    window._modal_overlay_zorder = controller  # type: ignore[attr-defined]
    return controller
