from __future__ import annotations

import time

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPointF, Qt, QTimer
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QLabel,
    QMainWindow,
    QWidget,
)

from .card_details_fast import FastCardDetailController


_OPEN_MS = 260
_CLOSE_MS = 210
_DRAWER_TRAVEL_PX = 14

_STATE_IDLE = "idle"
_STATE_OPENING = "opening"
_STATE_OPEN = "open"
_STATE_CLOSING = "closing"


def _open_ease() -> QEasingCurve:
    return QEasingCurve(QEasingCurve.Type.OutCubic)


def _close_ease() -> QEasingCurve:
    return QEasingCurve(QEasingCurve.Type.InOutCubic)


class StaticModalInteractionController(QObject):
    """Stable live modal animation with one geometry owner.

    The old implementation cross-faded two full-window raster snapshots. That made
    the transition depend on the exact instant a hovered/scaled card was captured,
    and a root resize had to snap the animation to an endpoint. The result was the
    visible button/card trail and occasional edge bounce reported by users.

    This controller never rasterizes or animates the workspace. The live workspace
    remains the only underlay. Only the absolute-positioned modal layers animate:
    a blurred backdrop fades in, the scrim fades in, and the drawer travels a small
    fixed distance while fading. Drawer geometry is recomputed from the current root
    rect every frame, so resizing/maximizing cannot create a stale edge target.
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
        self._progress = 0.0
        self._underlay_suspended = False
        self._underlay_settled = False
        self._activity_timer_was_active = False

        self._motion_started_s = 0.0
        self._motion_duration_s = 0.001
        self._motion_from = 0.0
        self._motion_to = 0.0
        self._motion_easing = _open_ease()

        self._original_show_prepared_modal = self.details._show_prepared_modal  # noqa: SLF001
        self._original_close = self.details.close

        # Opacity effects are attached only to the three absolute modal layers.
        # They never participate in the workspace layout or card geometry.
        self._drawer_opacity = QGraphicsOpacityEffect(self.details.drawer)
        self._drawer_opacity.setOpacity(0.0)
        self.details.drawer.setGraphicsEffect(self._drawer_opacity)

        self._scrim_opacity = QGraphicsOpacityEffect(self.details.scrim)
        self._scrim_opacity.setOpacity(0.0)
        self.details.scrim.setGraphicsEffect(self._scrim_opacity)

        self._backdrop_opacity = QGraphicsOpacityEffect(self.details.backdrop)
        self._backdrop_opacity.setOpacity(0.0)
        self.details.backdrop.setGraphicsEffect(self._backdrop_opacity)

        self.details.ghost.hide()
        self.details.backdrop.hide()
        self.details.scrim.hide()
        self.details.drawer.hide()

        self._motion_timer = QTimer(self)
        self._motion_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._motion_timer.timeout.connect(self._advance_motion)

        self.details._show_prepared_modal = self._show_with_animation  # type: ignore[method-assign]  # noqa: SLF001
        self.details.close = self.request_close  # type: ignore[method-assign]
        self._rewire_close_inputs()
        self._install_card_surfaces()
        self.root.installEventFilter(self)
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

    def _card_fx(self):  # noqa: ANN201
        return getattr(self.window, "_nekro_card_fx", None)

    def _suspend_underlay(self) -> None:
        if self._underlay_suspended:
            return
        self._underlay_suspended = True
        self._underlay_settled = False

        card_fx = self._card_fx()
        suspend_cards = getattr(card_fx, "suspend_for_modal", None)
        if callable(suspend_cards):
            try:
                # Preserve the exact hover/press pixels the user clicked. The
                # modal fade starts over that same live state, so there is no
                # pre-animation snap back to scale 1.0.
                suspend_cards(preserve_visual=True)
            except TypeError:
                suspend_cards()
            except RuntimeError:
                pass

        clock = getattr(self.window, "_presentation_clock", None)
        suspend_clock = getattr(clock, "suspend", None)
        if callable(suspend_clock):
            suspend_clock("modal")

        activity = getattr(self.window, "_activity_presence_controller", None)
        activity_widget = getattr(activity, "widget", None)
        activity_timer = getattr(activity_widget, "_timer", None)
        try:
            self._activity_timer_was_active = bool(
                activity_timer is not None and activity_timer.isActive()
            )
        except RuntimeError:
            self._activity_timer_was_active = False
        if self._activity_timer_was_active:
            try:
                activity_timer.stop()
            except RuntimeError:
                pass

    def _settle_hidden_underlay(self) -> None:
        if not self._underlay_suspended or self._underlay_settled:
            return
        card_fx = self._card_fx()
        settle = getattr(card_fx, "settle_suspended_for_modal", None)
        if callable(settle):
            try:
                settle()
                self._underlay_settled = True
                return
            except RuntimeError:
                pass
        self._underlay_settled = True

    def _resume_underlay(self) -> None:
        if not self._underlay_suspended:
            return
        self._underlay_suspended = False

        card_fx = self._card_fx()
        resume_cards = getattr(card_fx, "resume_from_modal", None)
        if callable(resume_cards):
            try:
                resume_cards()
            except RuntimeError:
                pass

        clock = getattr(self.window, "_presentation_clock", None)
        resume_clock = getattr(clock, "resume", None)
        if callable(resume_clock):
            resume_clock("modal")

        activity = getattr(self.window, "_activity_presence_controller", None)
        activity_widget = getattr(activity, "widget", None)
        activity_timer = getattr(activity_widget, "_timer", None)
        if self._activity_timer_was_active and activity_timer is not None:
            try:
                if bool(getattr(activity_widget, "active", False)) and not activity_timer.isActive():
                    activity_widget._last_frame_s = time.perf_counter()  # noqa: SLF001
                    activity_timer.start()
            except RuntimeError:
                pass
        self._activity_timer_was_active = False
        self._underlay_settled = False

        if self.background is not None:
            schedule_geometry = getattr(self.background, "schedule_mask_update", None)
            if callable(schedule_geometry):
                try:
                    schedule_geometry()
                except RuntimeError:
                    pass

    def _sync_modal_geometry(self) -> None:
        self.details.backdrop.setGeometry(self.root.rect())
        self.details.scrim.setGeometry(self.root.rect())
        target = self.details._drawer_rect()  # noqa: SLF001
        offset = round(_DRAWER_TRAVEL_PX * (1.0 - self._progress))
        moved = target.translated(0, offset)
        self.details.drawer.setGeometry(moved)

    def _prepare_live_modal(self, *, ratio: tuple[float, float], blurred) -> None:  # noqa: ANN001
        self.details._modal_ratio = ratio  # noqa: SLF001
        self.details.backdrop.setPixmap(blurred)
        self.details.scroll.verticalScrollBar().setValue(0)
        self.details.ghost.hide()

        # Modal-only layouts may be committed here; the workspace itself is never
        # activated or resized by the transition.
        self.details.body_layout.activate()
        drawer_layout = self.details.drawer.layout()
        if drawer_layout is not None:
            drawer_layout.activate()

        self._progress = 0.0
        self._sync_modal_geometry()
        self._drawer_opacity.setOpacity(0.0)
        self._scrim_opacity.setOpacity(0.0)
        self._backdrop_opacity.setOpacity(0.0)

        self.details.backdrop.show()
        self.details.backdrop.raise_()
        self.details.scrim.show()
        self.details.scrim.raise_()
        self.details.drawer.show()
        self.details.drawer.raise_()
        self.details.close_button.setEnabled(False)

    def _frame_interval_ms(self) -> int:
        refresh_hz = 60.0
        screen = self.window.screen()
        if screen is not None:
            try:
                candidate = float(screen.refreshRate())
                if 30.0 <= candidate <= 500.0:
                    refresh_hz = candidate
            except (RuntimeError, TypeError, ValueError):
                pass
        target_hz = max(60.0, min(144.0, refresh_hz))
        return max(5, int(round(1000.0 / target_hz)))

    def _stop_animation(self) -> None:
        self._motion_timer.stop()

    def _start_motion(self, *, end: float, duration_ms: int, easing: QEasingCurve) -> None:
        self._motion_timer.stop()
        self._motion_from = float(self._progress)
        self._motion_to = max(0.0, min(1.0, float(end)))
        distance = abs(self._motion_to - self._motion_from)
        self._motion_duration_s = max(0.001, (float(duration_ms) * max(0.18, distance)) / 1000.0)
        self._motion_easing = easing
        self._motion_started_s = time.perf_counter()
        self._motion_timer.setInterval(self._frame_interval_ms())
        self._motion_timer.start()

    def _apply_visual_state(self, progress: float) -> None:
        progress = max(0.0, min(1.0, float(progress)))
        self._progress = progress
        self._sync_modal_geometry()

        # The drawer leads the transition slightly; the blur/scrim catch up behind
        # it. All curves are monotonic, so there is no overshoot or edge rebound.
        drawer_alpha = progress
        backdrop_alpha = min(1.0, progress * 1.10)
        scrim_alpha = min(1.0, progress * 1.18)
        self._drawer_opacity.setOpacity(drawer_alpha)
        self._backdrop_opacity.setOpacity(backdrop_alpha)
        self._scrim_opacity.setOpacity(scrim_alpha)

    def _advance_motion(self) -> None:
        elapsed_s = max(0.0, time.perf_counter() - self._motion_started_s)
        linear = min(1.0, elapsed_s / self._motion_duration_s)
        eased = float(self._motion_easing.valueForProgress(linear))
        value = self._motion_from + (self._motion_to - self._motion_from) * eased
        self._apply_visual_state(value)
        if linear >= 1.0:
            self._motion_timer.stop()
            self._apply_visual_state(self._motion_to)
            self._finish_motion()

    def _show_with_animation(self, *, ratio: tuple[float, float]) -> None:
        if self._state != _STATE_IDLE or not self.details.drawer.isHidden():
            return

        # Capture only the static blur source. It is never used as an animated
        # workspace frame, so card/button geometry cannot leave raster trails.
        try:
            source = self.details._capture_source()  # noqa: SLF001
            blurred = self.details._blur_pixmap(source)  # noqa: SLF001
            if source.isNull() or blurred.isNull():
                raise RuntimeError("modal backdrop capture failed")
        except Exception:
            self._fallback_open(ratio)
            return

        self._suspend_underlay()
        self._state = _STATE_OPENING
        try:
            self._prepare_live_modal(ratio=ratio, blurred=blurred)
            self._start_motion(end=1.0, duration_ms=_OPEN_MS, easing=_open_ease())
        except Exception:
            self._fallback_open(ratio)

    def _finish_motion(self) -> None:
        if self._state == _STATE_OPENING:
            self._finish_open()
        elif self._state == _STATE_CLOSING:
            self._finish_close()

    def _finish_open(self) -> None:
        if self._state != _STATE_OPENING:
            return
        self._apply_visual_state(1.0)
        self._state = _STATE_OPEN
        self.details.close_button.setEnabled(True)
        self.details.close_button.setFocus(Qt.FocusReason.OtherFocusReason)

        # Once the blur is fully opaque, normalize the hidden hover state. The
        # eventual close therefore reveals an already-stable workspace instead of
        # snapping the clicked card back to rest on the final frame.
        self._settle_hidden_underlay()

    def request_close(self, *_args: object) -> None:
        if self._state == _STATE_IDLE:
            if not self.details.drawer.isHidden():
                self._fallback_close()
            return
        if self._state == _STATE_CLOSING:
            return

        # A very early click is allowed to reverse smoothly from its current
        # progress. If the modal has already become visually dominant, normalize
        # the hidden underlay before it is revealed again.
        if self._progress >= 0.72:
            self._settle_hidden_underlay()
        self.details.close_button.setEnabled(False)
        self._state = _STATE_CLOSING
        self._stop_animation()
        self._start_motion(end=0.0, duration_ms=_CLOSE_MS, easing=_close_ease())

    def _finish_close(self) -> None:
        if self._state != _STATE_CLOSING:
            return

        # If close interrupted the first part of opening, briefly normalize the
        # underlay while the modal layers still own the last transition turn.
        self._settle_hidden_underlay()
        self._apply_visual_state(0.0)
        self.details.drawer.hide()
        self.details.scrim.hide()
        self.details.backdrop.hide()
        self.details.backdrop.clear()
        self.details.ghost.hide()
        self.details.close_button.setEnabled(True)
        self.details._selected = None  # noqa: SLF001
        self.details._modal_ratio = (0.80, 0.80)  # noqa: SLF001
        self._state = _STATE_IDLE
        self._resume_underlay()
        self.root.update()

    def _fallback_open(self, ratio: tuple[float, float]) -> None:
        self._stop_animation()
        try:
            self._original_close()
        except RuntimeError:
            pass
        try:
            self._original_show_prepared_modal(ratio=ratio)
            self._state = _STATE_OPEN
            self._progress = 1.0
        except Exception:
            self._state = _STATE_IDLE
            self._progress = 0.0
            self._resume_underlay()

    def _fallback_close(self) -> None:
        self._stop_animation()
        try:
            self._original_close()
        finally:
            self._state = _STATE_IDLE
            self._progress = 0.0
            self._resume_underlay()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self.root:
            event_type = event.type()
            if event_type == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
                if event.key() == Qt.Key.Key_Escape and self._state in {
                    _STATE_OPENING,
                    _STATE_OPEN,
                }:
                    self.request_close()
                    return True
            elif event_type == QEvent.Type.Resize and self._state in {
                _STATE_OPENING,
                _STATE_OPEN,
                _STATE_CLOSING,
            }:
                # Retarget from the same normalized progress instead of snapping to
                # an animation endpoint when the window hits an edge/maximize zone.
                QTimer.singleShot(0, lambda: self._apply_visual_state(self._progress))
        return False

    def cleanup(self) -> None:
        self._stop_animation()
        try:
            self._original_close()
        except RuntimeError:
            pass
        self._state = _STATE_IDLE
        self._progress = 0.0
        self._resume_underlay()

        try:
            self.root.removeEventFilter(self)
        except RuntimeError:
            pass
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
    existing = getattr(window, "_static_modal_interaction", None)
    if isinstance(existing, StaticModalInteractionController):
        return existing
    controller = StaticModalInteractionController(window, details)
    window._static_modal_interaction = controller  # type: ignore[attr-defined]
    return controller


__all__ = ["StaticModalInteractionController", "install_static_modal_interaction"]
