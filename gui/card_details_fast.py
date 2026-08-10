from __future__ import annotations

from PySide6.QtCore import (
    QAbstractAnimation,
    QEvent,
    QEasingCurve,
    QObject,
    QPoint,
    QRect,
    Qt,
    QParallelAnimationGroup,
    QPropertyAnimation,
    QTimer,
)
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QFrame, QGraphicsOpacityEffect, QMainWindow

from .card_details import CardDetailController


_DRAWER_OPEN_MS = 138
_CONTENT_REVEAL_MS = 104
_DRAWER_CLOSE_MS = 126
_DRAWER_TRAVEL = 44
_CARD_PULSE_PAD = 4
_GEOMETRY_COALESCE_MS = 32


class FastCardDetailController(CardDetailController):
    """Detail drawer motion with no Python per-frame timer.

    The drawer is created at its final size and only its position is animated by
    Qt's C++ animation driver. Detail content is built while covered, then a
    small clipping cover animates away. Card-resize notifications share one
    coalesced geometry pass instead of queueing one callback per card/event.
    """

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)

        self.drawer.setGraphicsEffect(None)
        self.drawer_effect = None  # type: ignore[assignment]

        if self.ghost.graphicsEffect() is None:
            self.ghost_effect = QGraphicsOpacityEffect(self.ghost)
            self.ghost.setGraphicsEffect(self.ghost_effect)
        self.ghost_effect.setOpacity(0.0)

        self.reveal_cover = QFrame(self.scroll.viewport())
        self.reveal_cover.setObjectName("cardDetailRevealCover")
        self.reveal_cover.setStyleSheet(
            "QFrame#cardDetailRevealCover { background-color: rgba(0,0,0,118); border: 0; }"
        )
        self.reveal_cover.hide()
        self._source_rect = QRect()

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
        if self.drawer.isVisible() and self._animation is None:
            self.drawer.setGeometry(self._drawer_rect())

    def _stop_animation(self) -> None:
        animation = self._animation
        self._animation = None
        if animation is not None:
            animation.stop()
            animation.deleteLater()
        self.ghost.hide()
        self.reveal_cover.hide()

    @staticmethod
    def _property_animation(
        target,
        prop: bytes,
        start,
        end,
        duration: int,
        easing: QEasingCurve.Type,
    ) -> QPropertyAnimation:
        animation = QPropertyAnimation(target, prop)
        animation.setStartValue(start)
        animation.setEndValue(end)
        animation.setDuration(duration)
        animation.setEasingCurve(easing)
        return animation

    def _cover_rects(self) -> tuple[QRect, QRect]:
        viewport = self.scroll.viewport().rect()
        full = QRect(0, 0, max(0, viewport.width()), max(0, viewport.height()))
        clear = QRect(0, full.height(), full.width(), 0)
        return full, clear

    def open(self, frame: QFrame) -> None:
        if frame not in self._buttons:
            return
        self._stop_animation()
        self._selected = frame
        self._source_rect = self._card_rect(frame)

        self._clear_body()
        status = self._status_name(frame)
        title, eyebrow = self._card_identity(frame, status)
        self.title.setText(title)
        self.eyebrow.setText(eyebrow)

        target = self._drawer_rect()
        start_pos = target.topLeft() + QPoint(_DRAWER_TRAVEL, 0)
        self.scrim.setGeometry(self.root.rect())
        self.scrim.show()
        self.scrim.raise_()

        self.drawer.setGeometry(target)
        self.drawer.move(start_pos)
        self.drawer.show()
        self.drawer.raise_()
        if self.drawer.layout() is not None:
            self.drawer.layout().activate()

        self.ghost.setGeometry(self._source_rect)
        self.ghost_effect.setOpacity(0.26)
        self.ghost.show()
        self.ghost.raise_()

        group = QParallelAnimationGroup(self)
        group.addAnimation(
            self._property_animation(
                self.drawer,
                b"pos",
                start_pos,
                target.topLeft(),
                _DRAWER_OPEN_MS,
                QEasingCurve.Type.OutCubic,
            )
        )

        ghost_geometry = QPropertyAnimation(self.ghost, b"geometry")
        ghost_geometry.setStartValue(self._source_rect)
        ghost_geometry.setKeyValueAt(
            0.48,
            self._source_rect.adjusted(
                -_CARD_PULSE_PAD,
                -_CARD_PULSE_PAD,
                _CARD_PULSE_PAD,
                _CARD_PULSE_PAD,
            ),
        )
        ghost_geometry.setEndValue(self._source_rect)
        ghost_geometry.setDuration(_DRAWER_OPEN_MS)
        ghost_geometry.setEasingCurve(QEasingCurve.Type.InOutCubic)
        group.addAnimation(ghost_geometry)
        group.addAnimation(
            self._property_animation(
                self.ghost_effect,
                b"opacity",
                0.26,
                0.0,
                _DRAWER_OPEN_MS,
                QEasingCurve.Type.OutQuad,
            )
        )
        group.finished.connect(self._start_content_reveal)
        self._animation = group
        group.start(QAbstractAnimation.DeletionPolicy.KeepWhenStopped)

    def _start_content_reveal(self) -> None:
        old = self._animation
        self._animation = None
        if old is not None:
            old.deleteLater()
        self.ghost.hide()

        frame = self._selected
        if frame is None or not self.drawer.isVisible():
            self._finish_close()
            return

        self._populate(frame)
        if self.drawer.layout() is not None:
            self.drawer.layout().activate()
        self.body_layout.activate()
        self.scroll.verticalScrollBar().setValue(0)

        full, clear = self._cover_rects()
        self.reveal_cover.setGeometry(full)
        self.reveal_cover.show()
        self.reveal_cover.raise_()

        animation = QParallelAnimationGroup(self)
        animation.addAnimation(
            self._property_animation(
                self.reveal_cover,
                b"geometry",
                full,
                clear,
                _CONTENT_REVEAL_MS,
                QEasingCurve.Type.OutCubic,
            )
        )
        animation.finished.connect(self._finish_open)
        self._animation = animation
        animation.start(QAbstractAnimation.DeletionPolicy.KeepWhenStopped)

    def _finish_open(self) -> None:
        animation = self._animation
        self._animation = None
        if animation is not None:
            animation.deleteLater()
        self.reveal_cover.hide()
        self.drawer.setGeometry(self._drawer_rect())
        self.drawer.raise_()
        self.close_button.setFocus(Qt.FocusReason.OtherFocusReason)

    def close(self) -> None:
        if not self.drawer.isVisible() and not self.scrim.isVisible():
            return
        self._stop_animation()

        target = self._drawer_rect()
        start_pos = self.drawer.pos()
        end_pos = target.topLeft() + QPoint(_DRAWER_TRAVEL, 0)
        full, clear = self._cover_rects()
        self.reveal_cover.setGeometry(clear)
        self.reveal_cover.show()
        self.reveal_cover.raise_()
        self.drawer.raise_()

        group = QParallelAnimationGroup(self)
        group.addAnimation(
            self._property_animation(
                self.drawer,
                b"pos",
                start_pos,
                end_pos,
                _DRAWER_CLOSE_MS,
                QEasingCurve.Type.InCubic,
            )
        )
        group.addAnimation(
            self._property_animation(
                self.reveal_cover,
                b"geometry",
                clear,
                full,
                _DRAWER_CLOSE_MS - 18,
                QEasingCurve.Type.InCubic,
            )
        )
        group.finished.connect(self._finish_close)
        self._animation = group
        group.start(QAbstractAnimation.DeletionPolicy.KeepWhenStopped)

    def _finish_close(self) -> None:
        animation = self._animation
        self._animation = None
        if animation is not None:
            animation.deleteLater()
        self.drawer.hide()
        self.ghost.hide()
        self.reveal_cover.hide()
        self.scrim.hide()
        self._selected = None

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        event_type = event.type()
        if watched is self.root:
            if event_type in {QEvent.Type.Resize, QEvent.Type.Show}:
                self._schedule_geometry()
            elif event_type == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
                if event.key() == Qt.Key.Key_Escape and self.drawer.isVisible():
                    self.close()
                    return True
        elif isinstance(watched, QFrame) and watched in self._buttons:
            if event_type in {QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.LayoutRequest}:
                self._schedule_geometry()
        return False

    def _cleanup(self) -> None:
        self._geometry_timer.stop()
        super()._cleanup()


def install_card_details(window: QMainWindow) -> FastCardDetailController:
    controller = FastCardDetailController(window)
    window._card_details = controller  # type: ignore[attr-defined]
    window.destroyed.connect(controller._cleanup)
    return controller
