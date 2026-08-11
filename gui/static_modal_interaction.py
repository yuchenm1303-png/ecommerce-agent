from __future__ import annotations

import time

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPoint, QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QKeyEvent, QMouseEvent, QPainter, QPixmap, QRegion
from PySide6.QtWidgets import QFrame, QLabel, QMainWindow, QWidget

from .card_details_fast import FastCardDetailController


# Match the reference webpage's visible timing. Complex QWidget content never
# animates live; one transition surface owns presentation during motion.
_OPEN_MS = 500
_CLOSE_MS = 300

_STATE_IDLE = "idle"
_STATE_OPENING = "opening"
_STATE_OPEN = "open"
_STATE_CLOSING = "closing"


def _css_ease() -> QEasingCurve:
    """CSS `ease`: cubic-bezier(.25, .1, .25, 1)."""

    curve = QEasingCurve(QEasingCurve.Type.BezierSpline)
    curve.addCubicBezierSegment(
        QPointF(0.25, 0.10),
        QPointF(0.25, 1.00),
        QPointF(1.00, 1.00),
    )
    return curve


def _css_ease_in_out() -> QEasingCurve:
    """CSS `ease-in-out`: cubic-bezier(.42, 0, .58, 1)."""

    curve = QEasingCurve(QEasingCurve.Type.BezierSpline)
    curve.addCubicBezierSegment(
        QPointF(0.42, 0.00),
        QPointF(0.58, 1.00),
        QPointF(1.00, 1.00),
    )
    return curve


def _empty_surface_frame(widget: QWidget) -> QPixmap:
    dpr = max(1.0, float(widget.devicePixelRatioF()))
    width = max(1, int(round(widget.width() * dpr)))
    height = max(1, int(round(widget.height() * dpr)))
    frame = QPixmap(width, height)
    frame.setDevicePixelRatio(dpr)
    frame.fill(Qt.GlobalColor.transparent)
    return frame


def _fit_frame(source: QPixmap, widget: QWidget) -> QPixmap:
    if source.isNull() or widget.width() <= 0 or widget.height() <= 0:
        return QPixmap(source)

    dpr = max(1.0, float(widget.devicePixelRatioF()))
    target_width = max(1, int(round(widget.width() * dpr)))
    target_height = max(1, int(round(widget.height() * dpr)))
    same_pixels = source.width() == target_width and source.height() == target_height
    same_dpr = abs(float(source.devicePixelRatio()) - dpr) <= 1e-3
    if same_pixels and same_dpr:
        return QPixmap(source)

    fitted = source.scaled(
        target_width,
        target_height,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    fitted.setDevicePixelRatio(dpr)
    return fitted


class _ModalTransitionSurface(QWidget):
    """The only animated visual owner for modal transitions.

    Opening is an opaque cached A/B cross-fade because the real modal must be
    prepared invisibly under an exact entry frame.

    Closing is deliberately different: the *real live workspace* is restored
    underneath first, then one captured current-modal frame fades from opacity
    1 to 0. There is no synthetic workspace A frame and therefore no compositor
    handoff from a QPainter approximation to native Quick + layered QWidget.
    """

    clicked = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("cardDetailTransitionSurface")
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._base = QPixmap()
        self._top = QPixmap()
        self._progress = 0.0
        self._capture_suppressed = False
        self._live_underlay = False
        self.hide()

    def _sync_opaque_attribute(self) -> None:
        opaque = not self._capture_suppressed and not self._live_underlay
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, opaque)

    def set_capture_suppressed(self, suppressed: bool) -> None:
        suppressed = bool(suppressed)
        if suppressed == self._capture_suppressed:
            return
        self._capture_suppressed = suppressed
        self._sync_opaque_attribute()

    def set_hold_frame(self, frame: QPixmap) -> None:
        self._live_underlay = False
        self._sync_opaque_attribute()
        self._base = _fit_frame(frame, self)
        self._top = QPixmap()
        self._progress = 0.0
        self.update()

    def set_transition_frames(self, base: QPixmap, top: QPixmap, progress: float) -> None:
        self._live_underlay = False
        self._sync_opaque_attribute()
        self._base = _fit_frame(base, self)
        self._top = _fit_frame(top, self)
        self._progress = max(0.0, min(1.0, float(progress)))
        self.update()

    def set_live_underlay_fade(self, top: QPixmap, progress: float) -> None:
        """Fade one captured foreground over the actual live widgets beneath."""

        self._live_underlay = True
        self._sync_opaque_attribute()
        self._base = QPixmap()
        self._top = _fit_frame(top, self)
        self._progress = max(0.0, min(1.0, float(progress)))
        self.update()

    def clear_frames(self) -> None:
        self._base = QPixmap()
        self._top = QPixmap()
        self._progress = 0.0
        self._live_underlay = False
        self._sync_opaque_attribute()
        self.update()

    def set_progress(self, value: float) -> None:
        value = max(0.0, min(1.0, float(value)))
        if abs(value - self._progress) <= 1e-6:
            return
        self._progress = value
        self.update()

    def _draw_fitted(self, painter: QPainter, frame: QPixmap) -> None:
        if frame.isNull():
            return
        logical = frame.deviceIndependentSize()
        if (
            abs(float(logical.width()) - float(self.width())) <= 0.5
            and abs(float(logical.height()) - float(self.height())) <= 0.5
        ):
            painter.drawPixmap(0, 0, frame)
            return

        # Resize during a transition is rare and is snapped by the controller;
        # this remains only as a correctness fallback for the staging frame.
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawPixmap(self.rect(), frame, frame.rect())
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        if self._capture_suppressed:
            return

        painter = QPainter(self)
        if self._live_underlay:
            # Do not erase or synthesize a base frame. This child is non-opaque,
            # so the already-painted real workspace below remains authoritative.
            if self._progress > 0.0 and not self._top.isNull():
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
                painter.setOpacity(self._progress)
                self._draw_fitted(painter, self._top)
            painter.end()
            return

        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        if self._progress >= 1.0 - 1e-6 and not self._top.isNull():
            self._draw_fitted(painter, self._top)
            painter.end()
            return

        if not self._base.isNull():
            self._draw_fitted(painter, self._base)
        else:
            painter.fillRect(self.rect(), Qt.GlobalColor.black)
        if self._progress > 0.0 and not self._top.isNull():
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            painter.setOpacity(self._progress)
            self._draw_fitted(painter, self._top)
        painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)


class StaticModalInteractionController(QObject):
    """One transition surface plus one real interactive modal."""

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
        self._fallback_active = False
        self._underlay_suspended = False
        self._modal_closed_for_motion = False

        self._pointer_timer_was_active = False
        self._effects_timer_was_active = False
        self._activity_timer_was_active = False
        self._quick_animation_was_running = False

        self._motion_started_s = 0.0
        self._motion_duration_s = 0.001
        self._motion_from = 0.0
        self._motion_to = 0.0
        self._motion_easing = _css_ease()

        self._entry_workspace_frame = QPixmap()

        self._original_show_prepared_modal = self.details._show_prepared_modal  # noqa: SLF001
        self._original_close = self.details.close

        self.details.drawer.setGraphicsEffect(None)
        self.details.backdrop.hide()
        self.details.scrim.hide()
        self.details.ghost.hide()

        self._transition = _ModalTransitionSurface(self.root)
        self._transition.clicked.connect(self.request_close)

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

        pointer_timer = getattr(self.background, "_pointer_timer", None)
        try:
            self._pointer_timer_was_active = bool(
                pointer_timer is not None and pointer_timer.isActive()
            )
        except RuntimeError:
            self._pointer_timer_was_active = False
        if self._pointer_timer_was_active:
            try:
                pointer_timer.stop()
            except RuntimeError:
                pass

        quick = getattr(self.background, "quick_window", None)
        self._quick_animation_was_running = False
        if quick is not None:
            try:
                self._quick_animation_was_running = bool(quick.property("animationRunning"))
                quick.setProperty("animationRunning", False)
            except RuntimeError:
                self._quick_animation_was_running = False

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

        pointer_timer = getattr(self.background, "_pointer_timer", None)
        if self._pointer_timer_was_active and pointer_timer is not None:
            try:
                pointer_timer.start()
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

        quick = getattr(self.background, "quick_window", None)
        if self._quick_animation_was_running and quick is not None:
            try:
                quick.setProperty("animationRunning", True)
            except RuntimeError:
                pass
        self._quick_animation_was_running = False

        if self.background is not None:
            schedule_mask = getattr(self.background, "schedule_mask_update", None)
            if callable(schedule_mask):
                try:
                    schedule_mask()
                except RuntimeError:
                    pass

    def _sync_modal_geometry(self) -> None:
        self._transition.setGeometry(self.root.rect())
        self.details.backdrop.setGeometry(self.root.rect())
        self.details.scrim.setGeometry(self.root.rect())
        self.details.drawer.setGeometry(self.details._drawer_rect())  # noqa: SLF001
        self.details.body_layout.activate()
        drawer_layout = self.details.drawer.layout()
        if drawer_layout is not None:
            drawer_layout.activate()

    def _show_real_modal(self, blurred: QPixmap) -> None:
        self.details.backdrop.setPixmap(blurred)
        self.details.scroll.verticalScrollBar().setValue(0)
        self.details.ghost.hide()
        self._sync_modal_geometry()

        self.details.backdrop.show()
        self.details.backdrop.raise_()
        self.details.scrim.show()
        self.details.scrim.raise_()
        self.details.drawer.show()
        self.details.drawer.raise_()
        self._transition.raise_()

    def _render_root_without_transition(self) -> QPixmap:
        if self.root.width() <= 0 or self.root.height() <= 0:
            return QPixmap()
        frame = _empty_surface_frame(self.root)
        self._transition.set_capture_suppressed(True)
        try:
            self.root.render(
                frame,
                QPoint(0, 0),
                QRegion(),
                QWidget.RenderFlag.DrawChildren,
            )
        finally:
            self._transition.set_capture_suppressed(False)
        return frame

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
        target_hz = max(60.0, min(240.0, refresh_hz))
        return max(4, int(1000.0 / target_hz))

    def _stop_animation(self) -> None:
        self._motion_timer.stop()

    def _start_fade(self, *, end: float, duration_ms: int, easing: QEasingCurve) -> None:
        self._motion_timer.stop()
        self._motion_from = float(self._progress)
        self._motion_to = max(0.0, min(1.0, float(end)))
        self._motion_duration_s = max(0.001, float(duration_ms) / 1000.0)
        self._motion_easing = easing
        self._motion_started_s = time.perf_counter()
        self._motion_timer.setInterval(self._frame_interval_ms())
        self._motion_timer.start()

    def _set_progress(self, value: float) -> None:
        value = max(0.0, min(1.0, float(value)))
        self._progress = value
        self._transition.set_progress(value)

    def _advance_motion(self) -> None:
        elapsed_s = max(0.0, time.perf_counter() - self._motion_started_s)
        linear = min(1.0, elapsed_s / self._motion_duration_s)
        eased = float(self._motion_easing.valueForProgress(linear))
        value = self._motion_from + (self._motion_to - self._motion_from) * eased
        self._set_progress(value)

        if linear >= 1.0:
            self._motion_timer.stop()
            self._set_progress(self._motion_to)
            self._finish_motion()

    def _prepare_open_transition(self, *, ratio: tuple[float, float]) -> None:
        self.details._modal_ratio = ratio  # noqa: SLF001
        self._sync_modal_geometry()

        entry = self.details._capture_source()  # noqa: SLF001
        if entry.isNull():
            raise RuntimeError("failed to capture modal entry frame")
        self._entry_workspace_frame = _fit_frame(entry, self.root)

        blurred = self.details._blur_pixmap(entry)  # noqa: SLF001
        if blurred.isNull():
            raise RuntimeError("failed to prepare modal blur")

        # Hold the exact current screen while the one real modal is prepared.
        self._transition.set_hold_frame(self._entry_workspace_frame)
        self._transition.show()
        self._transition.raise_()
        self._transition.repaint()

        self._show_real_modal(blurred)
        final_modal = self._render_root_without_transition()
        if final_modal.isNull():
            raise RuntimeError("failed to capture final live modal frame")

        self._transition.set_transition_frames(
            self._entry_workspace_frame,
            final_modal,
            0.0,
        )
        self._transition.raise_()
        self._transition.repaint()
        self._progress = 0.0
        self._modal_closed_for_motion = False

    def _show_with_animation(self, *, ratio: tuple[float, float]) -> None:
        if self._state != _STATE_IDLE or not self.details.drawer.isHidden():
            return

        self._suspend_underlay()
        self._fallback_active = False
        self._state = _STATE_OPENING
        try:
            self._prepare_open_transition(ratio=ratio)
            self._start_fade(end=1.0, duration_ms=_OPEN_MS, easing=_css_ease())
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
        self._progress = 1.0
        self._transition.hide()
        self._transition.clear_frames()
        self._state = _STATE_OPEN
        self.details.close_button.setFocus(Qt.FocusReason.OtherFocusReason)

    def _prepare_close_transition(self) -> None:
        # This works both from steady OPEN and an interrupted OPENING: capture the
        # exact visual currently on screen, including the transition surface when
        # necessary. It becomes the only foreground frame used on close.
        current_modal = self.details._capture_source()  # noqa: SLF001
        if current_modal.isNull():
            raise RuntimeError("failed to capture current modal frame")
        current_modal = _fit_frame(current_modal, self.root)

        # Keep that exact image fully opaque while swapping what is underneath.
        self._sync_modal_geometry()
        self._transition.set_hold_frame(current_modal)
        self._transition.show()
        self._transition.raise_()
        self._transition.repaint()

        if not self.details.drawer.isHidden():
            self._original_close()
        self._modal_closed_for_motion = True

        # Restore the *real* native Quick + layered QWidget workspace while it is
        # still completely hidden by current_modal. From the first visible fade
        # frame onward there is no synthetic workspace and no later renderer swap.
        self._resume_underlay()
        self.root.update()

        self._transition.set_live_underlay_fade(current_modal, 1.0)
        self._transition.raise_()
        self._progress = 1.0

    def request_close(self, *_args: object) -> None:
        if self._fallback_active:
            self._fallback_close()
            return
        if self._state == _STATE_CLOSING:
            return

        if self._state in {_STATE_OPENING, _STATE_OPEN}:
            self._stop_animation()
            prior_progress = max(0.0, min(1.0, float(self._progress)))
            try:
                self._prepare_close_transition()
            except Exception:
                self._fallback_close()
                return
            self._state = _STATE_CLOSING
            duration = _CLOSE_MS
            if prior_progress < 1.0:
                duration = max(1, int(round(_CLOSE_MS * prior_progress)))
            self._start_fade(end=0.0, duration_ms=duration, easing=_css_ease_in_out())
            return

        if self._state == _STATE_IDLE and not self.details.drawer.isHidden():
            self._fallback_close()

    def _finish_close(self) -> None:
        if self._state != _STATE_CLOSING:
            return

        self._progress = 0.0
        self._transition.set_progress(0.0)
        if not self._modal_closed_for_motion:
            try:
                self._original_close()
            except RuntimeError:
                pass

        # At p=0 the user is already seeing the real live workspace underneath.
        # Hiding this now-transparent child cannot change brightness/compositing.
        self._transition.hide()
        self._transition.clear_frames()
        self._entry_workspace_frame = QPixmap()
        self._modal_closed_for_motion = False
        self._fallback_active = False
        self._state = _STATE_IDLE
        if self._underlay_suspended:
            self._resume_underlay()
        self.root.update()

    def _fallback_open(self, ratio: tuple[float, float]) -> None:
        self._stop_animation()
        self._transition.hide()
        self._transition.clear_frames()
        self._entry_workspace_frame = QPixmap()
        self._modal_closed_for_motion = False
        try:
            self._original_close()
        except RuntimeError:
            pass
        try:
            self._original_show_prepared_modal(ratio=ratio)
            self._fallback_active = True
            self._state = _STATE_OPEN
        except Exception:
            self._fallback_active = False
            self._state = _STATE_IDLE
            self._resume_underlay()

    def _fallback_close(self) -> None:
        self._stop_animation()
        self._transition.hide()
        self._transition.clear_frames()
        self._entry_workspace_frame = QPixmap()
        self._modal_closed_for_motion = False
        try:
            self._original_close()
        finally:
            self._fallback_active = False
            self._state = _STATE_IDLE
            self._resume_underlay()

    def _snap_motion_for_resize(self) -> None:
        if self._state not in {_STATE_OPENING, _STATE_CLOSING}:
            return
        target = self._motion_to
        self._stop_animation()
        self._sync_modal_geometry()
        self._progress = target
        self._transition.set_progress(target)
        self._finish_motion()

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
            elif event_type == QEvent.Type.Resize:
                if self._state in {_STATE_OPENING, _STATE_CLOSING}:
                    QTimer.singleShot(0, self._snap_motion_for_resize)
                elif self._state == _STATE_OPEN:
                    self._sync_modal_geometry()
        return False

    def cleanup(self) -> None:
        self._stop_animation()
        try:
            self._original_close()
        except RuntimeError:
            pass
        self._transition.hide()
        self._transition.clear_frames()
        self._entry_workspace_frame = QPixmap()
        self._fallback_active = False
        self._modal_closed_for_motion = False
        self._state = _STATE_IDLE
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
            self._transition.clicked.disconnect(self.request_close)
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

        try:
            self._transition.deleteLater()
        except RuntimeError:
            pass


def install_static_modal_interaction(
    window: QMainWindow,
    details: FastCardDetailController,
) -> StaticModalInteractionController:
    controller = StaticModalInteractionController(window, details)
    window._static_modal_interaction = controller  # type: ignore[attr-defined]
    return controller
