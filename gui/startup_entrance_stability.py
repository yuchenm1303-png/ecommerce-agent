from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import QObject, QPoint, QTimer
from PySide6.QtWidgets import QFrame, QMainWindow, QWidget


_LAYOUT_POLL_MS = 16
_LAYOUT_STABLE_SAMPLES = 3
_LAYOUT_SETTLE_TIMEOUT_MS = 240
_HANDOFF_FRAME_MS = 16
_NATIVE_SETTLE_FRAMES = 2


class StartupEntranceStabilityGate(QObject):
    """Keep startup snapshot geometry and the live QWidget/Quick handoff identical."""

    def __init__(self, window: QMainWindow, entrance: Any) -> None:
        super().__init__(window)
        self.window = window
        self.entrance = entrance
        self.visual = getattr(entrance, "visual", None)
        self.background = getattr(entrance, "background", None)
        self.overlay = getattr(entrance, "overlay", None)

        self._probe_started_s = 0.0
        self._last_signature: tuple[Any, ...] | None = None
        self._stable_samples = 0
        self._start_requested = False
        self._handoff_started = False

        if self.overlay is not None:
            try:
                self.overlay.finished.disconnect(self.entrance._finish)  # noqa: SLF001
            except (AttributeError, RuntimeError, TypeError):
                pass
            self.overlay.finished.connect(self._stage_finish)

    def start(self) -> None:
        if self._start_requested:
            return
        self._start_requested = True
        self._probe_started_s = time.perf_counter()
        raise_overlay = getattr(self.entrance, "raise_overlay", None)
        if callable(raise_overlay):
            raise_overlay()
        QTimer.singleShot(0, self._probe_layout)

    def _geometry_signature(self) -> tuple[Any, ...]:
        values: list[Any] = [int(self.window.width()), int(self.window.height())]

        central = self.window.centralWidget()
        if isinstance(central, QWidget):
            try:
                top_left = central.mapTo(self.window, QPoint(0, 0))
                values.extend(
                    (
                        "central",
                        int(top_left.x()),
                        int(top_left.y()),
                        int(central.width()),
                        int(central.height()),
                    )
                )
            except RuntimeError:
                pass

        scroll = getattr(self.window, "_single_page_scroll", None)
        viewport = getattr(scroll, "viewport", None)
        viewport = viewport() if callable(viewport) else None
        if isinstance(viewport, QWidget):
            try:
                top_left = viewport.mapTo(self.window, QPoint(0, 0))
                values.extend(
                    (
                        "viewport",
                        int(top_left.x()),
                        int(top_left.y()),
                        int(viewport.width()),
                        int(viewport.height()),
                    )
                )
            except RuntimeError:
                pass

        glass = getattr(self.visual, "_glass", None)
        if isinstance(glass, dict):
            for frame in glass:
                if not isinstance(frame, QFrame):
                    continue
                try:
                    if not frame.isVisibleTo(self.window):
                        continue
                    top_left = frame.mapTo(self.window, QPoint(0, 0))
                    values.extend(
                        (
                            frame.objectName(),
                            int(top_left.x()),
                            int(top_left.y()),
                            int(frame.width()),
                            int(frame.height()),
                        )
                    )
                except RuntimeError:
                    continue

        return tuple(values)

    def _probe_layout(self) -> None:
        if bool(getattr(self.entrance, "_finished", False)):
            return
        if bool(getattr(self.entrance, "_started", False)):
            return

        signature = self._geometry_signature()
        if signature == self._last_signature:
            self._stable_samples += 1
        else:
            self._last_signature = signature
            self._stable_samples = 1

        elapsed_ms = (time.perf_counter() - self._probe_started_s) * 1000.0
        if (
            self._stable_samples >= _LAYOUT_STABLE_SAMPLES
            or elapsed_ms >= _LAYOUT_SETTLE_TIMEOUT_MS
        ):
            start = getattr(self.entrance, "start", None)
            if callable(start):
                start()
            return

        QTimer.singleShot(_LAYOUT_POLL_MS, self._probe_layout)

    def _flush_native_background(self) -> None:
        """Publish final Quick card geometry/mask while the startup overlay hides it."""

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
            except RuntimeError:
                pass

    def _prime_static_runtime(self) -> None:
        """Prepare the exact live QWidget and Quick geometry under the final overlay frame."""

        # Compatibility with older local-glass builds. Current production glass is
        # owned by NativeQuickBackground, flushed synchronously below.
        local_glass = getattr(self.window, "_scroll_local_glass", None)
        layer = getattr(local_glass, "_layer", None)
        if isinstance(layer, QWidget):
            try:
                layer.show()
                resize = getattr(local_glass, "resize_to_viewport", None)
                if callable(resize):
                    resize()
                sync = getattr(local_glass, "sync_card_geometry", None)
                if callable(sync):
                    sync()
                layer.update()
            except RuntimeError:
                pass

        try:
            self.entrance._hidden_local_glass = None  # noqa: SLF001
        except (AttributeError, RuntimeError):
            pass

        glass = getattr(self.visual, "_glass", None)
        if isinstance(glass, dict):
            for frame in glass:
                if not isinstance(frame, QFrame):
                    continue
                try:
                    if frame.isVisibleTo(self.window):
                        frame.update()
                except RuntimeError:
                    continue

        central = self.window.centralWidget()
        if isinstance(central, QWidget):
            try:
                central.update()
            except RuntimeError:
                pass

        self._flush_native_background()

    def _stage_finish(self) -> None:
        if self._handoff_started:
            return
        self._handoff_started = True
        try:
            self.entrance._finished = True  # noqa: SLF001
        except (AttributeError, RuntimeError):
            pass

        self._prime_static_runtime()
        QTimer.singleShot(_HANDOFF_FRAME_MS, self._settle_live_runtime)

    def _settle_live_runtime(self) -> None:
        # A second native flush catches any geometry/layout request produced by the
        # first covered QWidget paint. Keep the overlay visible until Quick has had
        # another frame to consume the final mask texture.
        self._flush_native_background()
        QTimer.singleShot(
            _HANDOFF_FRAME_MS * max(1, _NATIVE_SETTLE_FRAMES - 1),
            self._commit_overlay_handoff,
        )

    def _commit_overlay_handoff(self) -> None:
        # One final cheap geometry flush is intentional here: it should be a no-op
        # when the covered settle frames were stable, and prevents a queued 24 ms
        # mask update from becoming the first visible post-entrance frame.
        self._flush_native_background()

        overlay = self.overlay
        if isinstance(overlay, QWidget):
            try:
                overlay.hide()
                overlay.deleteLater()
            except RuntimeError:
                pass

        assistant = getattr(self.window, "_runtime_assistant", None)
        if isinstance(assistant, QWidget):
            try:
                assistant.raise_()
            except RuntimeError:
                pass

        QTimer.singleShot(_HANDOFF_FRAME_MS, self._resume_effects)
        QTimer.singleShot(_HANDOFF_FRAME_MS * 2, self._resume_card_fx)
        QTimer.singleShot(_HANDOFF_FRAME_MS * 3, self._resume_pointer)

    def _resume_effects(self) -> None:
        effects = getattr(self.entrance, "_hidden_effects", None)
        if isinstance(effects, QWidget):
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
        if bool(getattr(self.entrance, "_card_fx_was_suspended", False)):
            return
        card_fx = getattr(self.window, "_nekro_card_fx", None)
        resume = getattr(card_fx, "resume_from_modal", None)
        if callable(resume):
            try:
                resume()
            except RuntimeError:
                pass

    def _resume_pointer(self) -> None:
        pointer_timer = getattr(self.background, "_pointer_timer", None)
        if bool(getattr(self.entrance, "_pointer_was_active", False)) and pointer_timer is not None:
            try:
                if not pointer_timer.isActive():
                    pointer_timer.start()
            except RuntimeError:
                pass
        if self.background is not None:
            try:
                self.background._last_pointer_norm = None  # noqa: SLF001
            except (AttributeError, RuntimeError):
                pass

        hotpath = getattr(self.window, "_background_pointer_hotpath", None)
        if hotpath is not None:
            try:
                hotpath._last_global = None  # noqa: SLF001
                hotpath._last_geometry = None  # noqa: SLF001
            except (AttributeError, RuntimeError):
                pass


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
