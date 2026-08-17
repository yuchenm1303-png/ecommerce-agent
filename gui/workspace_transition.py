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


# The live workspace is never replaced by a transition-only copy.  The transition
# only raises the neutral Fuji backdrop, switches the real workspace while fully
# covered, then reveals the already-presented real target page.
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
_QUICK_SYNC_TIMEOUT_MS = 64


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


class _WorkspaceTransitionSurface(QWidget):
    """Input-blocking neutral cover; it never paints cards or workspace content."""

    def __init__(self, root: QWidget) -> None:
        super().__init__(root)
        self.setObjectName("workspaceTransitionSurface")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._backdrop = QPixmap()
        self._backdrop_alpha = 0.0
        self._veil_alpha = 0.0
        self.hide()

    def begin(self, backdrop: QPixmap) -> None:
        self._backdrop = _fit_frame(backdrop, self)
        self._backdrop_alpha = 0.0
        self._veil_alpha = 0.0
        self.update()

    def set_mix(self, *, backdrop_alpha: float, veil_alpha: float) -> None:
        backdrop_alpha = max(0.0, min(1.0, float(backdrop_alpha)))
        veil_alpha = max(0.0, min(_VEIL_MAX_OPACITY, float(veil_alpha)))
        changed = (
            abs(backdrop_alpha - self._backdrop_alpha) > 1e-5
            or abs(veil_alpha - self._veil_alpha) > 1e-5
        )
        self._backdrop_alpha = backdrop_alpha
        self._veil_alpha = veil_alpha
        if changed:
            self.update()

    def clear_frame(self) -> None:
        self._backdrop = QPixmap()
        self._backdrop_alpha = 0.0
        self._veil_alpha = 0.0
        self.update()

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

        if self._backdrop_alpha > 1e-5:
            painter.setOpacity(self._backdrop_alpha)
            if self._backdrop.isNull():
                painter.fillRect(self.rect(), QColor(23, 38, 58))
            else:
                logical = self._backdrop.deviceIndependentSize()
                if (
                    abs(float(logical.width()) - float(self.width())) <= 0.5
                    and abs(float(logical.height()) - float(self.height())) <= 0.5
                ):
                    painter.drawPixmap(0, 0, self._backdrop)
                else:
                    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
                    painter.drawPixmap(self.rect(), self._backdrop, self._backdrop.rect())
                    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)

        if self._veil_alpha > 1e-5:
            painter.setOpacity(self._veil_alpha)
            painter.fillRect(self.rect(), _VEIL_COLOR)
        painter.end()


class WorkspaceTransitionController(QObject):
    """Atomic Single/Batch handoff behind one neutral cover."""

    def __init__(self, window: QMainWindow, visual: Any) -> None:
        super().__init__(window)
        self.window = window
        self.visual = visual
        self.root = window.centralWidget()
        self.stack = getattr(window, "mode_stack", None)
        self.background = getattr(visual, "background", None)
        self._set_mode = getattr(window, "_set_workspace_mode", None)
        if self.root is None or not isinstance(self.stack, QStackedWidget) or not callable(self._set_mode):
            raise RuntimeError("workspace transition requires installed mode workspace")

        self._snapshot_renderer = WorkspaceTransitionSnapshotRenderer(window, visual, self.stack)
        self._surface = _WorkspaceTransitionSurface(self.root)
        self._sync_surface_geometry()

        self._active = False
        self._target_index = int(self.stack.currentIndex())
        self._queued_index: int | None = None
        self._started_s = 0.0
        self._switched = False
        self._reveal_ready = False
        self._reveal_start_ms = float(_ENTER_START_MS)

        self._pointer_timer_was_active = False
        self._card_fx_suspended = False

        self._phase_badge = getattr(window, "phase_badge", None)
        self._phase_effect: QGraphicsOpacityEffect | None = None
        self._phase_old_text = ""
        self._phase_new_text = ""
        self._phase_swapped = False

        self._awaiting_quick_frame = False
        self._quick_frame_connected = False
        self._quick_sync_timeout = QTimer(self)
        self._quick_sync_timeout.setSingleShot(True)
        self._quick_sync_timeout.setTimerType(Qt.TimerType.PreciseTimer)
        self._quick_sync_timeout.timeout.connect(self._mark_reveal_ready)

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

    def _sync_surface_geometry(self) -> None:
        geometry = self._surface_geometry()
        if self._surface.geometry() != geometry:
            self._surface.setGeometry(geometry)

    def _raise_transition_surface(self) -> None:
        self._surface.raise_()
        effects = getattr(self.window, "_nekro_effects", None)
        if isinstance(effects, QWidget):
            try:
                effects.raise_()
            except RuntimeError:
                pass

    def _badge_text(self) -> str:
        badge = self._phase_badge
        if badge is None:
            return ""
        try:
            return str(badge.text())
        except RuntimeError:
            return ""

    def _begin_phase_badge_exit(self) -> None:
        badge = self._phase_badge
        self._phase_old_text = self._badge_text()
        self._phase_new_text = ""
        self._phase_swapped = False
        if badge is None or badge.graphicsEffect() is not None:
            self._phase_effect = None
            return
        effect = QGraphicsOpacityEffect(badge)
        effect.setOpacity(1.0)
        badge.setGraphicsEffect(effect)
        self._phase_effect = effect

    def _capture_phase_badge_target(self) -> None:
        badge = self._phase_badge
        effect = self._phase_effect
        self._phase_new_text = self._badge_text()
        self._phase_swapped = True
        if badge is None or effect is None:
            return
        try:
            effect.setOpacity(0.0)
            badge.setText(self._phase_new_text)
        except RuntimeError:
            self._phase_effect = None

    def _update_phase_badge(self, elapsed_ms: float) -> None:
        badge = self._phase_badge
        effect = self._phase_effect
        if badge is None or effect is None:
            return
        try:
            if not self._phase_swapped:
                if elapsed_ms <= _HEADER_EXIT_START_MS:
                    effect.setOpacity(1.0)
                elif elapsed_ms >= _HEADER_EXIT_END_MS:
                    effect.setOpacity(0.0)
                else:
                    progress = _segment_progress(
                        elapsed_ms,
                        _HEADER_EXIT_START_MS,
                        _HEADER_EXIT_END_MS,
                    )
                    effect.setOpacity(1.0 - float(self._exit_easing.valueForProgress(progress)))
                return

            if elapsed_ms <= _HEADER_ENTER_START_MS:
                effect.setOpacity(0.0)
            elif elapsed_ms >= _HEADER_ENTER_END_MS:
                effect.setOpacity(1.0)
            else:
                progress = _segment_progress(
                    elapsed_ms,
                    _HEADER_ENTER_START_MS,
                    _HEADER_ENTER_END_MS,
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
                elif self._phase_old_text:
                    badge.setText(self._phase_old_text)
                if effect is not None:
                    effect.setOpacity(1.0)
                    badge.setGraphicsEffect(None)
            except RuntimeError:
                pass
        self._phase_old_text = ""
        self._phase_new_text = ""
        self._phase_swapped = False

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

    def _prepare_target_under_cover(self) -> None:
        layout_keeper = getattr(self.window, "_workspace_layout_commit", None)
        prepare_page = getattr(layout_keeper, "prepare_page", None)
        if callable(prepare_page):
            try:
                prepare_page(self._target_index)
            except RuntimeError:
                pass

    def _request_presented_target_frame(self) -> None:
        flush_geometry = getattr(self.background, "_flush_geometry", None)
        if callable(flush_geometry):
            try:
                flush_geometry()
            except RuntimeError:
                pass

        quick = getattr(self.background, "quick_window", None)
        if quick is None:
            self._mark_reveal_ready()
            return

        self._awaiting_quick_frame = True
        try:
            quick.update()
        except RuntimeError:
            self._awaiting_quick_frame = False
            self._mark_reveal_ready()
            return

        self._quick_sync_timeout.start(
            max(_QUICK_SYNC_TIMEOUT_MS, self._frame_interval_ms() * 3)
        )

    def _switch_under_cover(self) -> None:
        if not self._active or self._switched:
            return

        # This repaint is the atomic barrier.  No live layout, card state or
        # currentIndex mutation is permitted before the neutral cover is opaque.
        self._surface.set_mix(backdrop_alpha=1.0, veil_alpha=_VEIL_MAX_OPACITY)
        self._raise_transition_surface()
        self._surface.repaint()

        self._switched = True
        self._suspend_presentation()
        self._prepare_target_under_cover()
        self._set_mode(self._target_index)
        self._capture_phase_badge_target()
        self._raise_transition_surface()

        schedule_mask = getattr(self.background, "schedule_mask_update", None)
        if callable(schedule_mask):
            try:
                schedule_mask()
            except RuntimeError:
                pass
        self._request_presented_target_frame()

    @Slot()
    def _on_quick_frame_swapped(self) -> None:
        if not self._active or not self._awaiting_quick_frame:
            return
        self._awaiting_quick_frame = False
        self._quick_sync_timeout.stop()
        self._mark_reveal_ready()

    def _mark_reveal_ready(self) -> None:
        if not self._active or not self._switched or self._reveal_ready:
            return
        self._awaiting_quick_frame = False
        self._quick_sync_timeout.stop()
        self._reveal_ready = True
        self._reveal_start_ms = max(float(_ENTER_START_MS), self._elapsed_ms())

        # Restore the live presentation while it is still fully hidden.  The
        # reveal therefore exposes only the ordinary steady-state target UI.
        self._resume_presentation()
        self._raise_transition_surface()
        self._surface.repaint()

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

        self._sync_surface_geometry()
        backdrop = self._snapshot_renderer.capture_neutral()

        self._target_index = index
        self._queued_index = None
        self._switched = False
        self._reveal_ready = False
        self._reveal_start_ms = float(_ENTER_START_MS)
        self._active = True

        # Show a transparent, input-blocking surface first.  It paints no cards,
        # changes no layout and leaves the currently visible real page untouched.
        self._surface.begin(backdrop)
        self._surface.show()
        self._raise_transition_surface()
        self._surface.repaint()
        self._begin_phase_badge_exit()

        self._started_s = time.perf_counter()
        self._timer.setInterval(self._frame_interval_ms())
        self._timer.start()

    def _elapsed_ms(self) -> float:
        return max(0.0, (time.perf_counter() - self._started_s) * 1000.0)

    def _cover_mix(self, elapsed_ms: float) -> tuple[float, float]:
        if not self._switched:
            if elapsed_ms <= _HOLD_MS:
                backdrop_alpha = 0.0
            elif elapsed_ms >= _EXIT_END_MS:
                backdrop_alpha = 1.0
            else:
                progress = _segment_progress(elapsed_ms, _HOLD_MS, _EXIT_END_MS)
                backdrop_alpha = float(self._exit_easing.valueForProgress(progress))
        elif not self._reveal_ready:
            backdrop_alpha = 1.0
        else:
            reveal_end = self._reveal_start_ms + float(_ENTER_DURATION_MS)
            if elapsed_ms <= self._reveal_start_ms:
                backdrop_alpha = 1.0
            elif elapsed_ms >= reveal_end:
                backdrop_alpha = 0.0
            else:
                progress = _segment_progress(elapsed_ms, self._reveal_start_ms, reveal_end)
                backdrop_alpha = 1.0 - float(self._enter_easing.valueForProgress(progress))

        if elapsed_ms <= _VEIL_START_MS or elapsed_ms >= _VEIL_END_MS:
            veil_alpha = 0.0
        elif elapsed_ms <= _VEIL_PEAK_MS:
            rise = _segment_progress(elapsed_ms, _VEIL_START_MS, _VEIL_PEAK_MS)
            veil_alpha = _VEIL_MAX_OPACITY * _smoothstep(rise)
        else:
            fall = _segment_progress(elapsed_ms, _VEIL_PEAK_MS, _VEIL_END_MS)
            veil_alpha = _VEIL_MAX_OPACITY * (1.0 - _smoothstep(fall))
        return backdrop_alpha, veil_alpha

    def _advance(self) -> None:
        if not self._active:
            return
        elapsed_ms = self._elapsed_ms()

        if not self._switched and elapsed_ms >= _EXIT_END_MS:
            self._switch_under_cover()

        backdrop_alpha, veil_alpha = self._cover_mix(elapsed_ms)
        self._surface.set_mix(
            backdrop_alpha=backdrop_alpha,
            veil_alpha=veil_alpha,
        )
        self._update_phase_badge(elapsed_ms)

        if self._reveal_ready:
            finish_ms = self._reveal_start_ms + float(_ENTER_DURATION_MS)
            if elapsed_ms >= finish_ms:
                self._finish_transition()

    def _finish_transition(self) -> None:
        self._timer.stop()
        self._quick_sync_timeout.stop()
        self._awaiting_quick_frame = False
        if not self._active:
            return

        if self._card_fx_suspended or self._pointer_timer_was_active:
            self._resume_presentation()

        self._surface.set_mix(backdrop_alpha=0.0, veil_alpha=0.0)
        self._surface.repaint()
        self._surface.hide()
        self._surface.clear_frame()
        self._active = False
        self._switched = False
        self._reveal_ready = False
        self._finish_phase_badge_transition()

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
        if self._card_fx_suspended or self._pointer_timer_was_active:
            self._resume_presentation()
        self._surface.hide()
        self._surface.clear_frame()
        self._active = False
        self._switched = False
        self._reveal_ready = False
        self._finish_phase_badge_transition()
        self._sync_toggle_to_stack()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        event_type = event.type()
        if watched is self.stack:
            if event_type in {QEvent.Type.Move, QEvent.Type.Resize, QEvent.Type.Show}:
                if self._active and event_type in {QEvent.Type.Move, QEvent.Type.Resize}:
                    self._finish_immediate()
                else:
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

        if self._active and (self._card_fx_suspended or self._pointer_timer_was_active):
            self._resume_presentation()
        self._active = False
        self._finish_phase_badge_transition()
        self._surface.hide()
        self._surface.clear_frame()


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
