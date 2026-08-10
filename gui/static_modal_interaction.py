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
    """Animate the real QWidget drawer with the smallest practical hot path.

    The backdrop and scrim stay static. The real drawer keeps the exact existing
    visual motion: position plus subtree opacity. Animation objects are allocated
    once and reused, expensive background effects are suspended while obscured,
    and the opacity effect is disabled outside transitions so normal modal
    interaction paints directly. No snapshot, layout animation, extra native HWND
    or second QQuickWindow participates in the motion path.
    """

    def __init__(self, window: QMainWindow, details: FastCardDetailController) -> None:
        super().__init__(window)
        self.window = window
        self.details = details
        self.root = window.centralWidget()
        if self.root is None:
            raise RuntimeError("static modal interaction requires a central widget")

        visual = getattr(window, "_visual_style", None)
        self.background = getattr(visual, "background", None)

        self._passive_labels: dict[QLabel, bool] = {}
        self._state = _STATE_IDLE
        self._geometry_sync_pending = False
        self._underlay_suspended = False
        self._pointer_timer_was_active = False
        self._effects_timer_was_active = False

        self._original_show_prepared_modal = self.details._show_prepared_modal  # noqa: SLF001
        self._original_close = self.details.close
        self._original_schedule_geometry = self.details._schedule_geometry  # noqa: SLF001

        # The real drawer owns the fade, so text/tables/buttons stay on exactly
        # the same opacity curve. Keep the effect disabled when opacity is 1.0;
        # that removes an unnecessary off-screen composition pass while the modal
        # is simply sitting open.
        self._drawer_effect = QGraphicsOpacityEffect(self.details.drawer)
        self._drawer_effect.setOpacity(1.0)
        self.details.drawer.setGraphicsEffect(self._drawer_effect)
        self._drawer_effect.setEnabled(False)
        self.details.drawer_effect = self._drawer_effect  # type: ignore[assignment]

        # Allocate the animation graph once. Reusing the same C++ animation
        # objects avoids per-open QObject allocation and deferred-delete churn.
        self._motion_group = QParallelAnimationGroup(self)
        self._position_animation = QPropertyAnimation(self.details.drawer, b"pos")
        self._opacity_animation = QPropertyAnimation(self._drawer_effect, b"opacity")
        self._motion_group.addAnimation(self._position_animation)
        self._motion_group.addAnimation(self._opacity_animation)
        self._motion_group.finished.connect(self._finish_motion)

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

    def _suspend_underlay(self) -> None:
        if self._underlay_suspended:
            return
        self._underlay_suspended = True

        card_fx = getattr(self.window, "_nekro_card_fx", None)
        suspend_cards = getattr(card_fx, "suspend_for_modal", None)
        if callable(suspend_cards):
            try:
                suspend_cards()
            except RuntimeError:
                pass

        # The sakura/cursor overlay normally wakes the GUI thread every 16 ms.
        # It is completely covered by the modal backdrop, so freeze its exact
        # current pixels for the modal lifetime instead of wasting paint cycles.
        effects = getattr(self.window, "_nekro_effects", None)
        effects_timer = getattr(effects, "timer", None)
        try:
            self._effects_timer_was_active = bool(
                effects_timer is not None and effects_timer.isActive()
            )
        except RuntimeError:
            self._effects_timer_was_active = False
        if self._effects_timer_was_active:
            try:
                effects_timer.stop()
            except RuntimeError:
                pass

        timer = getattr(self.background, "_pointer_timer", None)
        try:
            self._pointer_timer_was_active = bool(timer is not None and timer.isActive())
        except RuntimeError:
            self._pointer_timer_was_active = False
        if self._pointer_timer_was_active:
            try:
                timer.stop()
            except RuntimeError:
                pass

        quick = getattr(self.background, "quick_window", None)
        if quick is not None:
            try:
                quick.setProperty("animationRunning", False)
            except RuntimeError:
                pass

    def _resume_underlay(self) -> None:
        if not self._underlay_suspended:
            return
        self._underlay_suspended = False

        card_fx = getattr(self.window, "_nekro_card_fx", None)
        resume_cards = getattr(card_fx, "resume_from_modal", None)
        if callable(resume_cards):
            try:
                resume_cards()
            except RuntimeError:
                pass

        if self.background is not None:
            try:
                self.background._last_pointer_norm = None  # noqa: SLF001
            except (AttributeError, RuntimeError):
                pass

        timer = getattr(self.background, "_pointer_timer", None)
        if self._pointer_timer_was_active and timer is not None:
            try:
                timer.start()
            except RuntimeError:
                pass
        self._pointer_timer_was_active = False

        effects = getattr(self.window, "_nekro_effects", None)
        effects_timer = getattr(effects, "timer", None)
        if self._effects_timer_was_active and effects_timer is not None:
            try:
                if not effects_timer.isActive():
                    effects_timer.start()
            except RuntimeError:
                pass
        self._effects_timer_was_active = False

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
    def _configure_property_animation(
        animation: QPropertyAnimation,
        start: object,
        end: object,
        duration_ms: int,
        easing: QEasingCurve.Type,
    ) -> None:
        animation.setStartValue(start)
        animation.setEndValue(end)
        animation.setDuration(max(1, int(duration_ms)))
        animation.setEasingCurve(easing)

    def _configure_motion(
        self,
        *,
        end_pos: QPoint,
        end_opacity: float,
        motion_ms: int,
        fade_ms: int,
        motion_easing: QEasingCurve.Type,
        fade_easing: QEasingCurve.Type,
    ) -> None:
        self._configure_property_animation(
            self._position_animation,
            self.details.drawer.pos(),
            end_pos,
            motion_ms,
            motion_easing,
        )
        self._configure_property_animation(
            self._opacity_animation,
            self._drawer_effect.opacity(),
            float(end_opacity),
            fade_ms,
            fade_easing,
        )

    def _stop_animation(self) -> None:
        if self._motion_group.state() != QAbstractAnimation.State.Stopped:
            self._motion_group.stop()

    def _finish_motion(self) -> None:
        if self._state == _STATE_OPENING:
            self._finish_open()
        elif self._state == _STATE_CLOSING:
            self._finish_close()

    def _prepare_open_state(self, *, ratio: tuple[float, float]) -> QPoint:
        self.details._modal_ratio = ratio  # noqa: SLF001
        backdrop = self.details._capture_backdrop()  # noqa: SLF001
        target = self.details._drawer_rect()  # noqa: SLF001
        start_pos = target.topLeft() + QPoint(0, _OPEN_RISE_PX)

        updates_were_enabled = self.root.updatesEnabled()
        if updates_were_enabled:
            self.root.setUpdatesEnabled(False)

        try:
            self._drawer_effect.setEnabled(True)
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

        # Pay the first composition cost before the unified animation clock starts.
        self.root.repaint()
        return target.topLeft()

    def _show_with_animation(self, *, ratio: tuple[float, float]) -> None:
        if self._state != _STATE_IDLE or self.details.drawer.isVisible():
            return

        self._suspend_underlay()
        self._state = _STATE_OPENING
        self._set_motion_active(True)
        try:
            target_pos = self._prepare_open_state(ratio=ratio)
            self._configure_motion(
                end_pos=target_pos,
                end_opacity=1.0,
                motion_ms=_OPEN_MS,
                fade_ms=_OPEN_FADE_MS,
                motion_easing=QEasingCurve.Type.OutQuart,
                fade_easing=QEasingCurve.Type.OutCubic,
            )
            self._motion_group.start()
        except Exception:
            self._fallback_open(ratio)

    def _finish_open(self) -> None:
        if self._state != _STATE_OPENING:
            return

        target = self.details._drawer_rect()  # noqa: SLF001
        self.details.drawer.move(target.topLeft())
        self._drawer_effect.setOpacity(1.0)
        # Opacity 1.0 and a disabled effect are visually identical, but disabling
        # it removes the full drawer off-screen composition pass while users read,
        # scroll and interact with the modal.
        self._drawer_effect.setEnabled(False)
        self.details.drawer.repaint()

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
            self._drawer_effect.setOpacity(1.0)
            self._drawer_effect.setEnabled(False)
            self._resume_underlay()
            return

        if self._state not in {_STATE_OPENING, _STATE_OPEN, _STATE_IDLE}:
            return

        # If the modal is already fully open, enable and warm the opacity effect
        # once before motion begins. That moves its source-raster cost out of the
        # first animated frame. During an interrupted opening it is already live.
        if self._state in {_STATE_OPEN, _STATE_IDLE}:
            self._drawer_effect.setEnabled(True)
            self._drawer_effect.setOpacity(1.0)
            self.details.drawer.repaint()

        # If close arrives while opening, reverse smoothly from the exact current
        # pos/opacity instead of snapping to either endpoint first.
        self._stop_animation()
        self._state = _STATE_CLOSING
        self._set_motion_active(True)

        try:
            target = self.details._drawer_rect()  # noqa: SLF001
            end_pos = target.topLeft() + QPoint(0, _CLOSE_DROP_PX)
            self._configure_motion(
                end_pos=end_pos,
                end_opacity=0.0,
                motion_ms=_CLOSE_MS,
                fade_ms=_CLOSE_FADE_MS,
                motion_easing=QEasingCurve.Type.InCubic,
                fade_easing=QEasingCurve.Type.InCubic,
            )
            self._motion_group.start()
        except Exception:
            self._fallback_close()

    def _finish_close(self) -> None:
        if self._state != _STATE_CLOSING:
            return

        self._state = _STATE_IDLE
        self._set_motion_active(False)
        self._original_close()
        self._drawer_effect.setOpacity(1.0)
        self._drawer_effect.setEnabled(False)
        self._resume_underlay()

    def _fallback_open(self, ratio: tuple[float, float]) -> None:
        self._stop_animation()
        self._state = _STATE_IDLE
        self._set_motion_active(False)
        self._drawer_effect.setOpacity(1.0)
        self._drawer_effect.setEnabled(False)
        try:
            self._original_close()
        except RuntimeError:
            pass
        try:
            self._original_show_prepared_modal(ratio=ratio)
            self._drawer_effect.setOpacity(1.0)
            self._drawer_effect.setEnabled(False)
            self._state = _STATE_OPEN
        except Exception:
            self._state = _STATE_IDLE
            self._resume_underlay()

    def _fallback_close(self) -> None:
        self._stop_animation()
        self._state = _STATE_IDLE
        self._set_motion_active(False)
        self._original_close()
        self._drawer_effect.setOpacity(1.0)
        self._drawer_effect.setEnabled(False)
        self._resume_underlay()

    def cleanup(self) -> None:
        self._stop_animation()
        self._state = _STATE_IDLE
        self._set_motion_active(False)
        self._resume_underlay()

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
            self._drawer_effect.setEnabled(False)
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
