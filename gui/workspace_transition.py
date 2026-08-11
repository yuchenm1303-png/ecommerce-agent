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
    QRectF,
    Qt,
    QTimer,
    Slot,
)
from PySide6.QtGui import QColor, QPainter, QPixmap, QRegion
from PySide6.QtWidgets import QGraphicsOpacityEffect, QMainWindow, QStackedWidget, QWidget

from .native_background import _OVERSCAN


# Large top-level workspaces get more time than the tiny 300 ms switch control.
# The old workspace is fully gone before the new one is allowed to become readable.
_PREPARE_MS = 30
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

# Quick uses the threaded render loop in the formal runner. The new card geometry
# must reach at least one presented Quick frame before the incoming composite is
# sampled; otherwise the QWidget page can be combined with the previous glass mask.
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
    # CSS cubic-bezier(.4, 0, 1, 1): old content accelerates away.
    return _cubic_bezier(0.40, 0.00, 1.00, 1.00)


def _enter_easing() -> QEasingCurve:
    # Fast establishment, long gentle settle for the incoming workspace.
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
    """Root-level opaque owner for the complete modeStack image during motion.

    The surface intentionally lives under the central widget, not under
    QStackedWidget. Switching the current page can raise children inside the stack,
    but can never raise them above this sibling overlay. That makes one object the
    sole owner of every visible modeStack pixel until the final handoff.
    """

    def __init__(self, root: QWidget) -> None:
        super().__init__(root)
        self.setObjectName("workspaceTransitionSurface")
        # Block pointer input to the hidden live workspace while the header switch
        # remains available above/outside this geometry.
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

    def _draw_fitted(self, painter: QPainter, frame: QPixmap) -> None:
        if frame.isNull():
            painter.fillRect(self.rect(), QColor(23, 38, 58))
            return
        logical = frame.deviceIndependentSize()
        if (
            abs(float(logical.width()) - float(self.width())) <= 0.5
            and abs(float(logical.height()) - float(self.height())) <= 0.5
        ):
            painter.drawPixmap(0, 0, frame)
            return

        # Geometry changes during an active transition normally snap immediately,
        # so this is only a defensive fallback.
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawPixmap(self.rect(), frame, frame.rect())
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        painter = QPainter(self)

        # CompositionMode_Source is deliberate: the neutral Fuji base writes
        # opaque pixels into the translucent top-level backing store. Nothing from
        # the live QStackedWidget or native Quick glass can leak through.
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        self._draw_fitted(painter, self._neutral)

        if self._outgoing_alpha > 1e-5 and not self._outgoing.isNull():
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            painter.setOpacity(self._outgoing_alpha)
            self._draw_fitted(painter, self._outgoing)

        if self._incoming_alpha > 1e-5 and not self._incoming.isNull():
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            painter.setOpacity(self._incoming_alpha)
            self._draw_fitted(painter, self._incoming)

        if self._veil_alpha > 1e-5:
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            painter.setOpacity(self._veil_alpha)
            painter.fillRect(self.rect(), _VEIL_COLOR)

        painter.end()


class WorkspaceTransitionController(QObject):
    """Presentation-only Single/Batch top-level fade-through."""

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

        # Root-level ownership is the key anti-overlap invariant. A current page
        # can be raised inside QStackedWidget without ever overtaking this overlay.
        self._surface = _WorkspaceTransitionSurface(self.root)
        self._sync_surface_geometry()
        self._surface.hide()

        self._active = False
        self._target_index = int(self.stack.currentIndex())
        self._queued_index: int | None = None
        self._outgoing = QPixmap()
        self._incoming = QPixmap()
        self._neutral = QPixmap()
        self._wallpaper = self._load_wallpaper()
        self._started_s = 0.0
        self._incoming_enter_start_ms = float(_ENTER_START_MS)
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
        self._quick_sync_timeout.timeout.connect(self._capture_incoming_after_quick_sync)

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

    def _load_wallpaper(self) -> QPixmap:
        path = getattr(self.background, "_sharp_path", None)
        if path is None:
            return QPixmap()
        try:
            return QPixmap(str(path))
        except RuntimeError:
            return QPixmap()

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

        # Sakura is an independent ambient layer. Keep it above the workspace
        # transition so the background world never appears frozen.
        effects = getattr(self.window, "_nekro_effects", None)
        if isinstance(effects, QWidget):
            try:
                effects.raise_()
            except RuntimeError:
                pass

    def _render_current_page(self) -> QPixmap:
        """Render exactly one QStackedWidget page into stack coordinates."""

        page = self.stack.currentWidget()
        if (
            page is None
            or self.stack.width() <= 0
            or self.stack.height() <= 0
            or page.width() <= 0
            or page.height() <= 0
        ):
            return QPixmap()

        frame = _empty_frame(self.stack)
        target_offset = page.mapTo(self.stack, QPoint(0, 0))
        page.render(
            frame,
            target_offset,
            QRegion(),
            QWidget.RenderFlag.DrawChildren,
        )
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

    def _capture_neutral_background(self) -> QPixmap:
        """Rebuild the sharp Fuji frame at the current parallax offset, without glass."""

        quick = getattr(self.background, "quick_window", None)
        wallpaper = self._wallpaper
        if (
            quick is None
            or wallpaper.isNull()
            or quick.width() <= 0
            or quick.height() <= 0
            or self.root.width() <= 0
            or self.root.height() <= 0
        ):
            return self._capture_quick_for_stack()

        root_frame = _empty_frame(self.root)
        root_frame.fill(QColor(23, 38, 58))
        painter = QPainter(root_frame)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        root_w = float(quick.width())
        root_h = float(quick.height())
        item_w = root_w * float(_OVERSCAN)
        item_h = root_h * float(_OVERSCAN)
        try:
            item_x = float(quick.property("imageX"))
            item_y = float(quick.property("imageY"))
        except (TypeError, ValueError, RuntimeError):
            item_x = (root_w - item_w) * 0.5
            item_y = (root_h - item_h) * 0.5

        source_w = max(1.0, float(wallpaper.width()))
        source_h = max(1.0, float(wallpaper.height()))
        scale = max(item_w / source_w, item_h / source_h)
        visible_source_w = item_w / max(scale, 1e-9)
        visible_source_h = item_h / max(scale, 1e-9)
        source_rect = QRectF(
            (source_w - visible_source_w) * 0.5,
            (source_h - visible_source_h) * 0.5,
            visible_source_w,
            visible_source_h,
        )
        target_rect = QRectF(item_x, item_y, item_w, item_h)
        painter.drawPixmap(target_rect, wallpaper, source_rect)
        painter.end()

        top_left = self.stack.mapTo(self.root, QPoint(0, 0))
        dpr = max(1.0, float(root_frame.devicePixelRatio()))
        pixel_rect = QRect(
            int(round(top_left.x() * dpr)),
            int(round(top_left.y() * dpr)),
            max(1, int(round(self.stack.width() * dpr))),
            max(1, int(round(self.stack.height() * dpr))),
        )
        cropped = root_frame.copy(pixel_rect)
        cropped.setDevicePixelRatio(dpr)
        return _fit_frame(cropped, self.stack)

    def _capture_composite(self) -> QPixmap:
        quick_frame = self._capture_quick_for_stack()
        widget_frame = self._render_current_page()
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

    def _begin_phase_badge_transition(self, old_text: str, new_text: str) -> None:
        badge = self._phase_badge
        self._phase_old_text = str(old_text or "")
        self._phase_new_text = str(new_text or "")
        self._phase_swapped = False
        if (
            badge is None
            or self._phase_old_text == self._phase_new_text
            or badge.graphicsEffect() is not None
        ):
            self._phase_effect = None
            return

        badge.setText(self._phase_old_text)
        effect = QGraphicsOpacityEffect(badge)
        effect.setOpacity(1.0)
        badge.setGraphicsEffect(effect)
        self._phase_effect = effect

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

            if not self._phase_swapped:
                badge.setText(self._phase_new_text)
                self._phase_swapped = True

            if elapsed_ms < _HEADER_ENTER_START_MS:
                effect.setOpacity(0.0)
                return

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
                if effect is not None:
                    effect.setOpacity(1.0)
                    badge.setGraphicsEffect(None)
            except RuntimeError:
                pass
        self._phase_old_text = ""
        self._phase_new_text = ""
        self._phase_swapped = False

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

        neutral = self._capture_neutral_background()
        outgoing = self._capture_composite()
        if outgoing.isNull():
            self._resume_presentation()
            self._set_mode(index)
            return
        if neutral.isNull():
            neutral = self._capture_quick_for_stack()
        if neutral.isNull():
            neutral = QPixmap(outgoing)

        self._target_index = index
        self._queued_index = None
        self._outgoing = outgoing
        self._incoming = QPixmap()
        self._neutral = neutral
        self._incoming_enter_start_ms = float(_ENTER_START_MS)
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

        # Real state changes underneath a root-level opaque surface. Calling
        # setCurrentIndex can raise the new page only inside QStackedWidget; it
        # cannot overtake the transition surface anymore.
        self._set_mode(index)
        self._raise_transition_surface()

        phase_new = phase_old
        if badge is not None:
            try:
                phase_new = str(badge.text())
            except RuntimeError:
                phase_new = phase_old
        self._begin_phase_badge_transition(phase_old, phase_new)

        current_page = self.stack.currentWidget()
        page_layout = current_page.layout() if current_page is not None else None
        if page_layout is not None:
            page_layout.activate()

        schedule_mask = getattr(self.background, "schedule_mask_update", None)
        if callable(schedule_mask):
            schedule_mask()

        self._started_s = time.perf_counter()
        self._timer.setInterval(self._frame_interval_ms())
        self._timer.start()
        QTimer.singleShot(_PREPARE_MS, self._prepare_incoming)

    def _elapsed_ms(self) -> float:
        return max(0.0, (time.perf_counter() - self._started_s) * 1000.0)

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

        flush_geometry = getattr(self.background, "_flush_geometry", None)
        if callable(flush_geometry):
            try:
                flush_geometry()
            except RuntimeError:
                pass

        quick = getattr(self.background, "quick_window", None)
        if quick is None:
            self._capture_incoming_after_quick_sync()
            return

        # Do not grab immediately after quick.update(). With the threaded render
        # loop that can sample the previous glass mask and combine it with the new
        # QWidget page. Wait for the next queued/presented Quick frame instead.
        self._awaiting_quick_frame = True
        try:
            quick.update()
        except RuntimeError:
            self._awaiting_quick_frame = False
            self._capture_incoming_after_quick_sync()
            return

        self._quick_sync_timeout.start(
            max(_QUICK_SYNC_TIMEOUT_MS, self._frame_interval_ms() * 3)
        )

    @Slot()
    def _on_quick_frame_swapped(self) -> None:
        if not self._active or not self._awaiting_quick_frame:
            return
        self._awaiting_quick_frame = False
        self._quick_sync_timeout.stop()
        self._capture_incoming_after_quick_sync()

    def _capture_incoming_after_quick_sync(self) -> None:
        if not self._active:
            return
        self._awaiting_quick_frame = False
        self._quick_sync_timeout.stop()

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

        # Hard contract: two readable workspace snapshots never coexist.
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
        if elapsed_ms >= finish_ms and not self._incoming.isNull():
            self._finish_transition()

    def _refresh_phase_copy_for_current_mode(self) -> None:
        # Recompute once so any status signal that landed during motion wins.
        try:
            self._set_mode(int(self.stack.currentIndex()))
        except RuntimeError:
            pass

    def _finish_transition(self) -> None:
        self._timer.stop()
        self._quick_sync_timeout.stop()
        self._awaiting_quick_frame = False
        if not self._active:
            return

        # Paint the exact incoming endpoint once, then hand ownership to the live
        # page in the same GUI turn. The Quick frame used for this pixmap was
        # captured only after frameSwapped, eliminating old-mask/new-page flashes.
        self._surface.set_mix(
            outgoing_alpha=0.0,
            incoming_alpha=1.0,
            veil_alpha=0.0,
        )
        self._raise_transition_surface()
        self._surface.repaint()

        self._surface.hide()
        self._surface.clear_frames()
        self._outgoing = QPixmap()
        self._incoming = QPixmap()
        self._neutral = QPixmap()
        self._active = False
        self._finish_phase_badge_transition()
        self._resume_presentation()
        self._refresh_phase_copy_for_current_mode()

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
        self._surface.hide()
        self._surface.clear_frames()
        self._outgoing = QPixmap()
        self._incoming = QPixmap()
        self._neutral = QPixmap()
        was_active = self._active
        self._active = False
        self._finish_phase_badge_transition()
        if was_active:
            self._resume_presentation()
            self._refresh_phase_copy_for_current_mode()
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

        if self._active:
            self._active = False
            self._finish_phase_badge_transition()
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
