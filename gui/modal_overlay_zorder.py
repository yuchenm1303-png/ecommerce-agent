from __future__ import annotations

from PySide6.QtCore import QObject, QTimer
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtWidgets import QMainWindow

from .modal_interaction import GlassModalInteractionController


_BIND_RETRIES = 4
_OPEN_HANDOFF_GUARD_MS = 310
_CLOSE_HANDOFF_GUARD_MS = 230


class ModalOverlayZOrderController(QObject):
    """Own native visibility for the reusable Quick transition surface.

    The transition QQuickWindow exists only for GPU motion and must never own
    application interaction after a transition. The normal path hides it when
    the QML item becomes inactive. A single-shot deadline guard independently
    completes the handoff if the render-thread completion signal is lost or a
    QWidget handoff raises, so a full-screen Quick child can never stay stuck
    above the real QWidget application.
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
        self._guard_opened = True

        self._handoff_guard = QTimer(self)
        self._handoff_guard.setSingleShot(True)
        self._handoff_guard.timeout.connect(self._force_handoff)

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
            self._handoff_guard.stop()
            return

        if bool(item.property("active")):
            closing = bool(item.property("closingRequest"))
            self._guard_opened = not closing
            self._handoff_guard.start(
                _CLOSE_HANDOFF_GUARD_MS if closing else _OPEN_HANDOFF_GUARD_MS
            )
            surface.show()
            surface.raise_()
            surface.requestUpdate()
            return

        self._handoff_guard.stop()
        surface.hide()

    def _force_handoff(self) -> None:
        """Fail-safe only; normal transitions finish through QML first."""

        item = self.modal.transition_item
        surface = self.modal.transition_window
        if not isinstance(item, QQuickItem) or not bool(item.property("active")):
            if isinstance(surface, QQuickWindow):
                surface.hide()
            return

        opened = self._guard_opened
        state = str(getattr(self.modal, "_state", ""))
        try:
            if opened and state == "opening":
                self.modal._on_transition_finished(True)  # noqa: SLF001
            elif not opened and state == "closing":
                self.modal._on_transition_finished(False)  # noqa: SLF001
            elif opened and state == "open":
                if bool(getattr(self.modal, "_prepared_modal", False)):
                    self.modal._reveal_prepared_modal()  # noqa: SLF001
                self.modal.root.repaint()
            elif not opened and state == "closed":
                self.modal.details.close()
                self.modal.root.repaint()
        except Exception as exc:  # fail-safe must still release the native overlay
            try:
                self.modal._transition_error = self.modal._error_text(exc)  # noqa: SLF001
            except Exception:
                pass
            try:
                if opened and bool(getattr(self.modal, "_prepared_modal", False)):
                    self.modal._reveal_prepared_modal()  # noqa: SLF001
                    self.modal.root.repaint()
                elif not opened:
                    self.modal.details.close()
                    self.modal.root.repaint()
            except Exception:
                pass
        finally:
            # Input ownership is non-negotiable: even if the QWidget handoff
            # failed, remove the full-screen Quick child from hit testing.
            try:
                if bool(item.property("active")):
                    self.modal._deactivate_transition()  # noqa: SLF001
            except Exception:
                try:
                    item.setProperty("active", False)
                except RuntimeError:
                    pass
            if isinstance(surface, QQuickWindow):
                surface.hide()

    def cleanup(self) -> None:
        self._handoff_guard.stop()
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
