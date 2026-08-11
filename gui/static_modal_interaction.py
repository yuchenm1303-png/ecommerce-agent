from __future__ import annotations

from PySide6.QtCore import (
    QAbstractAnimation,
    QCoreApplication,
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
from PySide6.QtGui import QColor, QKeyEvent, QMouseEvent, QPainter, QPixmap, QRegion
from PySide6.QtWidgets import QFrame, QLabel, QMainWindow, QWidget

from .card_details_fast import FastCardDetailController


_OPEN_MS = 300
_CLOSE_MS = 250
_OPEN_RISE_PX = 18.0
_START_SCALE = 0.992
_SCRIM_ALPHA = 94
_DRAWER_FILL_RGBA = (220, 228, 238, 74)
_DRAWER_BORDER_RGBA = (255, 255, 255, 72)
_DRAWER_RADIUS = 14.0

_STATE_IDLE = "idle"
_STATE_OPENING = "opening"
_STATE_OPEN = "open"
_STATE_CLOSING = "closing"


class _ModalTransitionCompositor(QWidget):
    """One QWidget layer owns every visual part of the modal transition.

    The live application remains underneath and is frozen for the transition.
    This compositor cross-fades directly from the clear live UI to the final
    blurred backdrop while the scrim, drawer shell and cached drawer children
    share the same float progress and frame clock.

    The outer drawer glass is intentionally *not* baked into the cached pixmap.
    Rendering the semi-transparent QWidget background into an intermediate
    QPixmap on Windows can flatten it against an implicit palette/background;
    drawing that flattened frame a second time creates a pale rectangle during
    motion. The compositor therefore paints the exact QSS shell itself and the
    cache contains only the real child widgets (text, tables, buttons, etc.).
    """

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("cardDetailTransitionCompositor")
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAutoFillBackground(False)

        self._full_blur = QPixmap()
        self._panel_frame = QPixmap()
        self._target = QRectF()
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

    def set_frames(
        self,
        *,
        full_blur: QPixmap,
        panel_frame: QPixmap,
        target: QRect,
        progress: float,
    ) -> None:
        self._full_blur = full_blur
        self._panel_frame = panel_frame
        self._target = QRectF(target)
        parent = self.parentWidget()
        if parent is not None:
            self.setGeometry(parent.rect())
        self._progress = max(0.0, min(1.0, float(progress)))
        self.update()

    def set_panel_frame(self, panel_frame: QPixmap, target: QRect) -> None:
        self._panel_frame = panel_frame
        self._target = QRectF(target)
        parent = self.parentWidget()
        if parent is not None:
            self.setGeometry(parent.rect())
        self.update()

    def has_backdrop_frames(self) -> bool:
        return not self._full_blur.isNull()

    def clear_frames(self) -> None:
        self._full_blur = QPixmap()
        self._panel_frame = QPixmap()
        self._target = QRectF()
        self._progress = 0.0

    @staticmethod
    def _draw_scaled(painter: QPainter, pixmap: QPixmap, target: QRectF) -> None:
        if pixmap.isNull():
            return
        painter.drawPixmap(target, pixmap, QRectF(pixmap.rect()))

    @staticmethod
    def _draw_drawer_shell(painter: QPainter, width: float, height: float) -> None:
        shell = QRectF(0.5, 0.5, max(0.0, width - 1.0), max(0.0, height - 1.0))
        painter.setPen(QColor(*_DRAWER_BORDER_RGBA))
        painter.setBrush(QColor(*_DRAWER_FILL_RGBA))
        painter.drawRoundedRect(shell, _DRAWER_RADIUS, _DRAWER_RADIUS)

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        progress = max(0.0, min(1.0, self._progress))
        painter = QPainter(self)

        # A freshly shown translucent child can expose an uninitialised backing
        # store for one frame on Windows. Always clear our complete surface to
        # transparent, including progress == 0, before painting transition data.
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 0))
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

        if progress <= 0.0:
            painter.end()
            return

        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        viewport = QRectF(self.rect())

        # Cross-fade directly from the clear live UI underneath to the exact
        # final blurred frame. There is no intermediate pale/soft backdrop.
        painter.setOpacity(progress)
        self._draw_scaled(painter, self._full_blur, viewport)

        painter.setOpacity(1.0)
        painter.fillRect(
            viewport,
            QColor(12, 17, 26, int(round(_SCRIM_ALPHA * progress))),
        )

        if not self._panel_frame.isNull() and not self._target.isEmpty():
            scale = _START_SCALE + (1.0 - _START_SCALE) * progress
            y_offset = _OPEN_RISE_PX * (1.0 - progress)
            target = self._target
            center = target.center()

            painter.save()
            painter.setOpacity(progress)
            painter.translate(center.x(), center.y() + y_offset)
            painter.scale(scale, scale)
            painter.translate(-target.width() / 2.0, -target.height() / 2.0)

            # Paint the translucent outer shell exactly once in the final scene.
            # The cached pixmap below contains only the real child widget tree.
            self._draw_drawer_shell(painter, target.width(), target.height())
            painter.drawPixmap(QPointF(0.0, 0.0), self._panel_frame)
            painter.restore()

        painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        # The transition layer is also the short-lived input shield. It is an
        # ordinary QWidget child, not a native child window.
        event.accept()


class StaticModalInteractionController(QObject):
    """Unified composited transition for the existing QWidget detail modal."""

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

        self.details.drawer.setGraphicsEffect(None)
        self.details.drawer_effect = None  # type: ignore[assignment]

        self._compositor = _ModalTransitionCompositor(self.root)
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

    def _capture_source_frame(self) -> QPixmap:
        screen = self.window.screen()
        pixmap = QPixmap()
        if screen is not None:
            global_pos = self.root.mapToGlobal(QPoint(0, 0))
            screen_origin = screen.geometry().topLeft()
            local_pos = global_pos - screen_origin
            pixmap = screen.grabWindow(
                0,
                local_pos.x(),
                local_pos.y(),
                self.root.width(),
                self.root.height(),
            )
        if pixmap.isNull():
            pixmap = self.root.grab()
        return pixmap

    def _settle_drawer_tree(self) -> None:
        drawer = self.details.drawer
        widgets = (drawer, *drawer.findChildren(QWidget))
        for widget in widgets:
            try:
                widget.ensurePolished()
            except RuntimeError:
                pass

        QCoreApplication.sendPostedEvents(None, QEvent.Type.PolishRequest)
        for _ in range(2):
            QCoreApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest)
            self.details.body_layout.activate()
            for widget in widgets:
                try:
                    layout = widget.layout()
                    if layout is not None:
                        layout.activate()
                except RuntimeError:
                    pass

    def _render_drawer_frame(self) -> QPixmap:
        """Render only the drawer's child widget tree into a transparent frame.

        The outer QFrame background/border is intentionally excluded. Its QSS
        rgba shell is painted directly by _ModalTransitionCompositor so the
        translucent glass is composited exactly once against the animated
        backdrop instead of being flattened into an intermediate pixmap.
        """

        drawer = self.details.drawer
        self._settle_drawer_tree()

        dpr = max(1.0, float(drawer.devicePixelRatioF()))
        width = max(1, int(round(drawer.width() * dpr)))
        height = max(1, int(round(drawer.height() * dpr)))
        frame = QPixmap(width, height)
        frame.setDevicePixelRatio(dpr)
        frame.fill(Qt.GlobalColor.transparent)

        painter = QPainter(frame)
        flags = QWidget.RenderFlag.DrawWindowBackground | QWidget.RenderFlag.DrawChildren
        for child in drawer.children():
            if not isinstance(child, QWidget):
                continue
            if child.parentWidget() is not drawer or child.isHidden():
                continue
            child.render(
                painter,
                child.pos(),
                QRegion(),
                flags,
            )
        painter.end()
        return frame

    def _capture_panel_offscreen(self, target: QRect) -> QPixmap:
        drawer = self.details.drawer
        offscreen = QRect(
            self.root.width() + 64,
            target.y(),
            target.width(),
            target.height(),
        )
        drawer.setGeometry(offscreen)
        drawer.show()
        try:
            self._settle_drawer_tree()
            frame = self._render_drawer_frame()
        finally:
            drawer.hide()
            drawer.setGeometry(target)
        return frame

    def _prepare_open_state(self, *, ratio: tuple[float, float]) -> QRect:
        self.details._modal_ratio = ratio  # noqa: SLF001
        target = self.details._drawer_rect()  # noqa: SLF001

        source = self._capture_source_frame()
        full_blur = self.details._blur_pixmap(source)  # noqa: SLF001
        if source.isNull() or full_blur.isNull():
            raise RuntimeError("failed to capture modal backdrop")

        self.details.scroll.verticalScrollBar().setValue(0)
        self.details.ghost.hide()
        panel_frame = self._capture_panel_offscreen(target)
        if panel_frame.isNull():
            raise RuntimeError("failed to render complete modal drawer frame")

        self.details.backdrop.setPixmap(full_blur)
        self.details.backdrop.setGeometry(self.root.rect())
        self.details.scrim.setGeometry(self.root.rect())
        self.details.backdrop.hide()
        self.details.scrim.hide()
        self.details.drawer.hide()

        self._compositor.set_frames(
            full_blur=full_blur,
            panel_frame=panel_frame,
            target=target,
            progress=0.0,
        )
        self._compositor.show()
        self._compositor.raise_()
        self._compositor.repaint()
        return target

    def _prepare_close_state(self) -> QRect:
        target = self.details._drawer_rect()  # noqa: SLF001
        self.details.drawer.setGeometry(target)
        panel_frame = self._render_drawer_frame()
        if panel_frame.isNull():
            raise RuntimeError("failed to render modal close frame")
        if not self._compositor.has_backdrop_frames():
            raise RuntimeError("modal transition backdrop cache is unavailable")

        self._compositor.set_panel_frame(panel_frame, target)
        self._compositor.setProperty("progress", 1.0)
        self._compositor.show()
        self._compositor.raise_()
        self._compositor.repaint()

        # The compositor now owns an identical 100% modal frame. Remove the real
        # static layers underneath it before starting the reverse transition.
        self._original_close()
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
            self.details.backdrop.setGeometry(self.root.rect())
            self.details.scrim.setGeometry(self.root.rect())
            self.details.drawer.setGeometry(target)

            self.details.backdrop.show()
            self.details.backdrop.raise_()
            self.details.scrim.show()
            self.details.scrim.raise_()
            self.details.drawer.show()
            self.details.drawer.raise_()
            self._compositor.hide()
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

            self._stop_animation()
            self._compositor.hide()
            self._compositor.clear_frames()
            self._state = _STATE_IDLE
            self._set_motion_active(False)
            self._resume_underlay()
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

        self._compositor.hide()
        self._compositor.clear_frames()
        self.root.repaint()
        self._state = _STATE_IDLE
        self._set_motion_active(False)
        self._resume_underlay()

    def _fallback_open(self, ratio: tuple[float, float]) -> None:
        self._stop_animation()
        self._compositor.hide()
        self._compositor.clear_frames()
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
        self._compositor.clear_frames()
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
        self._compositor.clear_frames()
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
