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

from .workspace_transition_snapshot import WorkspaceTransitionBackdropRenderer


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
    """Fit the geometry-independent backdrop without non-uniform distortion."""

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
    """A geometry-neutral cover over modeStack.

    The surface never contains a rasterized QWidget page. It paints only the neutral
    wallpaper backdrop and fades that cover with one opacity effect. The live outgoing
    page therefore remains the actual page until it is fully hidden; the live incoming
    page is revealed only after its layout and Quick glass have settled underneath.
    """

    def __init__(self, root: QWidget) -> None:
        super().__init__(root)
        self.setObjectName("workspaceTransitionSurface")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._backdrop = QPixmap()
        self._veil_alpha = 0.0
        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)
        self.hide()

    def begin(self, backdrop: QPixmap) -> None:
        self._backdrop = _fit_frame(backdrop, self)
        self._veil_alpha = 0.0
        self._opacity.setOpacity(0.0)
        self.update()

    def set_cover(self, *, opacity: float, veil_alpha: float) -> None:
        opacity = max(0.0, min(1.0, float(opacity)))
        veil_alpha = max(0.0, min(_VEIL_MAX_OPACITY, float(veil_alpha)))
        changed = (
            abs(float(self._opacity.opacity()) - opacity) > 1e-5
            or abs(self._veil_alpha - veil_alpha) > 1e-5
        )
        self._opacity.setOpacity(opacity)
        self._veil_alpha = veil_alpha
        if changed:
            self.update()

    def clear(self) -> None:
        self._opacity.setOpacity(0.0)
        self._veil_alpha = 0.0
        self._backdrop = QPixmap()
        self.update()

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(), QColor(23, 38, 58))
        if not self._backdrop.isNull():
            painter.drawPixmap(0, 0, _fit_frame(self._backdrop, self))
        if self._veil_alpha > 1e-5:
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            painter.setOpacity(self._veil_alpha)
            painter.fillRect(self.rect(), _VEIL_COLOR)
        painter.end()


class WorkspaceTransitionController(QObject):
    """Single/Batch handoff without rebuilding either workspace while visible.

    The unified Quick scene and the legacy QWidget fallback share one timing model.
    Quick presentation fades the existing QQuickItem to the live wallpaper, commits
    the hidden target workspace only while that scene is fully transparent, refreshes
    the bridge once from settled geometry, and then fades the new scene back in.

    The legacy QWidget fallback keeps the neutral cover path. Neither presentation
    owner rasterizes or morphs a workspace during the visible part of the switch.
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

        self._backdrop_renderer = WorkspaceTransitionBackdropRenderer(window, visual, self.stack)
        self._surface = _WorkspaceTransitionSurface(self.root)
        self._active = False
        self._quick_scene_transition = False
        self._transition_geometry: QRect | None = None
        self._sync_surface_geometry()
        self._surface.hide()

        self._target_index = int(self.stack.currentIndex())
        self._queued_index: int | None = None
        self._started_s = 0.0
        self._incoming_enter_start_ms = float(_ENTER_START_MS)
        self._mode_switched = False
        self._live_ready = False

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
        return self._backdrop_renderer.capture_neutral()

    def _static_qml_controller(self):  # noqa: ANN202
        controller = getattr(self.window, "_static_qml_view_controller", None)
        try:
            if controller is not None and controller.static_active:
                return controller
        except (AttributeError, RuntimeError):
            pass
        return None

    def _static_qml_active(self) -> bool:
        return self._static_qml_controller() is not None

    def _static_qml_item(self):  # noqa: ANN202
        controller = self._static_qml_controller()
        item = getattr(controller, "item", None) if controller is not None else None
        return item

    def _set_static_scene_opacity(self, opacity: float) -> None:
        item = self._static_qml_item()
        if item is None:
            return
        try:
            item.setOpacity(max(0.0, min(1.0, float(opacity))))
        except RuntimeError:
            pass

    def _commit_target_layout(self, index: int) -> None:
        owner = getattr(self.window, "_workspace_layout_commit", None)
        prepare = getattr(owner, "prepare_page", None)
        if callable(prepare):
            try:
                prepare(int(index))
            except RuntimeError:
                pass

    def _refresh_static_scene_now(self) -> None:
        controller = self._static_qml_controller()
        if controller is None:
            return
        bridge = getattr(controller, "bridge", None)
        refresh = getattr(bridge, "refresh", None)
        if callable(refresh):
            try:
                refresh()
            except RuntimeError:
                pass
        activity = getattr(controller, "activity_presence", None)
        refresh_activity = getattr(activity, "refresh", None)
        if callable(refresh_activity):
            try:
                refresh_activity()
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

    def _start_quick_transition(self, index: int) -> bool:
        if self._static_qml_item() is None:
            return False

        self._target_index = index
        self._queued_index = None
        self._incoming_enter_start_ms = float(_ENTER_START_MS)
        self._mode_switched = False
        self._live_ready = False
        self._quick_scene_transition = True
        self._active = True
        self._set_static_scene_opacity(1.0)

        self._started_s = time.perf_counter()
        self._timer.setInterval(self._frame_interval_ms())
        self._timer.start()
        QTimer.singleShot(max(1, int(_EXIT_END_MS)), self._switch_target_under_cover)
        return True

    def request_mode(self, index: int) -> None:
        index = 0 if int(index) <= 0 else 1
        if self._active:
            self._queued_index = index
            return
        if index == int(self.stack.currentIndex()):
            self._sync_toggle_to_stack()
            return

        # The unified QML scene must keep the original transition choreography too.
        # Fade the already-rendered scene out first; only once it is fully transparent
        # may QWidget change mode and settle hidden geometry. This keeps the switch
        # smooth without exposing intermediate layouts or restoring page snapshots.
        if self._static_qml_active():
            if self._start_quick_transition(index):
                return
            self._commit_target_layout(index)
            self._set_mode(index)
            self._sync_toggle_to_stack()
            return

        if (
            not self.window.isVisible()
            or self.window.isMinimized()
            or self.stack.width() <= 0
            or self.stack.height() <= 0
        ):
            self._commit_target_layout(index)
            self._set_mode(index)
            return

        # Do not touch page geometry and do not rasterize the outgoing page. The
        # cover starts fully transparent, so the pixels visible after the click are
        # still the exact live layout that was visible before the click.
        self._lock_surface_geometry()
        backdrop = self._capture_neutral_background()
        self._target_index = index
        self._queued_index = None
        self._incoming_enter_start_ms = float(_ENTER_START_MS)
        self._mode_switched = False
        self._live_ready = False
        self._active = True

        self._surface.begin(backdrop)
        self._surface.show()
        self._raise_transition_surface()
        self._surface.set_cover(opacity=0.0, veil_alpha=0.0)
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

        # The mode changes only at a fully hidden presentation boundary. Quick uses
        # its scene opacity; the QWidget fallback uses the neutral cover.
        if self._quick_scene_transition:
            self._set_static_scene_opacity(0.0)
        else:
            self._surface.set_cover(opacity=1.0, veil_alpha=0.0)
            self._raise_transition_surface()
            self._surface.repaint()

        self._set_mode(self._target_index)
        self._mode_switched = True

        if not self._quick_scene_transition:
            badge = self._phase_badge
            if badge is not None:
                try:
                    self._set_phase_badge_target(str(badge.text()))
                except RuntimeError:
                    pass

        # Let QStackedWidget finish the Show/currentChanged cascade, then settle the
        # live target page while the presentation is still fully hidden.
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

        self._commit_target_layout(self._target_index)
        if self._quick_scene_transition:
            # Consume exactly one settled QWidget generation before asking Quick for
            # the frame that will be revealed. No child-event refresh storm is needed.
            self._refresh_static_scene_now()

        self._incoming_enter_start_ms = max(
            float(_ENTER_START_MS),
            self._elapsed_ms(),
        )

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

    def _cover_for_elapsed(self, elapsed_ms: float) -> tuple[float, float]:
        if not self._mode_switched:
            if elapsed_ms <= _HOLD_MS:
                cover_alpha = 0.0
            elif elapsed_ms >= _EXIT_END_MS:
                cover_alpha = 1.0
            else:
                progress = _segment_progress(elapsed_ms, _HOLD_MS, _EXIT_END_MS)
                cover_alpha = float(self._exit_easing.valueForProgress(progress))
        elif not self._live_ready:
            cover_alpha = 1.0
        else:
            enter_start = self._incoming_enter_start_ms
            enter_end = enter_start + float(_ENTER_DURATION_MS)
            if elapsed_ms <= enter_start:
                cover_alpha = 1.0
            elif elapsed_ms >= enter_end:
                cover_alpha = 0.0
            else:
                progress = _segment_progress(elapsed_ms, enter_start, enter_end)
                cover_alpha = 1.0 - float(self._enter_easing.valueForProgress(progress))

        if elapsed_ms <= _VEIL_START_MS or elapsed_ms >= _VEIL_END_MS:
            veil_alpha = 0.0
        elif elapsed_ms <= _VEIL_PEAK_MS:
            rise = _segment_progress(elapsed_ms, _VEIL_START_MS, _VEIL_PEAK_MS)
            veil_alpha = _VEIL_MAX_OPACITY * _smoothstep(rise)
        else:
            fall = _segment_progress(elapsed_ms, _VEIL_PEAK_MS, _VEIL_END_MS)
            veil_alpha = _VEIL_MAX_OPACITY * (1.0 - _smoothstep(fall))

        return cover_alpha, veil_alpha

    def _advance(self) -> None:
        elapsed_ms = self._elapsed_ms()
        cover_alpha, veil_alpha = self._cover_for_elapsed(elapsed_ms)
        if self._quick_scene_transition:
            self._set_static_scene_opacity(1.0 - cover_alpha)
        else:
            self._surface.set_cover(opacity=cover_alpha, veil_alpha=veil_alpha)
            self._update_phase_badge(elapsed_ms)

        finish_ms = max(
            float(_TOTAL_MS),
            self._incoming_enter_start_ms + float(_ENTER_DURATION_MS),
        )
        if (
            self._mode_switched
            and self._live_ready
            and elapsed_ms >= finish_ms
        ):
            self._finish_transition()

    def _clear_transition_state(self) -> None:
        if self._quick_scene_transition:
            self._set_static_scene_opacity(1.0)
        else:
            self._surface.hide()
            self._surface.clear()
        self._active = False
        self._quick_scene_transition = False
        self._mode_switched = False
        self._live_ready = False
        self._release_surface_geometry()

    def _finish_transition(self) -> None:
        self._timer.stop()
        self._quick_sync_timeout.stop()
        self._awaiting_quick_frame = False
        if not self._active:
            return

        if self._quick_scene_transition:
            self._set_static_scene_opacity(1.0)
        else:
            self._surface.set_cover(opacity=0.0, veil_alpha=0.0)
        self._clear_transition_state()
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
        was_active = self._active
        if was_active and not self._mode_switched:
            try:
                self._commit_target_layout(self._target_index)
                self._set_mode(self._target_index)
                if self._quick_scene_transition:
                    self._refresh_static_scene_now()
            except RuntimeError:
                pass
        self._clear_transition_state()
        self._finish_phase_badge_transition()
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
        if self._quick_scene_transition:
            self._set_static_scene_opacity(1.0)

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

        if self._active and not self._mode_switched:
            try:
                self._commit_target_layout(self._target_index)
                self._set_mode(self._target_index)
            except RuntimeError:
                pass

        self._active = False
        self._quick_scene_transition = False
        self._transition_geometry = None
        self._finish_phase_badge_transition()
        self._surface.hide()
        self._surface.clear()


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
