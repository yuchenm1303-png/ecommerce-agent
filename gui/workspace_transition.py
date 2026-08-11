from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPoint, QPointF, QRect, Qt, QTimer
from PySide6.QtGui import QPainter, QPixmap, QRegion
from PySide6.QtWidgets import QMainWindow, QStackedWidget, QWidget


_PREPARE_MS = 30
_TRANSITION_MS = 270
_SLIDE_PX = 18.0


def _workspace_easing() -> QEasingCurve:
    """Fast, polished ease-out used by modern workspace navigation."""

    curve = QEasingCurve(QEasingCurve.Type.BezierSpline)
    curve.addCubicBezierSegment(
        QPointF(0.22, 1.0),
        QPointF(0.36, 1.0),
        QPointF(1.0, 1.0),
    )
    return curve


def _empty_frame(widget: QWidget) -> QPixmap:
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


class _WorkspaceTransitionSurface(QWidget):
    """Animate only two cached workspace frames, never the live QWidget trees."""

    def __init__(self, stack: QStackedWidget) -> None:
        super().__init__(stack)
        self.setObjectName("workspaceTransitionSurface")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._from = QPixmap()
        self._to = QPixmap()
        self._progress = 0.0
        self._direction = 1
        self._capture_suppressed = False
        self.hide()

    def set_capture_suppressed(self, suppressed: bool) -> None:
        suppressed = bool(suppressed)
        if suppressed == self._capture_suppressed:
            return
        if suppressed:
            self._capture_suppressed = True
            self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
            return
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self._capture_suppressed = False

    def set_hold_frame(self, frame: QPixmap, direction: int) -> None:
        self._from = _fit_frame(frame, self)
        self._to = QPixmap()
        self._progress = 0.0
        self._direction = 1 if direction >= 0 else -1
        self.update()

    def set_transition_frames(
        self,
        old_frame: QPixmap,
        new_frame: QPixmap,
        direction: int,
    ) -> None:
        self._from = _fit_frame(old_frame, self)
        self._to = _fit_frame(new_frame, self)
        self._direction = 1 if direction >= 0 else -1
        self._progress = 0.0
        self.update()

    def set_progress(self, progress: float) -> None:
        progress = max(0.0, min(1.0, float(progress)))
        if abs(progress - self._progress) <= 1e-6:
            return
        self._progress = progress
        self.update()

    def clear_frames(self) -> None:
        self._from = QPixmap()
        self._to = QPixmap()
        self._progress = 0.0
        self.update()

    def _draw_frame(self, painter: QPainter, frame: QPixmap, x: float = 0.0) -> None:
        if frame.isNull():
            return
        logical = frame.deviceIndependentSize()
        if (
            abs(float(logical.width()) - float(self.width())) <= 0.5
            and abs(float(logical.height()) - float(self.height())) <= 0.5
        ):
            painter.drawPixmap(QPointF(x, 0.0), frame)
            return
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        target = self.rect()
        target.translate(int(round(x)), 0)
        painter.drawPixmap(target, frame, frame.rect())
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        if self._capture_suppressed:
            return

        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        self._draw_frame(painter, self._from)

        if not self._to.isNull() and self._progress > 0.0:
            if self._progress >= 1.0 - 1e-6:
                painter.setOpacity(1.0)
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
                self._draw_frame(painter, self._to)
            else:
                # The old workspace remains a stable full-frame backing layer.
                # The new workspace glides in by only 18px while dissolving over it,
                # so there are no exposed strips and no live-widget repaint storms.
                incoming_x = self._direction * _SLIDE_PX * (1.0 - self._progress)
                alpha = self._progress * self._progress * (3.0 - 2.0 * self._progress)
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
                painter.setOpacity(alpha)
                self._draw_frame(painter, self._to, incoming_x)

        painter.end()


class WorkspaceTransitionController(QObject):
    """Snapshot-based Single/Batch workspace transition.

    The business state machine still owns ``mode_stack`` and mode selection.
    This controller changes only presentation: it holds the outgoing workspace
    while the target page, layout and native glass mask settle underneath, then
    animates two precomposited pixmaps. No complex QWidget subtree is animated.
    """

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

        self._surface = _WorkspaceTransitionSurface(self.stack)
        self._surface.setGeometry(self.stack.rect())
        self._surface.hide()

        self._active = False
        self._target_index = int(self.stack.currentIndex())
        self._direction = 1
        self._queued_index: int | None = None
        self._outgoing = QPixmap()
        self._started_s = 0.0
        self._pointer_timer_was_active = False
        self._card_fx_suspended = False

        self._easing = _workspace_easing()
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._advance)
        self.stack.installEventFilter(self)
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

    def _sync_surface_geometry(self) -> None:
        geometry = self.stack.rect()
        if self._surface.geometry() != geometry:
            self._surface.setGeometry(geometry)

    def _render_stack_widgets(self) -> QPixmap:
        if self.stack.width() <= 0 or self.stack.height() <= 0:
            return QPixmap()
        frame = _empty_frame(self.stack)
        self._surface.set_capture_suppressed(True)
        try:
            self.stack.render(
                frame,
                QPoint(0, 0),
                QRegion(),
                QWidget.RenderFlag.DrawChildren,
            )
        finally:
            self._surface.set_capture_suppressed(False)
        return frame

    def _capture_quick_for_stack(self) -> QPixmap:
        quick = getattr(self.background, "quick_window", None)
        if quick is None:
            return QPixmap()
        try:
            image = quick.grabWindow()
        except RuntimeError:
            return QPixmap()
        if image.isNull():
            return QPixmap()

        full = _fit_frame(QPixmap.fromImage(image), self.root)
        if full.isNull():
            return QPixmap()

        top_left = self.stack.mapTo(self.root, QPoint(0, 0))
        dpr = max(1.0, float(full.devicePixelRatio()))
        pixel_rect = QRect(
            int(round(top_left.x() * dpr)),
            int(round(top_left.y() * dpr)),
            max(1, int(round(self.stack.width() * dpr))),
            max(1, int(round(self.stack.height() * dpr))),
        )
        cropped = full.copy(pixel_rect)
        cropped.setDevicePixelRatio(dpr)
        return _fit_frame(cropped, self.stack)

    def _capture_composite(self) -> QPixmap:
        quick_frame = self._capture_quick_for_stack()
        widget_frame = self._render_stack_widgets()
        if quick_frame.isNull():
            return _fit_frame(widget_frame, self.stack)
        if widget_frame.isNull():
            return _fit_frame(quick_frame, self.stack)

        result = _empty_frame(self.stack)
        painter = QPainter(result)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.drawPixmap(0, 0, _fit_frame(quick_frame, self.stack))
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.drawPixmap(0, 0, _fit_frame(widget_frame, self.stack))
        painter.end()
        return result

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
        self._suspend_presentation()

        outgoing = self._capture_composite()
        if outgoing.isNull():
            self._resume_presentation()
            self._set_mode(index)
            return

        current = int(self.stack.currentIndex())
        self._direction = 1 if index > current else -1
        self._target_index = index
        self._queued_index = None
        self._outgoing = outgoing
        self._active = True

        self._surface.set_hold_frame(outgoing, self._direction)
        self._surface.show()
        self._surface.raise_()
        self._surface.repaint()

        # Change the real state immediately under the frozen frame. Header copy and
        # switch state therefore respond in the same click turn, while the heavy
        # workspace is allowed one short settle window before it becomes visible.
        self._set_mode(index)
        current_page = self.stack.currentWidget()
        page_layout = current_page.layout() if current_page is not None else None
        if page_layout is not None:
            page_layout.activate()

        schedule_mask = getattr(self.background, "schedule_mask_update", None)
        if callable(schedule_mask):
            schedule_mask()
        QTimer.singleShot(_PREPARE_MS, self._prepare_incoming)

    def _prepare_incoming(self) -> None:
        if not self._active:
            return
        if (
            self.window.isMinimized()
            or not self.window.isVisible()
            or int(self.stack.currentIndex()) != self._target_index
        ):
            self._finish_immediate()
            return

        # Force any coalesced glass geometry work to land before the target frame
        # is sampled. The transition surface is still holding the exact old frame,
        # so this preparation is never visible.
        flush_geometry = getattr(self.background, "_flush_geometry", None)
        if callable(flush_geometry):
            try:
                flush_geometry()
            except RuntimeError:
                pass

        quick = getattr(self.background, "quick_window", None)
        if quick is not None:
            try:
                quick.update()
            except RuntimeError:
                pass

        incoming = self._capture_composite()
        if incoming.isNull():
            self._finish_immediate()
            return

        self._surface.set_transition_frames(
            self._outgoing,
            incoming,
            self._direction,
        )
        self._surface.raise_()
        self._surface.repaint()
        self._started_s = time.perf_counter()
        self._timer.setInterval(self._frame_interval_ms())
        self._timer.start()

    def _advance(self) -> None:
        elapsed_s = max(0.0, time.perf_counter() - self._started_s)
        linear = min(1.0, elapsed_s / max(0.001, _TRANSITION_MS / 1000.0))
        eased = float(self._easing.valueForProgress(linear))
        self._surface.set_progress(eased)
        if linear >= 1.0:
            self._finish_transition()

    def _finish_transition(self) -> None:
        self._timer.stop()
        if not self._active:
            return
        self._surface.set_progress(1.0)
        self._surface.repaint()
        self._surface.hide()
        self._surface.clear_frames()
        self._outgoing = QPixmap()
        self._active = False
        self._resume_presentation()

        queued = self._queued_index
        self._queued_index = None
        if queued is not None and queued != int(self.stack.currentIndex()):
            QTimer.singleShot(0, lambda target=queued: self.request_mode(target))
        else:
            self._sync_toggle_to_stack()

    def _finish_immediate(self) -> None:
        self._timer.stop()
        self._surface.hide()
        self._surface.clear_frames()
        self._outgoing = QPixmap()
        was_active = self._active
        self._active = False
        if was_active:
            self._resume_presentation()
        self._sync_toggle_to_stack()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self.stack:
            if event.type() == QEvent.Type.Resize:
                self._sync_surface_geometry()
            elif event.type() == QEvent.Type.Hide and self._active:
                self._finish_immediate()
        return False

    def cleanup(self) -> None:
        self._timer.stop()
        try:
            self.stack.removeEventFilter(self)
        except RuntimeError:
            pass
        if self._active:
            self._active = False
            self._resume_presentation()
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
