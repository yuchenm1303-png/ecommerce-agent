from __future__ import annotations

from typing import Any

from PySide6.QtCore import QEvent, QObject, QPoint, QTimer, Signal
from PySide6.QtWidgets import QMainWindow, QWidget


_LAYOUT_POLL_MS = 16
_LAYOUT_STABLE_SAMPLES = 5
_HANDOFF_FRAME_MS = 16
_QUICK_SETTLE_FRAMES = 2
_LAYOUT_ACTIVITY_EVENTS = {
    QEvent.Type.LayoutRequest,
    QEvent.Type.Resize,
    QEvent.Type.Move,
    QEvent.Type.Show,
    QEvent.Type.Hide,
    QEvent.Type.StyleChange,
    QEvent.Type.FontChange,
}


class StartupEntranceStabilityGate(QObject):
    """Settle QWidget state, transfer presentation to Quick, then reveal in Quick.

    QWidget remains the authoritative startup layout/state host, but it never owns
    the entrance animation. Once layout is quiescent, the unified QQuickWindow is
    activated behind the still-closed Quick curtain. Two rendered frames complete
    the native-child handoff before the GPU reveal timeline is allowed to start.
    """

    handoffReady = Signal()

    def __init__(self, window: QMainWindow, entrance: Any) -> None:
        super().__init__(window)
        self.window = window
        self.entrance = entrance
        self.visual = getattr(entrance, "visual", None)
        self.background = getattr(entrance, "background", None)
        self.overlay = getattr(entrance, "overlay", None)

        self._last_signature: tuple[Any, ...] | None = None
        self._stable_samples = 0
        self._live_paint_seen = False
        self._layout_epoch = 0
        self._layout_watch: list[QWidget] = []
        self._start_requested = False
        self._reveal_barrier_started = False
        self._reveal_frames_remaining = 0
        self._reveal_frame_quick: Any | None = None
        self._handoff_emitted = False
        self._finish_started = False

        clock = getattr(window, "_presentation_clock", None)
        suspend = getattr(clock, "suspend", None)
        if callable(suspend):
            suspend("startup")

        self._install_live_surface_watch()

        if self.overlay is not None:
            try:
                self.overlay.finished.disconnect(self.entrance._finish)  # noqa: SLF001
            except (AttributeError, RuntimeError, TypeError):
                pass
            self.overlay.finished.connect(self._stage_finish)

    def _watch_layout_widget(self, widget: QWidget | None) -> None:
        if not isinstance(widget, QWidget):
            return
        if any(current is widget for current in self._layout_watch):
            return
        widget.installEventFilter(self)
        self._layout_watch.append(widget)

    def _install_live_surface_watch(self) -> None:
        self._watch_layout_widget(self.window)
        central = self.window.centralWidget()
        self._watch_layout_widget(central)
        if isinstance(central, QWidget):
            for widget in central.findChildren(QWidget):
                self._watch_layout_widget(widget)

    def _remove_live_surface_watch(self) -> None:
        for widget in self._layout_watch:
            try:
                widget.removeEventFilter(self)
            except RuntimeError:
                pass
        self._layout_watch.clear()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if bool(getattr(self.entrance, "_finished", False)):
            return False

        try:
            central = self.window.centralWidget()
        except RuntimeError:
            return False

        event_type = event.type()
        if watched is central and event_type == QEvent.Type.Paint:
            self._live_paint_seen = True

        if event_type in _LAYOUT_ACTIVITY_EVENTS:
            self._layout_epoch += 1
            self._stable_samples = 0
            self._live_paint_seen = False

        return False

    def start(self) -> None:
        if self._start_requested:
            return
        self._start_requested = True
        raise_overlay = getattr(self.entrance, "raise_overlay", None)
        if callable(raise_overlay):
            raise_overlay()
        QTimer.singleShot(0, self._probe_layout)

    def _ensure_live_paint(self) -> None:
        if self._live_paint_seen:
            return
        central = self.window.centralWidget()
        if not isinstance(central, QWidget):
            return
        try:
            if central.isVisibleTo(self.window):
                # update(), unlike repaint(), does not synchronously block the GUI
                # thread while the startup gate is still collecting layout state.
                central.update()
        except RuntimeError:
            pass

    def _geometry_signature(self) -> tuple[Any, ...]:
        values: list[Any] = [
            int(self._layout_epoch),
            int(self.window.width()),
            int(self.window.height()),
        ]
        for widget in self._layout_watch:
            if widget is self.window:
                continue
            try:
                if not widget.isVisibleTo(self.window):
                    continue
                top_left = widget.mapTo(self.window, QPoint(0, 0))
                values.extend(
                    (
                        id(widget),
                        int(top_left.x()),
                        int(top_left.y()),
                        int(widget.width()),
                        int(widget.height()),
                    )
                )
            except RuntimeError:
                continue
        return tuple(values)

    def _probe_layout(self) -> None:
        if bool(getattr(self.entrance, "_finished", False)):
            return
        if bool(getattr(self.entrance, "_started", False)) or self._reveal_barrier_started:
            return

        self._ensure_live_paint()
        signature = self._geometry_signature()
        if signature == self._last_signature:
            self._stable_samples += 1
        else:
            self._last_signature = signature
            self._stable_samples = 1

        if self._live_paint_seen and self._stable_samples >= _LAYOUT_STABLE_SAMPLES:
            self._begin_reveal_frame_barrier()
            return
        QTimer.singleShot(_LAYOUT_POLL_MS, self._probe_layout)

    def _flush_native_background(self) -> None:
        background = self.background
        if background is None:
            return
        geometry_timer = getattr(background, "_geometry_timer", None)
        if geometry_timer is not None:
            try:
                geometry_timer.stop()
            except RuntimeError:
                pass
        flush = getattr(background, "_flush_geometry", None)
        if callable(flush):
            try:
                flush()
            except RuntimeError:
                pass

        quick = getattr(background, "quick_window", None)
        if quick is not None:
            try:
                quick.setProperty("animationRunning", False)
                request_update = getattr(quick, "requestUpdate", None)
                if callable(request_update):
                    request_update()
                else:
                    quick.update()
            except RuntimeError:
                pass

    def _prime_live_runtime(self) -> None:
        central = self.window.centralWidget()
        if isinstance(central, QWidget):
            try:
                central.update()
            except RuntimeError:
                pass
        self._flush_native_background()

    def _emit_handoff_once(self) -> None:
        if self._handoff_emitted:
            return
        self._handoff_emitted = True
        self.handoffReady.emit()

    def _begin_reveal_frame_barrier(self) -> None:
        if self._reveal_barrier_started:
            return
        self._reveal_barrier_started = True

        # No QWidget layout observer is allowed to run during the entrance itself.
        # From this point forward the visible animation is exclusively Scene Graph.
        self._remove_live_surface_watch()
        self._prime_live_runtime()

        quick = getattr(self.background, "quick_window", None)
        if quick is None:
            self._emit_handoff_once()
            self._start_entrance()
            return
        try:
            quick.frameSwapped.connect(self._on_reveal_frame_swapped)
        except (AttributeError, RuntimeError, TypeError):
            self._emit_handoff_once()
            QTimer.singleShot(0, self._start_entrance)
            return

        self._reveal_frame_quick = quick
        self._reveal_frames_remaining = _QUICK_SETTLE_FRAMES

        # Activate the main Quick scene while the startup curtain is still fully
        # closed. StaticQmlViewController hides the native QWidget child on its
        # first rendered frame; the second frame settles the unified composition.
        self._emit_handoff_once()
        self._flush_native_background()

    def _disconnect_reveal_frame_barrier(self) -> None:
        quick = self._reveal_frame_quick
        self._reveal_frame_quick = None
        if quick is None:
            return
        try:
            quick.frameSwapped.disconnect(self._on_reveal_frame_swapped)
        except (AttributeError, RuntimeError, TypeError):
            pass

    def _on_reveal_frame_swapped(self) -> None:
        if self._reveal_frames_remaining <= 0:
            return
        self._reveal_frames_remaining -= 1
        if self._reveal_frames_remaining > 0:
            try:
                self._reveal_frame_quick.requestUpdate()
            except (AttributeError, RuntimeError):
                pass
            return
        self._disconnect_reveal_frame_barrier()
        QTimer.singleShot(0, self._start_entrance)

    def _start_entrance(self) -> None:
        self._disconnect_reveal_frame_barrier()
        self._reveal_frames_remaining = 0
        start = getattr(self.entrance, "start", None)
        if callable(start) and not bool(getattr(self.entrance, "_started", False)):
            start()

    def _stage_finish(self) -> None:
        if self._finish_started:
            return
        self._finish_started = True
        self._disconnect_reveal_frame_barrier()
        self._remove_live_surface_watch()
        try:
            self.entrance._finished = True  # noqa: SLF001
        except (AttributeError, RuntimeError):
            pass

        release = getattr(self.entrance, "release_overlay", None)
        if callable(release):
            release()

        assistant = getattr(self.window, "_runtime_assistant", None)
        if isinstance(assistant, QWidget):
            try:
                assistant.raise_()
            except RuntimeError:
                pass

        QTimer.singleShot(_HANDOFF_FRAME_MS, self._resume_effects)
        QTimer.singleShot(_HANDOFF_FRAME_MS * 2, self._resume_card_fx)
        QTimer.singleShot(_HANDOFF_FRAME_MS * 3, self._resume_presentation)

    def _quick_presentation_active(self) -> bool:
        view = getattr(self.window, "_static_qml_view_controller", None)
        return bool(getattr(view, "static_active", False))

    def _resume_effects(self) -> None:
        effects = getattr(self.entrance, "_hidden_effects", None)
        if isinstance(effects, QWidget) and not self._quick_presentation_active():
            try:
                effects.show()
                effects.raise_()
            except RuntimeError:
                pass
        try:
            self.entrance._hidden_effects = None  # noqa: SLF001
        except (AttributeError, RuntimeError):
            pass

    def _resume_card_fx(self) -> None:
        if self._quick_presentation_active():
            return
        if bool(getattr(self.entrance, "_card_fx_was_suspended", False)):
            return
        card_fx = getattr(self.window, "_nekro_card_fx", None)
        resume = getattr(card_fx, "resume_from_modal", None)
        if callable(resume):
            try:
                resume()
            except RuntimeError:
                pass

    def _resume_presentation(self) -> None:
        clock = getattr(self.window, "_presentation_clock", None)
        resume = getattr(clock, "resume", None)
        if callable(resume):
            resume("startup")


def install_startup_entrance_stability(
    window: QMainWindow,
    entrance: Any,
) -> StartupEntranceStabilityGate:
    existing = getattr(window, "_startup_entrance_stability", None)
    if isinstance(existing, StartupEntranceStabilityGate):
        return existing
    gate = StartupEntranceStabilityGate(window, entrance)
    window._startup_entrance_stability = gate  # type: ignore[attr-defined]
    return gate


__all__ = ["StartupEntranceStabilityGate", "install_startup_entrance_stability"]
