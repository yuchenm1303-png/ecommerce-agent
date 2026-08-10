from __future__ import annotations

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QObject,
    QPoint,
    QParallelAnimationGroup,
    QPropertyAnimation,
    Qt,
)
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QLabel,
    QMainWindow,
)

from .card_details_fast import FastCardDetailController


_OPEN_MS = 220
_OPEN_FADE_MS = 205
_CLOSE_MS = 170
_CLOSE_FADE_MS = 150
_OPEN_RISE_PX = 12
_CLOSE_DROP_PX = 9

_STATE_IDLE = "idle"
_STATE_OPENING = "opening"
_STATE_OPEN = "open"
_STATE_CLOSING = "closing"


class StaticModalInteractionController(QObject):
    """Animate the real QWidget drawer without snapshots or native overlays.

    The backdrop and scrim are static. Only the real drawer's opacity and position
    animate, so every child control (text, tables, buttons) participates in the
    same fade. Width and height never animate, which keeps layout/reflow out of
    the hot path. No additional QQuickWindow or native HWND is created.
    """

    def __init__(self, window: QMainWindow, details: FastCardDetailController) -> None:
        super().__init__(window)
        self.window = window
        self.details = details
        self.root = window.centralWidget()
        if self.root is None:
            raise RuntimeError("static modal interaction requires a central widget")

        self._passive_labels: dict[QLabel, bool] = {}
        self._state = _STATE_IDLE
        self._animation: QParallelAnimationGroup | None = None
        self._geometry_sync_pending = False

        self._original_show_prepared_modal = self.details._show_prepared_modal  # noqa: SLF001
        self._original_close = self.details.close
        self._original_schedule_geometry = self.details._schedule_geometry  # noqa: SLF001

        # Fade the real drawer subtree. Unlike the previous panel snapshot, this
        # makes text, tables and buttons share one continuous opacity curve.
        self._drawer_effect = QGraphicsOpacityEffect(self.details.drawer)
        self._drawer_effect.setOpacity(1.0)
        self.details.drawer.setGraphicsEffect(self._drawer_effect)
        self.details.drawer_effect = self._drawer_effect  # type: ignore[assignment]

        self.details._show_prepared_modal = self._show_with_animation  # type: ignore[method-assign]  # noqa: SLF001
        self.details.close = self.request_close  # type: ignore[method-assign]
        self.details._schedule_geometry = self._schedule_geometry_guarded  # type: ignore[method-assign]  # noqa: SLF001
        self._rewire_close_inputs()
        self._install_card_surfaces()
        window.destroyed.connect(self.cleanup)

    @staticmethod
    def _label_is_passive(label: QLabel) -> bool:
        flags = label.textInteractionFlags()
        interactive = (
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        return not bool(flags & interactive)

    def _install_card_surfaces(self) -> None:
        for card in self.details._expandable_cards:  # noqa: SLF001
            if not isinstance(card, QFrame):
                continue
            card.setCursor(Qt.CursorShape.PointingHandCursor)
            for label in card.findChildren(QLabel):
                if not self._label_is_passive(label):
                    continue
                previous = label.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                self._passive_labels[label] = previous
                label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def _rewire_close_inputs(self) -> None:
        try:
            self.details.close_button.clicked.disconnect(self._original_close)
        except (RuntimeError, TypeError):
            pass
        self.details.close_button.clicked.connect(self.request_close)

        try:
            self.details.scrim.clicked.disconnect(self._original_close)
        except (RuntimeError, TypeError):
            pass
        self.details.scrim.clicked.connect(self.request_close)

    def _schedule_geometry_guarded(self, *_args: object) -> None:
        if self._state in {_STATE_OPENING, _STATE_CLOSING}:
            self._geometry_sync_pending = True
            return
        self._original_schedule_geometry()

    def _set_motion_active(self, active: bool) -> None:
        self.details._presentation_animating = bool(active)  # noqa: SLF001
        timer = getattr(self.details, "_geometry_timer", None)
        if active:
            if timer is not None:
                try:
                    timer.stop()
                except RuntimeError:
                    pass
            return

        if self._geometry_sync_pending:
            self._geometry_sync_pending = False
            self._original_schedule_geometry()

    @staticmethod
    def _property_animation(
        target: QObject,
        prop: bytes,
        start: object,
        end: object,
        duration_ms: int,
        easing: QEasingCurve.Type,
    ) -> QPropertyAnimation:
        animation = QPropertyAnimation(target, prop)
        animation.setStartValue(start)
        animation.setEndValue(end)
        animation.setDuration(max(1, int(duration_ms)))
        animation.setEasingCurve(easing)
        return animation

    def _stop_animation(self) -> None:
        animation = self._animation
        self._animation = None
        if animation is not None:
            animation.stop()
            animation.deleteLater()

    def _prepare_open_state(self, *, ratio: tuple[float, float]) -> QPoint:
        self.details._modal_ratio = ratio  # noqa: SLF001
        backdrop = self.details._capture_backdrop()  # noqa: SLF001
        target = self.details._drawer_rect()  # noqa: SLF001
        start_pos = target.topLeft() + QPoint(0, _OPEN_RISE_PX)

        updates_were_enabled = self.root.updatesEnabled()
        if updates_were_enabled:
            self.root.setUpdatesEnabled(False)

        try:
            self._drawer_effect.setOpacity(0.0)
            self.details.backdrop.setPixmap(backdrop)
            self.details.backdrop.setGeometry(self.root.rect())
            self.details.scrim.setGeometry(self.root.rect())
            self.details.drawer.setGeometry(target)
            self.details.body_layout.activate()
            if self.details.drawer.layout() is not None:
                self.details.drawer.layout().activate()
            self.details.scroll.verticalScrollBar().setValue(0)
            self.details.ghost.hide()

            # Build the first frame atomically: the real drawer exists already,
            # but starts fully transparent and slightly lower than its final pos.
            self.details.drawer.move(start_pos)
            self.details.backdrop.show()
            self.details.backdrop.raise_()
            self.details.scrim.show()
            self.details.scrim.raise_()
            self.details.drawer.show()
            self.details.drawer.raise_()
        finally:
            if updates_were_enabled:
                self.root.setUpdatesEnabled(True)

        # One synchronous first paint prevents a half-constructed panel frame.
        self.root.repaint()
        return target.topLeft()

    def _show_with_animation(self, *, ratio: tuple[float, float]) -> None:
        if self._state != _STATE_IDLE or self.details.drawer.isVisible():
            return

        self._state = _STATE_OPENING
        self._set_motion_active(True)
        try:
            target_pos = self._prepare_open_state(ratio=ratio)
            group = QParallelAnimationGroup(self)
            group.addAnimation(
                self._property_animation(
                    self.details.drawer,
                    b"pos",
                    self.details.drawer.pos(),
                    target_pos,
                    _OPEN_MS,
                    QEasingCurve.Type.OutQuart,
                )
            )
            group.addAnimation(
                self._property_animation(
                    self._drawer_effect,
                    b"opacity",
                    self._drawer_effect.opacity(),
                    1.0,
                    _OPEN_FADE_MS,
                    QEasingCurve.Type.OutCubic,
                )
            )
            group.finished.connect(self._finish_open)
            self._animation = group
            group.start(QAbstractAnimation.DeletionPolicy.KeepWhenStopped)
        except Exception:
            self._fallback_open(ratio)

    def _finish_open(self) -> None:
        if self._state != _STATE_OPENING:
            return
        animation = self._animation
        self._animation = None
        if animation is not None:
            animation.deleteLater()

        target = self.details._drawer_rect()  # noqa: SLF001
        self.details.drawer.move(target.topLeft())
        self._drawer_effect.setOpacity(1.0)
        self._state = _STATE_OPEN
        self._set_motion_active(False)
        self.details.close_button.setFocus(Qt.FocusReason.OtherFocusReason)
        self.details._schedule_geometry()  # noqa: SLF001

    def request_close(self, *_args: object) -> None:
        if self._state == _STATE_CLOSING:
            return

        if self.details.drawer.isHidden() and self.details.scrim.isHidden():
            self._stop_animation()
            self._state = _STATE_IDLE
            self._set_motion_active(False)
            return

        if self._state not in {_STATE_OPENING, _STATE_OPEN, _STATE_IDLE}:
            return

        # If close arrives while opening, reverse smoothly from the exact current
        # pos/opacity instead of snapping to either endpoint first.
        self._stop_animation()
        self._state = _STATE_CLOSING
        self._set_motion_active(True)

        try:
            target = self.details._drawer_rect()  # noqa: SLF001
            end_pos = target.topLeft() + QPoint(0, _CLOSE_DROP_PX)
            group = QParallelAnimationGroup(self)
            group.addAnimation(
                self._property_animation(
                    self.details.drawer,
                    b"pos",
                    self.details.drawer.pos(),
                    end_pos,
                    _CLOSE_MS,
                    QEasingCurve.Type.InCubic,
                )
            )
            group.addAnimation(
                self._property_animation(
                    self._drawer_effect,
                    b"opacity",
                    self._drawer_effect.opacity(),
                    0.0,
                    _CLOSE_FADE_MS,
                    QEasingCurve.Type.InCubic,
                )
            )
            group.finished.connect(self._finish_close)
            self._animation = group
            group.start(QAbstractAnimation.DeletionPolicy.KeepWhenStopped)
        except Exception:
            self._fallback_close()

    def _finish_close(self) -> None:
        if self._state != _STATE_CLOSING:
            return
        animation = self._animation
        self._animation = None
        if animation is not None:
            animation.deleteLater()

        self._state = _STATE_IDLE
        self._set_motion_active(False)
        self._original_close()
        self._drawer_effect.setOpacity(1.0)

    def _fallback_open(self, ratio: tuple[float, float]) -> None:
        self._stop_animation()
        self._state = _STATE_IDLE
        self._set_motion_active(False)
        self._drawer_effect.setOpacity(1.0)
        try:
            self._original_close()
        except RuntimeError:
            pass
        self._original_show_prepared_modal(ratio=ratio)
        self._drawer_effect.setOpacity(1.0)
        self._state = _STATE_OPEN

    def _fallback_close(self) -> None:
        self._stop_animation()
        self._state = _STATE_IDLE
        self._set_motion_active(False)
        self._original_close()
        self._drawer_effect.setOpacity(1.0)

    def cleanup(self) -> None:
        self._stop_animation()
        self._state = _STATE_IDLE
        self._set_motion_active(False)

        try:
            self.details.close_button.clicked.disconnect(self.request_close)
        except (RuntimeError, TypeError):
            pass
        try:
            self.details.scrim.clicked.disconnect(self.request_close)
        except (RuntimeError, TypeError):
            pass
        try:
            self.details.close_button.clicked.connect(self._original_close)
            self.details.scrim.clicked.connect(self._original_close)
        except RuntimeError:
            pass

        try:
            self.details._show_prepared_modal = self._original_show_prepared_modal  # type: ignore[method-assign]  # noqa: SLF001
            self.details.close = self._original_close  # type: ignore[method-assign]
            self.details._schedule_geometry = self._original_schedule_geometry  # type: ignore[method-assign]  # noqa: SLF001
        except RuntimeError:
            pass

        try:
            if self.details.drawer.graphicsEffect() is self._drawer_effect:
                self.details.drawer.setGraphicsEffect(None)
            self.details.drawer_effect = None  # type: ignore[assignment]
        except RuntimeError:
            pass

        for label, previous in tuple(self._passive_labels.items()):
            try:
                label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, previous)
            except RuntimeError:
                pass
        self._passive_labels.clear()


def install_static_modal_interaction(
    window: QMainWindow,
    details: FastCardDetailController,
) -> StaticModalInteractionController:
    controller = StaticModalInteractionController(window, details)
    window._static_modal_interaction = controller  # type: ignore[attr-defined]
    return controller
