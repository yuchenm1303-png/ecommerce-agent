from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import QObject, QPoint, QTimer
from PySide6.QtWidgets import QFrame, QMainWindow, QWidget


_LAYOUT_POLL_MS = 16
_LAYOUT_STABLE_SAMPLES = 3
_LAYOUT_SETTLE_TIMEOUT_MS = 240
_HANDOFF_FRAME_MS = 16


class StartupEntranceStabilityGate(QObject):
    """Keep startup snapshot geometry and the live QWidget handoff frame-identical.

    The native owner is maximized immediately before the entrance starts. Windows,
    the embedded QWidget child and Qt layouts can therefore keep settling for a few
    event-loop turns after ``shell.show()``. Capturing during that settle window
    bakes stale card coordinates into the entrance snapshot and produces a visible
    whole-page jump when the overlay disappears.

    This gate waits for several identical geometry samples before allowing the
    existing entrance controller to capture. At the other end it primes the static
    glass layer while the final entrance frame is still covering the window, then
    restores effects/card hover/pointer motion over separate frames instead of
    doing all of that work in the same paint turn as overlay removal.
    """

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
            # Replace the eager one-turn restore in StartupEntranceController with
            # the staged handoff below. Keep the original controller untouched so
            # the entrance visual/timing code remains one stable implementation.
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

    def _prime_static_runtime(self) -> None:
        """Prepare the exact live geometry while the final overlay frame hides it."""

        local_glass = getattr(self.window, "_scroll_local_glass", None)
        layer = getattr(local_glass, "_layer", None)
        if isinstance(layer, QWidget):
            try:
                layer.show()
                resize = getattr(layer, "resize_to_viewport", None)
                if callable(resize):
                    resize()
                sync = getattr(layer, "sync_card_geometry", None)
                if callable(sync):
                    sync()
                layer.update()
            except RuntimeError:
                pass

        # StartupEntranceController tracks this exact widget for its normal
        # restore path. We have already restored it under cover, so clear the
        # marker and do not show it a second time during the staged resume.
        try:
            self.entrance._hidden_local_glass = None  # noqa: SLF001
        except (AttributeError, RuntimeError):
            pass

        central = self.window.centralWidget()
        if isinstance(central, QWidget):
            try:
                central.update()
            except RuntimeError:
                pass

    def _stage_finish(self) -> None:
        if self._handoff_started:
            return
        self._handoff_started = True
        try:
            self.entrance._finished = True  # noqa: SLF001
        except (AttributeError, RuntimeError):
            pass

        self._prime_static_runtime()
        # Give the backing store one complete event-loop frame to publish the
        # final live card/glass geometry while it is still visually covered.
        QTimer.singleShot(_HANDOFF_FRAME_MS, self._commit_overlay_handoff)

    def _commit_overlay_handoff(self) -> None:
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

        # Do not wake every animated subsystem on the exact same frame that swaps
        # the frozen entrance composite for the live QWidget tree.
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
