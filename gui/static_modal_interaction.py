from __future__ import annotations

import time

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QEvent,
    QObject,
    QPointF,
    Property,
    Qt,
    QPropertyAnimation,
    Signal,
)
from PySide6.QtGui import QColor, QKeyEvent, QMouseEvent, QPainter, QPixmap
from PySide6.QtWidgets import QFrame, QGraphicsOpacityEffect, QLabel, QMainWindow, QWidget

from .card_details_fast import FastCardDetailController


# The reference webpage swaps the normal card group out when boxOpenState opens,
# then lets the real expanded box run a simple .5 s fade. QWidget cannot use the
# browser's backdrop-filter, so the desktop port freezes the old surface into one
# clear/blurred backdrop pair and animates one shared progress value.
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


class _FrozenBackdrop(QWidget):
    """Opaque frozen underlay: clear -> blurred + scrim, driven by one progress."""

    clicked = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("cardDetailFrozenBackdrop")
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setAutoFillBackground(False)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._clear = QPixmap()
        self._blurred = QPixmap()
        self._progress = 0.0
        self.hide()

    def set_frames(self, clear: QPixmap, blurred: QPixmap) -> None:
        self._clear = QPixmap(clear)
        self._blurred = QPixmap(blurred)
        self.update()

    def clear_frames(self) -> None:
        self._clear = QPixmap()
        self._blurred = QPixmap()
        self._progress = 0.0
        self.update()

    def set_progress(self, value: float) -> None:
        value = max(0.0, min(1.0, float(value)))
        if abs(value - self._progress) <= 1e-6:
            return
        self._progress = value
        self.update()

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        target = self.rect()
        if not self._clear.isNull():
            painter.setOpacity(1.0)
            painter.drawPixmap(target, self._clear, self._clear.rect())
        else:
            painter.fillRect(target, QColor(0, 0, 0))

        if self._progress > 0.0 and not self._blurred.isNull():
            painter.setOpacity(self._progress)
            painter.drawPixmap(target, self._blurred, self._blurred.rect())

        painter.setOpacity(1.0)
        if self._progress > 0.0:
            painter.fillRect(
                target,
                QColor(12, 17, 26, int(round(94.0 * self._progress))),
            )
        painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)


class StaticModalInteractionController(QObject):
    """Reference-web-style detail fade without a full-screen opacity effect.

    The real drawer is never copied. A single scalar progress drives:
      - one opaque frozen backdrop (clear -> final blur + scrim);
      - one opacity effect attached only to the much smaller real drawer.

    Because the backdrop is opaque, the large workspace beneath it does not
    participate in transition repainting, matching the reference page's
    `v-show(!boxOpenState)` behavior without destroying live business widgets.
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
        self._fallback_active = False
        self._pointer_timer_was_active = False
        self._effects_timer_was_active = False
        self._activity_timer_was_active = False
        self._quick_animation_was_running = False

        self._original_show_prepared_modal = self.details._show_prepared_modal  # noqa: SLF001
        self._original_close = self.details.close

        self.details.backdrop.hide()
        self.details.scrim.hide()
        self.details.ghost.hide()

        self._frozen_backdrop = _FrozenBackdrop(self.root)
        self._frozen_backdrop.clicked.connect(self.request_close)

        self._drawer_effect = QGraphicsOpacityEffect(self.details.drawer)
        self._drawer_effect.setOpacity(1.0)
        self._drawer_effect.setEnabled(False)
        self.details.drawer.setGraphicsEffect(self._drawer_effect)

        self._fade_animation = QPropertyAnimation(self, b"progress", self)
        self._fade_animation.finished.connect(self._finish_motion)

        self.details._show_prepared_modal = self._show_with_animation  # type: ignore[method-assign]  # noqa: SLF001
        self.details.close = self.request_close  # type: ignore[method-assign]
        self._rewire_close_inputs()
        self._install_card_surfaces()
        self.root.installEventFilter(self)
        window.destroyed.connect(self.cleanup)

    def _get_progress(self) -> float:
        return self._progress

    def _set_progress(self, value: float) -> None:
        value = max(0.0, min(1.0, float(value)))
        self._progress = value
        self._frozen_backdrop.set_progress(value)
        self._drawer_effect.setOpacity(value)

    progress = Property(float, _get_progress, _set_progress)

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
                value = quick.property("animationRunning")
                self._quick_animation_was_running = bool(value)
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

    def _sync_modal_geometry(self) -> None:
        self._frozen_backdrop.setGeometry(self.root.rect())
        self.details.drawer.setGeometry(self.details._drawer_rect())  # noqa: SLF001
        self.details.body_layout.activate()
        drawer_layout = self.details.drawer.layout()
        if drawer_layout is not None:
            drawer_layout.activate()

    def _stop_animation(self) -> None:
        if self._fade_animation.state() != QAbstractAnimation.State.Stopped:
            self._fade_animation.stop()

    def _start_fade(self, *, end: float, duration_ms: int, easing: QEasingCurve) -> None:
        self._fade_animation.stop()
        current = float(self._progress)
        self._fade_animation.setStartValue(current)
        self._fade_animation.setEndValue(max(0.0, min(1.0, float(end))))
        self._fade_animation.setDuration(max(1, int(duration_ms)))
        self._fade_animation.setEasingCurve(easing)
        self._fade_animation.start()

    def _prepare_live_modal(self, *, ratio: tuple[float, float]) -> None:
        self.details._modal_ratio = ratio  # noqa: SLF001

        source = self.details._capture_source()  # noqa: SLF001
        if source.isNull():
            raise RuntimeError("failed to capture modal source")
        blurred = self.details._blur_pixmap(source)  # noqa: SLF001
        if blurred.isNull():
            raise RuntimeError("failed to prepare modal blur")

        self._frozen_backdrop.set_frames(source, blurred)
        self.details.scroll.verticalScrollBar().setValue(0)
        self.details.backdrop.hide()
        self.details.scrim.hide()
        self.details.ghost.hide()
        self._sync_modal_geometry()

        self._drawer_effect.setEnabled(True)
        self._set_progress(0.0)

        self._frozen_backdrop.show()
        self._frozen_backdrop.raise_()
        self.details.drawer.show()
        self.details.drawer.raise_()
        self._frozen_backdrop.repaint()
        self.details.drawer.repaint()

    def _show_with_animation(self, *, ratio: tuple[float, float]) -> None:
        if self._state != _STATE_IDLE or not self.details.drawer.isHidden():
            return

        self._suspend_underlay()
        self._fallback_active = False
        self._state = _STATE_OPENING
        try:
            self._prepare_live_modal(ratio=ratio)
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
        self._set_progress(1.0)
        self._drawer_effect.setEnabled(False)
        self.details.drawer.repaint()
        self._state = _STATE_OPEN
        self.details.close_button.setFocus(Qt.FocusReason.OtherFocusReason)
        self.details._schedule_geometry()  # noqa: SLF001

    def request_close(self, *_args: object) -> None:
        if self._fallback_active:
            self._fallback_close()
            return
        if self._state == _STATE_CLOSING:
            return

        if self._state == _STATE_OPENING:
            self._stop_animation()
            current = max(0.0, min(1.0, float(self._progress)))
            self._drawer_effect.setEnabled(True)
            self._drawer_effect.setOpacity(current)
            self._state = _STATE_CLOSING
            duration = max(1, int(round(_CLOSE_MS * current)))
            self._start_fade(end=0.0, duration_ms=duration, easing=_css_ease_in_out())
            return

        if self._state == _STATE_OPEN:
            self._stop_animation()
            self._set_progress(1.0)
            self._drawer_effect.setEnabled(True)
            self._drawer_effect.setOpacity(1.0)
            self.details.drawer.repaint()
            self._state = _STATE_CLOSING
            self._start_fade(end=0.0, duration_ms=_CLOSE_MS, easing=_css_ease_in_out())
            return

        if self._state == _STATE_IDLE and not self.details.drawer.isHidden():
            self._fallback_close()

    def _finish_close(self) -> None:
        if self._state != _STATE_CLOSING:
            return

        self._set_progress(0.0)
        try:
            self._original_close()
        finally:
            self._frozen_backdrop.hide()
            self._frozen_backdrop.clear_frames()
            self._drawer_effect.setEnabled(False)
            self._drawer_effect.setOpacity(1.0)
            self._fallback_active = False
            self._state = _STATE_IDLE
            self._resume_underlay()
            self.root.update()

    def _fallback_open(self, ratio: tuple[float, float]) -> None:
        self._stop_animation()
        self._frozen_backdrop.hide()
        self._frozen_backdrop.clear_frames()
        self._drawer_effect.setEnabled(False)
        self._drawer_effect.setOpacity(1.0)
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
        self._frozen_backdrop.hide()
        self._frozen_backdrop.clear_frames()
        self._drawer_effect.setEnabled(False)
        self._drawer_effect.setOpacity(1.0)
        self._original_close()
        self._fallback_active = False
        self._state = _STATE_IDLE
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
            elif event_type == QEvent.Type.Resize and self._frozen_backdrop.isVisible():
                self._sync_modal_geometry()
        return False

    def cleanup(self) -> None:
        self._stop_animation()
        try:
            self._original_close()
        except RuntimeError:
            pass
        self._frozen_backdrop.hide()
        self._frozen_backdrop.clear_frames()
        self._fallback_active = False
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
            self._frozen_backdrop.clicked.disconnect(self.request_close)
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
            self.details.drawer.setGraphicsEffect(None)
            self._frozen_backdrop.deleteLater()
        except RuntimeError:
            pass


def install_static_modal_interaction(
    window: QMainWindow,
    details: FastCardDetailController,
) -> StaticModalInteractionController:
    controller = StaticModalInteractionController(window, details)
    window._static_modal_interaction = controller  # type: ignore[attr-defined]
    return controller
