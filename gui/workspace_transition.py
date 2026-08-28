from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QPoint,
    QPointF,
    QRect,
    Qt,
    QTimer,
    Slot,
)
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QGraphicsOpacityEffect, QMainWindow, QStackedWidget, QWidget

from .workspace_transition_snapshot import WorkspaceTransitionSnapshotRenderer


_HOLD_MS = 40
_EXIT_END_MS = 155
_ENTER_START_MS = 175
_TOTAL_MS = 390
_ENTER_DURATION_MS = _TOTAL_MS - _ENTER_START_MS

_HEADER_EXIT_START_MS = 45
_HEADER_EXIT_END_MS = 125
_HEADER_ENTER_START_MS = 150
_HEADER_ENTER_END_MS = 270

_VEIL_START_MS = 135
_VEIL_PEAK_MS = 170
_VEIL_END_MS = 220
_VEIL_MAX_OPACITY = 0.06
_VEIL_COLOR = QColor(228, 241, 250)

_QUICK_SYNC_TIMEOUT_MS = 96


def _cubic_bezier(c1x: float, c1y: float, c2x: float, c2y: float) -> QEasingCurve:
    curve = QEasingCurve(QEasingCurve.Type.BezierSpline)
    curve.addCubicBezierSegment(
        QPointF(c1x, c1y),
        QPointF(c2x, c2y),
        QPointF(1.0, 1.0),
    )
    return curve


def _exit_easing() -> QEasingCurve:
    return _cubic_bezier(0.40, 0.00, 1.00, 1.00)


def _enter_easing() -> QEasingCurve:
    return _cubic_bezier(0.16, 1.00, 0.30, 1.00)


def _smoothstep(value: float) -> float:
    value = max(0.0, min(1.0, float(value)))
    return value * value * (3.0 - 2.0 * value)


def _segment_progress(elapsed_ms: float, start_ms: float, end_ms: float) -> float:
    if elapsed_ms <= start_ms:
        return 0.0
    if elapsed_ms >= end_ms:
        return 1.0
    duration = max(1e-6, float(end_ms - start_ms))
    return (float(elapsed_ms) - float(start_ms)) / duration


def _fit_frame(source: QPixmap, widget: QWidget) -> QPixmap:
    """Fit without non-uniform distortion; normal path is already exact-size."""

    if source.isNull() or widget.width() <= 0 or widget.height() <= 0:
        return QPixmap(source)

    dpr = max(1.0, float(widget.devicePixelRatioF()))
    target_width = max(1, int(round(widget.width() * dpr)))
    target_height = max(1, int(round(widget.height() * dpr)))
    same_pixels = source.width() == target_width and source.height() == target_height
    same_dpr = abs(float(source.devicePixelRatio()) - dpr) <= 1e-3
    if same_pixels and same_dpr:
        return QPixmap(source)

    scaled = source.scaled(
        target_width,
        target_height,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    left = max(0, (scaled.width() - target_width) // 2)
    top = max(0, (scaled.height() - target_height) // 2)
    fitted = scaled.copy(left, top, target_width, target_height)
    fitted.setDevicePixelRatio(dpr)
    return fitted


class _WorkspaceTransitionSurface(QWidget):
    """Opaque root-level owner of modeStack pixels for the complete handoff."""

    def __init__(self, root: QWidget) -> None:
        super().__init__(root)
        self.setObjectName("workspaceTransitionSurface")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._neutral = QPixmap()
        self._outgoing = QPixmap()
        self._incoming = QPixmap()
        self._outgoing_alpha = 1.0
        self._incoming_alpha = 0.0
        self._veil_alpha = 0.0
        self.hide()

    def begin(self, neutral: QPixmap, outgoing: QPixmap) -> None:
        self._neutral = _fit_frame(neutral, self)
        self._outgoing = _fit_frame(outgoing, self)
        self._incoming = QPixmap()
        self._outgoing_alpha = 1.0
        self._incoming_alpha = 0.0
        self._veil_alpha = 0.0
        self.update()

    def set_incoming(self, incoming: QPixmap) -> None:
        self._incoming = _fit_frame(incoming, self)
        self.update()

    def set_mix(
        self,
        *,
        outgoing_alpha: float,
        incoming_alpha: float,
        veil_alpha: float,
    ) -> None:
        outgoing_alpha = max(0.0, min(1.0, float(outgoing_alpha)))
        incoming_alpha = max(0.0, min(1.0, float(incoming_alpha)))
        veil_alpha = max(0.0, min(_VEIL_MAX_OPACITY, float(veil_alpha)))
        changed = (
            abs(outgoing_alpha - self._outgoing_alpha) > 1e-5
            or abs(incoming_alpha - self._incoming_alpha) > 1e-5
            or abs(veil_alpha - self._veil_alpha) > 1e-5
        )
        self._outgoing_alpha = outgoing_alpha
        self._incoming_alpha = incoming_alpha
        self._veil_alpha = veil_alpha
        if changed:
            self.update()

    def clear_frames(self) -> None:
        self._neutral = QPixmap()
        self._outgoing = QPixmap()
        self._incoming = QPixmap()
        self._outgoing_alpha = 1.0
        self._incoming_alpha = 0.0
        self._veil_alpha = 0.0
        self.update()

    def _draw_frame(self, painter: QPainter, frame: QPixmap) -> None:
        if frame.isNull():
            return
        painter.drawPixmap(0, 0, _fit_frame(frame, self))

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        painter = QPainter(self)

        # The surface writes every workspace pixel. Live QWidget/Quick geometry may
        # update underneath, but no intermediate layout can leak to the user.
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(), QColor(23, 38, 58))
        if not self._neutral.isNull():
            self._draw_frame(painter, self._neutral)

        if self._outgoing_alpha > 1e-5 and not self._outgoing.isNull():
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            painter.setOpacity(self._outgoing_alpha)
            self._draw_frame(painter, self._outgoing)

        if self._incoming_alpha > 1e-5 and not self._incoming.isNull():
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            painter.setOpacity(self._incoming_alpha)
            self._draw_frame(painter, self._incoming)

        if self._veil_alpha > 1e-5:
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            painter.setOpacity(self._veil_alpha)
            painter.fillRect(self.rect(), _VEIL_COLOR)

        painter.end()


class WorkspaceTransitionController(QObject):
    """Pure presentation transition over two already-committed persistent pages.

    This controller never calls QLayout.activate(), never prepares hidden page
    geometry, and never combines Quick pixels with QWidget pixels. Geometry belongs
    to WorkspaceLayoutCommitter; Batch width belongs to BatchCardResponsiveController.
    The only business mutation here is the existing _set_workspace_mode call, made
    after the outgoing snapshot is fully covered by the neutral frame.
    """

    def __init__(self, window: QMainWindow, visual: Any) -> None:
        super().__init__(window)
        self.window = window
        self.visual = visual
        self.root = window.centralWidget()
        self.stack = getattr(window, "mode_stack", None)
        self.background = getattr(visual, "background", None)
        self._set_mode = getattr(window, "_set_workspace_mode", None)
        if (
            self.root is None
            or not isinstance(self.stack, QStackedWidget)
            or not callable(self._set_mode)
        ):
            raise RuntimeError("workspace transition requires installed mode workspace")

        self._snapshot_renderer = WorkspaceTransitionSnapshotRenderer(window, visual, self.stack)
        self._surface = _WorkspaceTransitionSurface(self.root)
        self._active = False
        self._transition_geometry: QRect | None = None
        self._sync_surface_geometry()
        self._surface.hide()

        self._target_index = int(self.stack.currentIndex())
        self._queued_index: int | None = None
        self._outgoing = QPixmap()
        self._incoming = QPixmap()
        self._neutral = QPixmap()
        self._started_s = 0.0
        self._incoming_enter_start_ms = float(_ENTER_START_MS)
        self._mode_switched = False
        self._live_ready = False
        self._pointer_timer_was_active = False
        self._card_fx_suspended = False

        self._phase_badge = getattr(window, "phase_badge", None)
        self._phase_effect: QGraphicsOpacityEffect | None = None
        self._phase_old_text = ""
        self._phase_new_text = ""
        self._phase_swapped = False
        self._phase_enter_start_ms = float(_HEADER_ENTER_START_MS)

        self._awaiting_quick_frame = False
        self._quick_frame_connected = False
        self._quick_sync_timeout = QTimer(self)
        self._quick_sync_timeout.setSingleShot(True)
        self._quick_sync_timeout.setTimerType(Qt.TimerType.PreciseTimer)
        self._quick_sync_timeout.timeout.connect(self._mark_live_ready)

        quick = getattr(self.background, "quick_window", None)
        if quick is not None:
            try:
                quick.frameSwapped.connect(
                    self._on_quick_frame_swapped,
                    type=Qt.ConnectionType.QueuedConnection,
                )
                self._quick_frame_connected = True
            except (RuntimeError, TypeError):
                self._quick_frame_connected = False

        self._exit_easing = _exit_easing()
        self._enter_easing = _enter_easing()
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._advance)

        self.stack.installEventFilter(self)
        self.root.installEventFilter(self)
        window.destroyed.connect(self.cleanup)

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

    def _surface_geometry(self) -> QRect:
        top_left = self.stack.mapTo(self.root, QPoint(0, 0))
        return QRect(top_left, self.stack.size())

    def _capture_geometry(self) -> QRect:
        if self._transition_geometry is not None:
            return QRect(self._transition_geometry)
        return self._surface_geometry()

    def _lock_surface_geometry(self) -> None:
        geometry = self._surface_geometry()
        self._transition_geometry = QRect(geometry)
        if self._surface.geometry() != geometry:
            self._surface.setGeometry(geometry)

    def _sync_surface_geometry(self) -> None:
        geometry = self._capture_geometry() if self._active else self._surface_geometry()
        if self._surface.geometry() != geometry:
            self._surface.setGeometry(geometry)

    def _release_surface_geometry(self) -> None:
        self._transition_geometry = None
        self._sync_surface_geometry()

    def _raise_transition_surface(self) -> None:
        self._surface.raise_()
        effects = getattr(self.window, "_nekro_effects", None)
        if isinstance(effects, QWidget):
            try:
                effects.raise_()
            except RuntimeError:
                pass

    def _capture_neutral_background(self) -> QPixmap:
        return self._snapshot_renderer.capture_neutral()

    def _capture_composite(self) -> QPixmap:
        return self._snapshot_renderer.capture_composite()

    def _suspend_presentation(self) -> None:
        card_fx = getattr(self.window, "_nekro_card_fx", None)
        suspend_cards = getattr(card_fx, "suspend_for_modal", None)
        if callable(suspend_cards) and not bool(getattr(card_fx, "_suspended", False)):
            try:
                suspend_cards()
                self._card_fx_suspended = True
            except RuntimeError:
                self._card_fx_suspended = False

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
        if quick is not None:
            try:
                quick.setProperty("animationRunning", False)
            except RuntimeError:
                pass

    def _resume_presentation(self) -> None:
        if self.background is not None:
            try:
                self.background._last_pointer_norm = None  # noqa: SLF001
            except (AttributeError, RuntimeError):
                pass

        pointer_timer = getattr(self.background, "_pointer_timer", None)
        if self._pointer_timer_was_active and pointer_timer is not None:
            try:
                if not pointer_timer.isActive():
                    pointer_timer.start()
            except RuntimeError:
                pass
        self._pointer_timer_was_active = False

        if self._card_fx_suspended:
            card_fx = getattr(self.window, "_nekro_card_fx", None)
            resume_cards = getattr(card_fx, "resume_from_modal", None)
            if callable(resume_cards):
                try:
                    resume_cards()
                except RuntimeError:
                    pass
        self._card_fx_suspended = False

        schedule_mask = getattr(self.background, "schedule_mask_update", None)
        if callable(schedule_mask):
            try:
                schedule_mask()
            except RuntimeError:
                pass

    def _begin_phase_badge_transition(self, old_text: str) -> None:
        badge = self._phase_badge
        self._phase_old_text = str(old_text or "")
        self._phase_new_text = ""
        self._phase_swapped = False
        self._phase_enter_start_ms = float(_HEADER_ENTER_START_MS)
        if badge is None or badge.graphicsEffect() is not None:
            self._phase_effect = None
            return

        badge.setText(self._phase_old_text)
        effect = QGraphicsOpacityEffect(badge)
        effect.setOpacity(1.0)
        badge.setGraphicsEffect(effect)
        self._phase_effect = effect

    def _set_phase_badge_target(self, new_text: str) -> None:
        self._phase_new_text = str(new_text or "")
        self._phase_enter_start_ms = max(
            float(_HEADER_ENTER_START_MS),
            self._elapsed_ms(),
        )

    def _update_phase_badge(self, elapsed_ms: float) -> None:
        badge = self._phase_badge
        effect = self._phase_effect
        if badge is None or effect is None:
            return
        try:
            if elapsed_ms < _HEADER_EXIT_START_MS:
                effect.setOpacity(1.0)
                return
            if elapsed_ms < _HEADER_EXIT_END_MS:
                progress = _segment_progress(
                    elapsed_ms,
                    _HEADER_EXIT_START_MS,
                    _HEADER_EXIT_END_MS,
                )
                effect.setOpacity(1.0 - float(self._exit_easing.valueForProgress(progress)))
                return

            effect.setOpacity(0.0)
            if not self._phase_new_text:
                return
            if not self._phase_swapped:
                badge.setText(self._phase_new_text)
                self._phase_swapped = True

            enter_start = self._phase_enter_start_ms
            enter_duration = max(
                1.0,
                float(_HEADER_ENTER_END_MS - _HEADER_ENTER_START_MS),
            )
            if elapsed_ms <= enter_start:
                return
            progress = _segment_progress(
                elapsed_ms,
                enter_start,
                enter_start + enter_duration,
            )
            effect.setOpacity(float(self._enter_easing.valueForProgress(progress)))
        except RuntimeError:
            self._phase_effect = None

    def _finish_phase_badge_transition(self) -> None:
        badge = self._phase_badge
        effect = self._phase_effect
        self._phase_effect = None
        if badge is not None:
            try:
                if self._phase_new_text:
                    badge.setText(self._phase_new_text)
                if effect is not None:
                    effect.setOpacity(1.0)
                    badge.setGraphicsEffect(None)
            except RuntimeError:
                pass
        self._phase_old_text = ""
        self._phase_new_text = ""
        self._phase_swapped = False
        self._phase_enter_start_ms = float(_HEADER_ENTER_START_MS)

    def _sync_toggle_to_stack(self) -> None:
        toggle = getattr(self.window, "_workspace_mode_switch", None)
        if toggle is None:
            return
        target = int(self.stack.currentIndex()) == 1
        try:
            if toggle.isChecked() != target:
                immediate = getattr(toggle, "set_checked_immediate", None)
                if callable(immediate):
                    immediate(target)
                else:
                    toggle.setChecked(target)
        except RuntimeError:
            pass

    def request_mode(self, index: int) -> None:
        index = 0 if int(index) <= 0 else 1
        if self._active:
            self._queued_index = index
            return
        if index == int(self.stack.currentIndex()):
            self._sync_toggle_to_stack()
            return
        if (
            not self.window.isVisible()
            or self.window.isMinimized()
            or self.stack.width() <= 0
            or self.stack.height() <= 0
        ):
            self._set_mode(index)
            return

        # Lock and capture the already-committed source page. No page geometry is
        # touched anywhere in this method.
        self._lock_surface_geometry()
        self._suspend_presentation()
        neutral = self._capture_neutral_background()
        outgoing = self._capture_composite()
        if outgoing.isNull():
            self._release_surface_geometry()
            self._resume_presentation()
            self._set_mode(index)
            return
        if neutral.isNull():
            neutral = QPixmap(outgoing)

        self._target_index = index
        self._queued_index = None
        self._outgoing = outgoing
        self._incoming = QPixmap()
        self._neutral = neutral
        self._incoming_enter_start_ms = float(_ENTER_START_MS)
        self._mode_switched = False
        self._live_ready = False
        self._active = True

        self._surface.begin(neutral, outgoing)
        self._surface.show()
        self._raise_transition_surface()
        self._surface.repaint()

        phase_old = ""
        badge = self._phase_badge
        if badge is not None:
            try:
                phase_old = str(badge.text())
            except RuntimeError:
                phase_old = ""
        self._begin_phase_badge_transition(phase_old)

        self._started_s = time.perf_counter()
        self._timer.setInterval(self._frame_interval_ms())
        self._timer.start()
        QTimer.singleShot(max(1, int(_EXIT_END_MS)), self._switch_target_under_cover)

    def _elapsed_ms(self) -> float:
        return max(0.0, (time.perf_counter() - self._started_s) * 1000.0)

    def _switch_target_under_cover(self) -> None:
        if not self._active or self._mode_switched:
            return
        if self.window.isMinimized() or not self.window.isVisible():
            self._finish_immediate()
            return

        # Establish a fully opaque neutral owner before changing currentIndex.
        self._surface.set_mix(
            outgoing_alpha=0.0,
            incoming_alpha=0.0,
            veil_alpha=0.0,
        )
        self._raise_transition_surface()
        self._surface.repaint()

        self._set_mode(self._target_index)
        self._mode_switched = True

        badge = self._phase_badge
        if badge is not None:
            try:
                self._set_phase_badge_target(str(badge.text()))
            except RuntimeError:
                pass

        # Allow the Show event itself to finish, but perform no layout activation.
        QTimer.singleShot(0, self._prepare_incoming)

    def _prepare_incoming(self) -> None:
        if not self._active or not self._mode_switched:
            return
        if (
            self.window.isMinimized()
            or not self.window.isVisible()
            or int(self.stack.currentIndex()) != self._target_index
        ):
            self._finish_immediate()
            return

        incoming = self._capture_composite()
        if incoming.isNull():
            self._finish_immediate()
            return

        self._incoming = incoming
        self._surface.set_incoming(incoming)
        self._raise_transition_surface()
        self._surface.repaint()
        self._incoming_enter_start_ms = max(
            float(_ENTER_START_MS),
            self._elapsed_ms(),
        )

        # Update the independent live Quick renderer only for the final handoff.
        # Its pixels are never part of either cached transition snapshot.
        schedule_mask = getattr(self.background, "schedule_mask_update", None)
        if callable(schedule_mask):
            try:
                schedule_mask()
            except RuntimeError:
                pass
        flush_geometry = getattr(self.background, "_flush_geometry", None)
        if callable(flush_geometry):
            try:
                flush_geometry()
            except RuntimeError:
                pass

        quick = getattr(self.background, "quick_window", None)
        if quick is None:
            self._live_ready = True
            return

        self._awaiting_quick_frame = True
        try:
            quick.update()
        except RuntimeError:
            self._awaiting_quick_frame = False
            self._live_ready = True
            return

        self._quick_sync_timeout.start(
            max(_QUICK_SYNC_TIMEOUT_MS, self._frame_interval_ms() * 4)
        )

    @Slot()
    def _on_quick_frame_swapped(self) -> None:
        if not self._active or not self._awaiting_quick_frame:
            return
        self._mark_live_ready()

    @Slot()
    def _mark_live_ready(self) -> None:
        if not self._active:
            return
        self._awaiting_quick_frame = False
        self._quick_sync_timeout.stop()
        self._live_ready = True

    def _mix_for_elapsed(self, elapsed_ms: float) -> tuple[float, float, float]:
        if elapsed_ms <= _HOLD_MS:
            outgoing_alpha = 1.0
        elif elapsed_ms >= _EXIT_END_MS:
            outgoing_alpha = 0.0
        else:
            progress = _segment_progress(elapsed_ms, _HOLD_MS, _EXIT_END_MS)
            outgoing_alpha = 1.0 - float(self._exit_easing.valueForProgress(progress))

        enter_start = self._incoming_enter_start_ms
        enter_end = enter_start + float(_ENTER_DURATION_MS)
        if self._incoming.isNull() or elapsed_ms <= enter_start:
            incoming_alpha = 0.0
        elif elapsed_ms >= enter_end:
            incoming_alpha = 1.0
        else:
            progress = _segment_progress(elapsed_ms, enter_start, enter_end)
            incoming_alpha = float(self._enter_easing.valueForProgress(progress))

        if elapsed_ms <= _VEIL_START_MS or elapsed_ms >= _VEIL_END_MS:
            veil_alpha = 0.0
        elif elapsed_ms <= _VEIL_PEAK_MS:
            rise = _segment_progress(elapsed_ms, _VEIL_START_MS, _VEIL_PEAK_MS)
            veil_alpha = _VEIL_MAX_OPACITY * _smoothstep(rise)
        else:
            fall = _segment_progress(elapsed_ms, _VEIL_PEAK_MS, _VEIL_END_MS)
            veil_alpha = _VEIL_MAX_OPACITY * (1.0 - _smoothstep(fall))

        if outgoing_alpha > 1e-4:
            incoming_alpha = 0.0

        return outgoing_alpha, incoming_alpha, veil_alpha

    def _advance(self) -> None:
        elapsed_ms = self._elapsed_ms()
        outgoing_alpha, incoming_alpha, veil_alpha = self._mix_for_elapsed(elapsed_ms)
        self._surface.set_mix(
            outgoing_alpha=outgoing_alpha,
            incoming_alpha=incoming_alpha,
            veil_alpha=veil_alpha,
        )
        self._update_phase_badge(elapsed_ms)

        finish_ms = max(
            float(_TOTAL_MS),
            self._incoming_enter_start_ms + float(_ENTER_DURATION_MS),
        )
        if (
            elapsed_ms >= finish_ms
            and not self._incoming.isNull()
            and self._live_ready
        ):
            self._finish_transition()

    def _clear_transition_state(self) -> None:
        self._surface.hide()
        self._surface.clear_frames()
        self._outgoing = QPixmap()
        self._incoming = QPixmap()
        self._neutral = QPixmap()
        self._active = False
        self._mode_switched = False
        self._live_ready = False
        self._release_surface_geometry()

    def _finish_transition(self) -> None:
        self._timer.stop()
        self._quick_sync_timeout.stop()
        self._awaiting_quick_frame = False
        if not self._active:
            return

        self._surface.set_mix(
            outgoing_alpha=0.0,
            incoming_alpha=1.0,
            veil_alpha=0.0,
        )
        self._raise_transition_surface()
        self._surface.repaint()

        self._clear_transition_state()
        self._finish_phase_badge_transition()
        self._resume_presentation()

        queued = self._queued_index
        self._queued_index = None
        if queued is not None and queued != int(self.stack.currentIndex()):
            QTimer.singleShot(0, lambda target=queued: self.request_mode(target))
        else:
            self._sync_toggle_to_stack()

    def _finish_immediate(self) -> None:
        self._timer.stop()
        self._quick_sync_timeout.stop()
        self._awaiting_quick_frame = False
        was_active = self._active
        if was_active and not self._mode_switched:
            try:
                self._set_mode(self._target_index)
            except RuntimeError:
                pass
        self._clear_transition_state()
        self._finish_phase_badge_transition()
        if was_active:
            self._resume_presentation()
        self._sync_toggle_to_stack()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        event_type = event.type()

        if watched is self.stack:
            if event_type in {QEvent.Type.Move, QEvent.Type.Resize, QEvent.Type.LayoutRequest}:
                if not self._active:
                    self._sync_surface_geometry()
            elif event_type == QEvent.Type.Show and not self._active:
                self._sync_surface_geometry()
            elif event_type == QEvent.Type.Hide and self._active:
                self._finish_immediate()

        elif watched is self.root:
            if event_type == QEvent.Type.Resize:
                if self._active:
                    self._finish_immediate()
                else:
                    self._sync_surface_geometry()
            elif event_type == QEvent.Type.Hide and self._active:
                self._finish_immediate()

        return False

    def cleanup(self) -> None:
        self._timer.stop()
        self._quick_sync_timeout.stop()
        self._awaiting_quick_frame = False

        try:
            self.stack.removeEventFilter(self)
        except RuntimeError:
            pass
        try:
            self.root.removeEventFilter(self)
        except RuntimeError:
            pass

        quick = getattr(self.background, "quick_window", None)
        if self._quick_frame_connected and quick is not None:
            try:
                quick.frameSwapped.disconnect(self._on_quick_frame_swapped)
            except (RuntimeError, TypeError):
                pass
        self._quick_frame_connected = False

        if self._active:
            if not self._mode_switched:
                try:
                    self._set_mode(self._target_index)
                except RuntimeError:
                    pass
            self._active = False
            self._finish_phase_badge_transition()
            self._resume_presentation()

        self._transition_geometry = None
        self._surface.hide()
        self._surface.clear_frames()


def install_workspace_transition(
    window: QMainWindow,
    visual: Any,
) -> WorkspaceTransitionController:
    existing = getattr(window, "_workspace_transition_controller", None)
    if isinstance(existing, WorkspaceTransitionController):
        return existing
    controller = WorkspaceTransitionController(window, visual)
    window._workspace_transition_controller = controller  # type: ignore[attr-defined]
    return controller
