from __future__ import annotations

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QEvent,
    QObject,
    QParallelAnimationGroup,
    QPoint,
    QPropertyAnimation,
    Qt,
    QTimer,
)
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QFrame, QGraphicsOpacityEffect, QLabel, QMainWindow

from .card_details_fast import FastCardDetailController


_OPEN_BACKDROP_MS = 170
_OPEN_SCRIM_MS = 180
_OPEN_PANEL_MS = 210
_CLOSE_BACKDROP_MS = 145
_CLOSE_SCRIM_MS = 150
_CLOSE_PANEL_MS = 160
_PANEL_RISE_PX = 14
_PANEL_CLOSE_DROP_PX = 10


class GlassModalInteractionController(QObject):
    """Polish the shared glass modal without animating the real application layout.

    Only three cheap presentation properties move during a transition:
    * opacity of the already-blurred backdrop pixmap,
    * opacity of the flat scrim,
    * position of the final-size modal panel.

    The panel width/height, its child layouts, the main splitters and all source
    cards remain fixed for the entire transition.
    """

    def __init__(self, window: QMainWindow, details: FastCardDetailController) -> None:
        super().__init__(window)
        self.window = window
        self.details = details
        self.root = window.centralWidget()
        if self.root is None:
            raise RuntimeError("glass modal interaction requires a central widget")

        self._group: QParallelAnimationGroup | None = None
        self._closing = False
        self._opening = False
        self._label_cards: dict[QLabel, QFrame] = {}

        self.backdrop_effect = QGraphicsOpacityEffect(self.details.backdrop)
        self.backdrop_effect.setOpacity(0.0)
        self.details.backdrop.setGraphicsEffect(self.backdrop_effect)

        self.scrim_effect = QGraphicsOpacityEffect(self.details.scrim)
        self.scrim_effect.setOpacity(0.0)
        self.details.scrim.setGraphicsEffect(self.scrim_effect)

        # The modal panel itself deliberately has no graphics effect. Moving a
        # final-size QWidget by a few pixels is substantially cheaper than
        # re-compositing all of its table/text children through an opacity effect.
        self.details.drawer.setGraphicsEffect(None)

        self.details.drawer.installEventFilter(self)
        self.root.installEventFilter(self)
        self._install_card_click_forwarding()
        self._rewire_close_inputs()
        window.destroyed.connect(self.cleanup)

    def _rewire_close_inputs(self) -> None:
        try:
            self.details.close_button.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        self.details.close_button.clicked.connect(self.request_close)

        try:
            self.details.scrim.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        self.details.scrim.clicked.connect(self.request_close)

    @staticmethod
    def _label_is_passive(label: QLabel) -> bool:
        flags = label.textInteractionFlags()
        interactive = (
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        return not bool(flags & interactive)

    def _install_card_click_forwarding(self) -> None:
        for card in self.details._expandable_cards:  # noqa: SLF001 - presentation adapter
            card.setCursor(Qt.CursorShape.PointingHandCursor)
            for label in card.findChildren(QLabel):
                if not self._label_is_passive(label):
                    continue
                label.installEventFilter(self)
                label.setCursor(Qt.CursorShape.PointingHandCursor)
                self._label_cards[label] = card

    @staticmethod
    def _animation(
        target: QObject,
        prop: bytes,
        start: object,
        end: object,
        duration: int,
        easing: QEasingCurve.Type,
    ) -> QPropertyAnimation:
        animation = QPropertyAnimation(target, prop)
        animation.setStartValue(start)
        animation.setEndValue(end)
        animation.setDuration(duration)
        animation.setEasingCurve(easing)
        return animation

    def _stop_group(self) -> None:
        group = self._group
        self._group = None
        if group is not None:
            group.stop()
            group.deleteLater()
        self._opening = False

    def _prepare_open(self) -> None:
        target = self.details._drawer_rect()  # noqa: SLF001 - shared modal geometry
        self.backdrop_effect.setOpacity(0.0)
        self.scrim_effect.setOpacity(0.0)
        self.details.drawer.setGeometry(target)
        self.details.drawer.move(target.x(), target.y() + _PANEL_RISE_PX)
        self._opening = True
        self._closing = False
        QTimer.singleShot(0, self._start_open)

    def _start_open(self) -> None:
        if not self.details.drawer.isVisible() or self._closing:
            return
        self._stop_group()
        self._opening = True

        geometry_timer = getattr(self.details, "_geometry_timer", None)
        if isinstance(geometry_timer, QTimer):
            geometry_timer.stop()

        target = self.details._drawer_rect()  # noqa: SLF001
        start_pos = QPoint(target.x(), target.y() + _PANEL_RISE_PX)
        self.details.drawer.setGeometry(target)
        self.details.drawer.move(start_pos)

        group = QParallelAnimationGroup(self)
        group.addAnimation(
            self._animation(
                self.backdrop_effect,
                b"opacity",
                0.0,
                1.0,
                _OPEN_BACKDROP_MS,
                QEasingCurve.Type.OutCubic,
            )
        )
        group.addAnimation(
            self._animation(
                self.scrim_effect,
                b"opacity",
                0.0,
                1.0,
                _OPEN_SCRIM_MS,
                QEasingCurve.Type.OutCubic,
            )
        )
        group.addAnimation(
            self._animation(
                self.details.drawer,
                b"pos",
                start_pos,
                target.topLeft(),
                _OPEN_PANEL_MS,
                QEasingCurve.Type.OutQuart,
            )
        )
        group.finished.connect(self._finish_open)
        self._group = group
        group.start(QAbstractAnimation.DeletionPolicy.KeepWhenStopped)

    def _finish_open(self) -> None:
        group = self._group
        self._group = None
        if group is not None:
            group.deleteLater()
        self._opening = False
        self.backdrop_effect.setOpacity(1.0)
        self.scrim_effect.setOpacity(1.0)
        if self.details.drawer.isVisible():
            self.details.drawer.setGeometry(self.details._drawer_rect())  # noqa: SLF001

    def request_close(self, *_args: object) -> None:
        if self._closing:
            return
        if not self.details.drawer.isVisible() and not self.details.scrim.isVisible():
            return

        self._stop_group()
        self._closing = True
        target_pos = self.details.drawer.pos() + QPoint(0, _PANEL_CLOSE_DROP_PX)

        group = QParallelAnimationGroup(self)
        group.addAnimation(
            self._animation(
                self.backdrop_effect,
                b"opacity",
                self.backdrop_effect.opacity(),
                0.0,
                _CLOSE_BACKDROP_MS,
                QEasingCurve.Type.InCubic,
            )
        )
        group.addAnimation(
            self._animation(
                self.scrim_effect,
                b"opacity",
                self.scrim_effect.opacity(),
                0.0,
                _CLOSE_SCRIM_MS,
                QEasingCurve.Type.InCubic,
            )
        )
        group.addAnimation(
            self._animation(
                self.details.drawer,
                b"pos",
                self.details.drawer.pos(),
                target_pos,
                _CLOSE_PANEL_MS,
                QEasingCurve.Type.InCubic,
            )
        )
        group.finished.connect(self._finish_close)
        self._group = group
        group.start(QAbstractAnimation.DeletionPolicy.KeepWhenStopped)

    def _finish_close(self) -> None:
        group = self._group
        self._group = None
        if group is not None:
            group.deleteLater()
        self._closing = False
        self._opening = False
        # FastCardDetailController.close() is atomic and performs the one final
        # repaint after hiding the prepared modal layers.
        self.details.close()
        self.backdrop_effect.setOpacity(0.0)
        self.scrim_effect.setOpacity(0.0)

    def _snap_after_resize(self) -> None:
        if not self.details.drawer.isVisible() or self._closing:
            return
        self._stop_group()
        self.backdrop_effect.setOpacity(1.0)
        self.scrim_effect.setOpacity(1.0)
        self.details._sync_geometry()  # noqa: SLF001 - one final geometry reconcile

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        event_type = event.type()

        if watched is self.details.drawer and event_type == QEvent.Type.Show:
            self._prepare_open()
            return False

        card = self._label_cards.get(watched) if isinstance(watched, QLabel) else None
        if card is not None and event_type == QEvent.Type.MouseButtonRelease:
            if (
                isinstance(event, QMouseEvent)
                and event.button() == Qt.MouseButton.LeftButton
                and not self.details.drawer.isVisible()
            ):
                self.details.open(card)
                event.accept()
                return True

        if watched is self.root:
            if event_type == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
                if event.key() == Qt.Key.Key_Escape and self.details.drawer.isVisible():
                    self.request_close()
                    return True
            elif event_type == QEvent.Type.Resize and self.details.drawer.isVisible():
                QTimer.singleShot(0, self._snap_after_resize)

        return False

    def cleanup(self) -> None:
        self._stop_group()
        try:
            self.details.drawer.removeEventFilter(self)
        except RuntimeError:
            pass
        try:
            self.root.removeEventFilter(self)
        except RuntimeError:
            pass
        for label in tuple(self._label_cards):
            try:
                label.removeEventFilter(self)
            except RuntimeError:
                pass
        self._label_cards.clear()


def install_modal_interaction(
    window: QMainWindow,
    details: FastCardDetailController,
) -> GlassModalInteractionController:
    controller = GlassModalInteractionController(window, details)
    window._glass_modal_interaction = controller  # type: ignore[attr-defined]
    return controller
