from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QFrame, QMainWindow

from .card_details import CardDetailController


_GEOMETRY_COALESCE_MS = 32


class FastCardDetailController(CardDetailController):
    """Stable detail drawer opened directly from the card surface.

    The legacy top-right expand affordance is removed before the window is
    shown.  Clicking a card's own non-interactive surface opens the same atomic
    detail drawer; child controls keep their native mouse handling and therefore
    do not accidentally trigger details.
    """

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)

        # The base controller creates opacity effects for its animated path.
        # This controller has no animated path, so remove those offscreen
        # composition surfaces completely.
        self.drawer.setGraphicsEffect(None)
        self.drawer_effect = None  # type: ignore[assignment]
        self.ghost.setGraphicsEffect(None)
        self.ghost_effect = None  # type: ignore[assignment]
        self.ghost.hide()

        # Keep the base discovery logic, but remove the visible expand buttons.
        # The frame event filters installed by the base controller become the
        # click targets instead.  This happens before shell.show(), so the old
        # affordance never flashes on screen.
        self._expandable_cards = tuple(self._installed_cards)
        for button in tuple(self._buttons.values()):
            button.hide()
            button.setParent(None)
            button.deleteLater()
        self._buttons.clear()

        self._geometry_timer = QTimer(self)
        self._geometry_timer.setSingleShot(True)
        self._geometry_timer.setInterval(_GEOMETRY_COALESCE_MS)
        self._geometry_timer.timeout.connect(self._sync_geometry)

    def attach_mature(self, mature: QObject) -> None:
        """Reclaim the lane that ui_maturity used to reserve for the icon.

        MatureResponsiveController reapplies presentation margins after resize,
        so run the lightweight reclaim immediately after its own coalesced apply
        callback.  No extra timer or global event filter is introduced.
        """

        self._reclaim_expand_lane()
        timer = getattr(mature, "_timer", None)
        if isinstance(timer, QTimer):
            timer.timeout.connect(self._reclaim_expand_lane)

    def _reclaim_expand_lane(self) -> None:
        for frame in self._expandable_cards:
            layout = frame.layout()
            if layout is None:
                continue
            margins = layout.contentsMargins()
            # ui_maturity's legacy expand lane is 38 px. Restore the normal
            # symmetric card padding only when that lane is present.
            if margins.right() >= 38:
                right = margins.left() if margins.left() > 0 else 10
                layout.setContentsMargins(margins.left(), margins.top(), right, margins.bottom())

    def _schedule_geometry(self) -> None:
        if not self._geometry_timer.isActive():
            self._geometry_timer.start()

    def _sync_geometry(self) -> None:
        self.scrim.setGeometry(self.root.rect())
        if self.drawer.isVisible():
            self.drawer.setGeometry(self._drawer_rect())

    def _stop_animation(self) -> None:
        # Defensive cleanup for the base API. No animation is created here.
        animation = self._animation
        self._animation = None
        if animation is not None:
            animation.stop()
            animation.deleteLater()
        self.ghost.hide()

    def open(self, frame: QFrame) -> None:
        if frame not in self._expandable_cards:
            return

        self._stop_animation()
        updates_were_enabled = self.root.updatesEnabled()
        if updates_were_enabled:
            self.root.setUpdatesEnabled(False)

        try:
            self._selected = frame
            self._populate(frame)
            self.body_layout.activate()
            if self.drawer.layout() is not None:
                self.drawer.layout().activate()
            self.scroll.verticalScrollBar().setValue(0)

            self.scrim.setGeometry(self.root.rect())
            self.drawer.setGeometry(self._drawer_rect())
            self.scrim.show()
            self.scrim.raise_()
            self.drawer.show()
            self.drawer.raise_()
            self.ghost.hide()
        finally:
            if updates_were_enabled:
                self.root.setUpdatesEnabled(True)
                self.root.update()

        self.close_button.setFocus(Qt.FocusReason.OtherFocusReason)
        self._schedule_geometry()

    def close(self) -> None:
        if not self.drawer.isVisible() and not self.scrim.isVisible():
            return

        self._stop_animation()
        updates_were_enabled = self.root.updatesEnabled()
        if updates_were_enabled:
            self.root.setUpdatesEnabled(False)
        try:
            self.drawer.hide()
            self.ghost.hide()
            self.scrim.hide()
            self._selected = None
        finally:
            if updates_were_enabled:
                self.root.setUpdatesEnabled(True)
                self.root.update()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        event_type = event.type()
        if watched is self.root:
            if event_type in {QEvent.Type.Resize, QEvent.Type.Show}:
                self._schedule_geometry()
            elif event_type == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
                if event.key() == Qt.Key.Key_Escape and self.drawer.isVisible():
                    self.close()
                    return True
        elif isinstance(watched, QFrame) and watched in self._expandable_cards:
            if event_type in {QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.LayoutRequest}:
                self._schedule_geometry()
            elif event_type == QEvent.Type.MouseButtonRelease and isinstance(event, QMouseEvent):
                # Interactive children (buttons, inputs, tables, scroll areas)
                # consume their own mouse events. Only a release that reaches the
                # card surface is treated as the card-level detail action.
                if event.button() == Qt.MouseButton.LeftButton:
                    self.open(watched)
                    event.accept()
                    return True
        return False

    def _cleanup(self) -> None:
        self._geometry_timer.stop()
        super()._cleanup()


def install_card_details(window: QMainWindow) -> FastCardDetailController:
    controller = FastCardDetailController(window)
    window._card_details = controller  # type: ignore[attr-defined]
    window.destroyed.connect(controller._cleanup)
    return controller
