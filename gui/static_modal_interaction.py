from __future__ import annotations

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QEvent,
    QObject,
    QPoint,
    QPointF,
    Property,
    QRect,
    QRectF,
    Qt,
    QPropertyAnimation,
)
from PySide6.QtGui import QKeyEvent, QPainter, QPixmap, QRegion
from PySide6.QtWidgets import QFrame, QLabel, QMainWindow, QWidget

from .card_details_fast import FastCardDetailController


_OPEN_MS = 300
_CLOSE_MS = 250
_OPEN_RISE_PX = 18.0
_START_SCALE = 0.992
_COMPOSITOR_PAD = 24

_STATE_IDLE = "idle"
_STATE_OPENING = "opening"
_STATE_OPEN = "open"
_STATE_CLOSING = "closing"


class _PanelCompositor(QWidget):
    """Paint one cached drawer frame with float transforms only.

    This is a normal non-native QWidget child. It never accepts input and covers
    only the panel transition bounds instead of the full application surface.
    """

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("cardDetailTransitionCompositor")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAutoFillBackground(False)

        self._frame = QPixmap()
        self._target_local = QRectF()
        self._progress = 0.0
        self.hide()

    def _get_progress(self) -> float:
        return self._progress

    def _set_progress(self, value: float) -> None:
        value = max(0.0, min(1.0, float(value)))
        if abs(value - self._progress) < 0.0001:
            return
        self._progress = value
        self.update()

    progress = Property(float, _get_progress, _set_progress)

    def set_frame(self, frame: QPixmap, target: QRect, *, progress: float) -> None:
        self._frame = frame
        parent = self.parentWidget()
        parent_rect = parent.rect() if parent is not None else target
        bounds = target.adjusted(
            -_COMPOSITOR_PAD,
            -_COMPOSITOR_PAD,
            _COMPOSITOR_PAD,
            _COMPOSITOR_PAD + int(_OPEN_RISE_PX),
        ).intersected(parent_rect)
        self.setGeometry(bounds)
        translated = target.translated(QPoint(-bounds.x(), -bounds.y()))
        self._target_local = QRectF(translated)
        self._progress = max(0.0, min(1.0, float(progress)))
        self.update()

    def clear_frame(self) -> None:
        self._frame = QPixmap()
        self._target_local = QRectF()
        self._progress = 0.0

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        if self._frame.isNull() or self._target_local.isEmpty():
            return

        progress = max(0.0, min(1.0, self._progress))
        scale = _START_SCALE + (1.0 - _START_SCALE) * progress
        y_offset = _OPEN_RISE_PX * (1.0 - progress)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setOpacity(progress)

        target = self._target_local
        center = target.center()
        painter.translate(center.x(), center.y() + y_offset)
        painter.scale(scale, scale)
        painter.translate(-target.width() / 2.0, -target.height() / 2.0)
        painter.drawPixmap(QPointF(0.0, 0.0), self._frame)
        painter.end()


class StaticModalInteractionController(QObject):
    """Composited QWidget modal transition with no per-frame child-tree paint.

    The real detail drawer is fully laid out and synchronously rendered once.
    Opening/closing then animates only one cached pixmap in a mouse-transparent
    child compositor with float translation/scale/opacity. The real drawer is
    shown only after the final cached frame is already on screen.
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

        # FastCardDetailController already owns the final glass appearance.
        # Transition frames must not attach a QGraphicsOpacityEffect to that
        # complex child tree; the compositor handles opacity as one cached layer.
        self.details.drawer.setGraphicsEffect(None)
        self.details.drawer_effect = None  # type: ignore[assignment]

        self._compositor = _PanelCompositor(self.root)
        self._progress_animation = QPropertyAnimation(self._compositor, b"progress", self)
        self._progress_animation.finished.connect(self._finish_motion)

        self.details._show_prepared_modal = self._show_with_animation  # type: ignore[method-assign]  # noqa: SLF001
        self.details.close = self.request_close  # type: ignore[method-assign]
        self.details._schedule_geometry = self._schedule_geometry_guarded  # type: ignore[method-assign]  # noqa: SLF001
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

    def _stop_animation(self) -> None:
        if self._progress_animation.state() != QAbstractAnimation.State.Stopped:
            self._progress_animation.stop()

    def _start_progress_animation(
        self,
        *,
        end: float,
        duration_ms: int,
        easing: QEasingCurve.Type,
    ) -> None:
        self._progress_animation.stop()
        current = float(self._compositor.property("progress") or 0.0)
        self._progress_animation.setStartValue(current)
        self._progress_animation.setEndValue(float(end))
        self._progress_animation.setDuration(max(1, int(duration_ms)))
        self._progress_animation.setEasingCurve(easing)
        self._progress_animation.start()

    def _render_drawer_frame(self) -> QPixmap:
        drawer = self.details.drawer
        drawer.ensurePolished()
        self.details.body_layout.activate()
        if drawer.layout() is not None:
            drawer.layout().activate()

        dpr = max(1.0, float(drawer.devicePixelRatioF()))
        width = max(1, int(round(drawer.width() * dpr)))
        height = max(1, int(round(drawer.height() * dpr)))
        frame = QPixmap(width, height)
        frame.setDevicePixelRatio(dpr)
        frame.fill(Qt.GlobalColor.transparent)
        drawer.render(
            frame,
            QPoint(0, 0),
            QRegion(),
            QWidget.RenderFlag.DrawChildren,
        )
        return frame

    def _prepare_open_state(self, *, ratio: tuple[float, float]) -> QRect:
        self.details._modal_ratio = ratio  # noqa: SLF001
        backdrop = self.details._capture_backdrop()  # noqa: SLF001
        target = self.details._drawer_rect()  # noqa: SLF001

        updates_were_enabled = self.root.updatesEnabled()
        if updates_were_enabled:
            self.root.setUpdatesEnabled(False)

        try:
            self.details.backdrop.setPixmap(backdrop)
            self.details.backdrop.setGeometry(self.root.rect())
            self.details.scrim.setGeometry(self.root.rect())
            self.details.drawer.setGeometry(target)
            self.details.body_layout.activate()
            if self.details.drawer.layout() is not None:
                self.details.drawer.layout().activate()
            self.details.scroll.verticalScrollBar().setValue(0)
            self.details.ghost.hide()

            # The drawer is shown only while it paints into the off-screen cache.
            # Root updates are disabled, so no intermediate real frame is exposed.
            self.details.drawer.show()
            self.details.drawer.raise_()
            frame = self._render_drawer_frame()
            if frame.isNull():
                raise RuntimeError("failed to render modal drawer frame")
            self.details.drawer.hide()

            self.details.backdrop.show()
            self.details.backdrop.raise_()
            self.details.scrim.show()
            self.details.scrim.raise_()

            self._compositor.set_frame(frame, target, progress=0.0)
            self._compositor.show()
            self._compositor.raise_()
        finally:
            if updates_were_enabled:
                self.root.setUpdatesEnabled(True)

        self.root.repaint()
        return target

    def _prepare_close_state(self) -> QRect:
        target = self.details._drawer_rect()  # noqa: SLF001
        self.details.drawer.setGeometry(target)
        frame = self._render_drawer_frame()
        if frame.isNull():
            raise RuntimeError("failed to render modal close frame")

        updates_were_enabled = self.root.updatesEnabled()
        if updates_were_enabled:
            self.root.setUpdatesEnabled(False)
        try:
            self._compositor.set_frame(frame, target, progress=1.0)
            self._compositor.show()
            self._compositor.raise_()
            self.details.drawer.hide()
        finally:
            if updates_were_enabled:
                self.root.setUpdatesEnabled(True)

        self.root.repaint()
        self._compositor.repaint()
        return target

    def _show_with_animation(self, *, ratio: tuple[float, float]) -> None:
        if self._state != _STATE_IDLE or self.details.drawer.isVisible():
            return

        self._suspend_underlay()
        self._state = _STATE_OPENING
        self._set_motion_active(True)
        try:
            self._prepare_open_state(ratio=ratio)
            self._start_progress_animation(
                end=1.0,
                duration_ms=_OPEN_MS,
                easing=QEasingCurve.Type.OutCubic,
            )
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

        target = self.details._drawer_rect()  # noqa: SLF001
        updates_were_enabled = self.root.updatesEnabled()
        if updates_were_enabled:
            self.root.setUpdatesEnabled(False)

        try:
            self.details.drawer.setGeometry(target)
            self.details.drawer.show()
            self.details.drawer.raise_()
            self._compositor.hide()
            self._compositor.clear_frame()
        finally:
            if updates_were_enabled:
                self.root.setUpdatesEnabled(True)

        self.root.repaint()
        self._state = _STATE_OPEN
        self._set_motion_active(False)
        self.details.close_button.setFocus(Qt.FocusReason.OtherFocusReason)
        self._original_schedule_geometry()

    def request_close(self, *_args: object) -> None:
        if self._state == _STATE_CLOSING:
            return

        if self.details.drawer.isHidden() and self.details.scrim.isHidden():
            self._stop_animation()
            self._compositor.hide()
            self._compositor.clear_frame()
            self._state = _STATE_IDLE
            self._set_motion_active(False)
            self._resume_underlay()
            return

        if self._state == _STATE_OPENING:
            self._stop_animation()
            self._state = _STATE_CLOSING
            self._set_motion_active(True)
            current = float(self._compositor.property("progress") or 0.0)
            duration = max(1, int(round(_CLOSE_MS * current)))
            self._start_progress_animation(
                end=0.0,
                duration_ms=duration,
                easing=QEasingCurve.Type.InCubic,
            )
            return

        if self._state not in {_STATE_OPEN, _STATE_IDLE}:
            return

        self._stop_animation()
        self._state = _STATE_CLOSING
        self._set_motion_active(True)
        try:
            self._prepare_close_state()
            self._start_progress_animation(
                end=0.0,
                duration_ms=_CLOSE_MS,
                easing=QEasingCurve.Type.InCubic,
            )
        except Exception:
            self._fallback_close()

    def _finish_close(self) -> None:
        if self._state != _STATE_CLOSING:
            return

        updates_were_enabled = self.root.updatesEnabled()
        if updates_were_enabled:
            self.root.setUpdatesEnabled(False)
        try:
            self._compositor.hide()
            self._original_close()
            self._compositor.clear_frame()
        finally:
            if updates_were_enabled:
                self.root.setUpdatesEnabled(True)

        self.root.repaint()
        self._state = _STATE_IDLE
        self._set_motion_active(False)
        self._resume_underlay()

    def _fallback_open(self, ratio: tuple[float, float]) -> None:
        self._stop_animation()
        self._compositor.hide()
        self._compositor.clear_frame()
        self._state = _STATE_IDLE
        self._set_motion_active(False)
        try:
            self._original_close()
        except RuntimeError:
            pass
        try:
            self._original_show_prepared_modal(ratio=ratio)
            self._state = _STATE_OPEN
        except Exception:
            self._state = _STATE_IDLE
            self._resume_underlay()

    def _fallback_close(self) -> None:
        self._stop_animation()
        self._compositor.hide()
        self._compositor.clear_frame()
        self._state = _STATE_IDLE
        self._set_motion_active(False)
        self._original_close()
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
            elif event_type == QEvent.Type.Resize:
                # A mid-transition resize invalidates the cached target rect.
                # Finalize atomically rather than stretching a stale snapshot.
                if self._state == _STATE_OPENING:
                    self._stop_animation()
                    self._finish_open()
                elif self._state == _STATE_CLOSING:
                    self._stop_animation()
                    self._finish_close()
        return False

    def cleanup(self) -> None:
        self._stop_animation()
        self._compositor.hide()
        self._compositor.clear_frame()
        self._state = _STATE_IDLE
        self._set_motion_active(False)
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
            self.details._schedule_geometry = self._original_schedule_geometry  # type: ignore[method-assign]  # noqa: SLF001
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
