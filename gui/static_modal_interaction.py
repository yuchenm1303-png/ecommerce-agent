from __future__ import annotations

from PySide6.QtCore import QAbstractAnimation, QEasingCurve, QEvent, QObject, QPointF, Qt, QPropertyAnimation
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QFrame, QGraphicsOpacityEffect, QLabel, QMainWindow, QWidget

from .card_details_fast import FastCardDetailController


# The reference webpage uses a single live overlay whose whole subtree fades in.
# Its expanded detail box and settings overlay both use a 0.5 s fade.  The global
# Vue fade transition uses 0.3 s on leave, so the desktop port keeps the same
# simple rhythm while adding a graceful close instead of disappearing instantly.
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


class StaticModalInteractionController(QObject):
    """Reference-web-style fade for the existing live QWidget detail modal.

    There is exactly one visual owner for modal content: the real QWidget tree.
    Backdrop, scrim, drawer, text, tables and controls live under one transparent
    parent and share one opacity effect.  The final blurred backdrop is captured
    once before opening; no drawer snapshot, compositor, manual glass copy,
    transform, off-screen render or handoff exists in this path.
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
        self._underlay_suspended = False
        self._pointer_timer_was_active = False
        self._effects_timer_was_active = False
        self._quick_animation_was_running = False

        self._original_show_prepared_modal = self.details._show_prepared_modal  # noqa: SLF001
        self._original_close = self.details.close

        # FastCardDetailController already owns the real drawer and all business
        # content.  Reparent its three visible modal surfaces under one live layer
        # so a single opacity is equivalent to the webpage's parent overlay fade.
        self._modal_layer = QWidget(self.root)
        self._modal_layer.setObjectName("cardDetailFadeLayer")
        self._modal_layer.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self._modal_layer.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._modal_layer.setAutoFillBackground(False)
        self._modal_layer.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._modal_layer.hide()

        for widget in (self.details.backdrop, self.details.scrim, self.details.drawer):
            widget.hide()
            widget.setParent(self._modal_layer)

        self._fade_effect = QGraphicsOpacityEffect(self._modal_layer)
        self._fade_effect.setOpacity(1.0)
        self._fade_effect.setEnabled(False)
        self._modal_layer.setGraphicsEffect(self._fade_effect)

        self._fade_animation = QPropertyAnimation(self._fade_effect, b"opacity", self)
        self._fade_animation.finished.connect(self._finish_motion)

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

        quick = getattr(self.background, "quick_window", None)
        if self._quick_animation_was_running and quick is not None:
            try:
                quick.setProperty("animationRunning", True)
            except RuntimeError:
                pass
        self._quick_animation_was_running = False

    def _sync_modal_geometry(self) -> None:
        self._modal_layer.setGeometry(self.root.rect())
        self.details.backdrop.setGeometry(self._modal_layer.rect())
        self.details.scrim.setGeometry(self._modal_layer.rect())
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
        current = float(self._fade_effect.opacity())
        self._fade_animation.setStartValue(current)
        self._fade_animation.setEndValue(max(0.0, min(1.0, float(end))))
        self._fade_animation.setDuration(max(1, int(duration_ms)))
        self._fade_animation.setEasingCurve(easing)
        self._fade_animation.start()

    def _prepare_live_modal(self, *, ratio: tuple[float, float]) -> None:
        self.details._modal_ratio = ratio  # noqa: SLF001

        # QWidget has no native CSS backdrop-filter.  Mirror the reference site
        # by preparing one *final* blurred backdrop, then fading that real overlay
        # over the still-clear application.  Blur is never recomputed per frame.
        snapshot = self.details._capture_backdrop()  # noqa: SLF001
        if snapshot.isNull():
            raise RuntimeError("failed to capture modal backdrop")

        self.details.backdrop.setPixmap(snapshot)
        self.details.scroll.verticalScrollBar().setValue(0)
        self.details.ghost.hide()
        self._sync_modal_geometry()

        # Everything is already in its final live geometry before the first
        # visible frame.  The single parent opacity is the only animated value.
        self._fade_effect.setEnabled(True)
        self._fade_effect.setOpacity(0.0)

        self.details.backdrop.show()
        self.details.backdrop.raise_()
        self.details.scrim.show()
        self.details.scrim.raise_()
        self.details.drawer.show()
        self.details.drawer.raise_()
        self._modal_layer.show()
        self._modal_layer.raise_()
        self._modal_layer.repaint()

    def _show_with_animation(self, *, ratio: tuple[float, float]) -> None:
        if self._state != _STATE_IDLE or not self.details.drawer.isHidden():
            return

        self._suspend_underlay()
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
        self._fade_effect.setOpacity(1.0)
        # At steady state the live widgets draw directly.  The expensive full
        # overlay opacity composition exists only during the short transition.
        self._fade_effect.setEnabled(False)
        self._modal_layer.repaint()
        self._state = _STATE_OPEN
        self.details.close_button.setFocus(Qt.FocusReason.OtherFocusReason)
        self.details._schedule_geometry()  # noqa: SLF001

    def request_close(self, *_args: object) -> None:
        if self._state == _STATE_CLOSING:
            return

        if self._state == _STATE_OPENING:
            self._stop_animation()
            current = max(0.0, min(1.0, float(self._fade_effect.opacity())))
            self._state = _STATE_CLOSING
            duration = max(1, int(round(_CLOSE_MS * current)))
            self._start_fade(end=0.0, duration_ms=duration, easing=_css_ease_in_out())
            return

        if self._state == _STATE_OPEN:
            self._stop_animation()
            # Re-enable one parent opacity at 1.0.  No visual copy or second
            # drawer is introduced; the same live subtree simply enters fade-out.
            self._fade_effect.setOpacity(1.0)
            self._fade_effect.setEnabled(True)
            self._modal_layer.raise_()
            self._modal_layer.repaint()
            self._state = _STATE_CLOSING
            self._start_fade(end=0.0, duration_ms=_CLOSE_MS, easing=_css_ease_in_out())
            return

        if self._state == _STATE_IDLE and not self.details.drawer.isHidden():
            self._fallback_close()

    def _finish_close(self) -> None:
        if self._state != _STATE_CLOSING:
            return

        self._fade_effect.setOpacity(0.0)
        try:
            self._original_close()
        finally:
            self._modal_layer.hide()
            self._fade_effect.setEnabled(False)
            self._fade_effect.setOpacity(1.0)
            self._state = _STATE_IDLE
            self._resume_underlay()

    def _fallback_open(self, ratio: tuple[float, float]) -> None:
        self._stop_animation()
        self._fade_effect.setEnabled(False)
        self._fade_effect.setOpacity(1.0)
        self._sync_modal_geometry()
        self._modal_layer.show()
        self._modal_layer.raise_()
        try:
            self._original_close()
        except RuntimeError:
            pass
        try:
            self._original_show_prepared_modal(ratio=ratio)
            self._modal_layer.raise_()
            self._state = _STATE_OPEN
        except Exception:
            self._modal_layer.hide()
            self._state = _STATE_IDLE
            self._resume_underlay()

    def _fallback_close(self) -> None:
        self._stop_animation()
        self._fade_effect.setEnabled(False)
        self._fade_effect.setOpacity(1.0)
        self._original_close()
        self._modal_layer.hide()
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
            elif event_type == QEvent.Type.Resize and self._modal_layer.isVisible():
                self._sync_modal_geometry()
        return False

    def cleanup(self) -> None:
        self._stop_animation()
        try:
            self._original_close()
        except RuntimeError:
            pass
        self._modal_layer.hide()
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

        # Leave FastCardDetailController structurally valid if this presentation
        # adapter is ever torn down independently.
        for widget in (self.details.backdrop, self.details.scrim, self.details.drawer):
            try:
                widget.hide()
                widget.setParent(self.root)
            except RuntimeError:
                pass
        try:
            self._modal_layer.setGraphicsEffect(None)
            self._modal_layer.deleteLater()
        except RuntimeError:
            pass


def install_static_modal_interaction(
    window: QMainWindow,
    details: FastCardDetailController,
) -> StaticModalInteractionController:
    controller = StaticModalInteractionController(window, details)
    window._static_modal_interaction = controller  # type: ignore[attr-defined]
    return controller
