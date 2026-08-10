from __future__ import annotations

import math
import time

from PySide6.QtCore import QRect, Qt, QTimer
from PySide6.QtWidgets import QFrame, QGraphicsOpacityEffect, QMainWindow

from .card_details import CardDetailController


# Short-lived only. The 8 ms timer is active while opening/revealing/closing and
# never runs during normal GUI idle time.
_MOTION_INTERVAL_MS = 8
_DRAWER_OPEN_MS = 132
_CONTENT_REVEAL_MS = 108
_DRAWER_CLOSE_MS = 128
_DRAWER_TRAVEL = 46
_CARD_PULSE_PAD = 5


class FastCardDetailController(CardDetailController):
    """Low-overhead card detail motion for the native layered QWidget shell.

    The original implementation animated a large QWidget's geometry and opacity
    while also resizing a ghost from card-size to drawer-size. On Windows that
    forces repeated large translucent backing-store composition. This controller
    keeps the drawer at its final size, moves only its x position, keeps the
    source-card pulse small, and reveals populated detail content with a cheap
    clipping cover after the drawer has arrived.
    """

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)

        # The large drawer must never carry QGraphicsOpacityEffect. Removing it
        # avoids a full-size offscreen composition surface on every animation tick.
        self.drawer.setGraphicsEffect(None)
        self.drawer_effect = None  # type: ignore[assignment]

        # Keep a graphics effect only on the tiny source-card pulse. Its painted
        # area stays close to the original card and never grows into the drawer.
        if self.ghost.graphicsEffect() is None:
            self.ghost_effect = QGraphicsOpacityEffect(self.ghost)
            self.ghost.setGraphicsEffect(self.ghost_effect)
        self.ghost_effect.setOpacity(0.0)

        # A simple cover over the scroll viewport creates a real content reveal
        # animation without fading/re-laying-out tables, logs or text widgets.
        self.reveal_cover = QFrame(self.scroll.viewport())
        self.reveal_cover.setObjectName("cardDetailRevealCover")
        self.reveal_cover.setStyleSheet(
            "QFrame#cardDetailRevealCover { background-color: rgba(0,0,0,118); border: 0; }"
        )
        self.reveal_cover.hide()

        self._motion_timer = QTimer(self)
        self._motion_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._motion_timer.setInterval(_MOTION_INTERVAL_MS)
        self._motion_timer.timeout.connect(self._tick_motion)
        self._motion_mode: str | None = None
        self._motion_started = 0.0
        self._source_rect = QRect()
        self._close_start_x = 0

    @staticmethod
    def _ease_out_cubic(progress: float) -> float:
        p = min(1.0, max(0.0, progress))
        return 1.0 - (1.0 - p) ** 3

    @staticmethod
    def _ease_in_cubic(progress: float) -> float:
        p = min(1.0, max(0.0, progress))
        return p * p * p

    @staticmethod
    def _progress(started: float, duration_ms: int) -> float:
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return min(1.0, max(0.0, elapsed_ms / max(1.0, float(duration_ms))))

    def _sync_geometry(self) -> None:
        self.scrim.setGeometry(self.root.rect())
        for frame in self._installed_cards:
            self._position_button(frame)
        if self.drawer.isVisible() and not self._motion_timer.isActive():
            self.drawer.setGeometry(self._drawer_rect())

    def _stop_animation(self) -> None:
        # Base cleanup/open calls this name; make it stop only our short-lived
        # high-refresh motion and never leave an old ghost/cover in front.
        if hasattr(self, "_motion_timer"):
            self._motion_timer.stop()
        self._motion_mode = None
        if hasattr(self, "ghost"):
            self.ghost.hide()
        if hasattr(self, "reveal_cover"):
            self.reveal_cover.hide()

    def _set_reveal_progress(self, reveal: float) -> None:
        """0 = fully covered, 1 = fully revealed; cover itself is trivial paint."""

        viewport = self.scroll.viewport()
        rect = viewport.rect()
        if rect.width() <= 0 or rect.height() <= 0:
            return
        value = min(1.0, max(0.0, reveal))
        if value >= 0.999:
            self.reveal_cover.hide()
            return
        top = int(round(rect.height() * value))
        self.reveal_cover.setGeometry(0, top, rect.width(), max(0, rect.height() - top))
        self.reveal_cover.show()
        self.reveal_cover.raise_()

    def _start_motion(self, mode: str) -> None:
        self._motion_mode = mode
        self._motion_started = time.perf_counter()
        if not self._motion_timer.isActive():
            self._motion_timer.start()

    def open(self, frame: QFrame) -> None:
        if frame not in self._buttons:
            return
        self._stop_animation()
        self._selected = frame
        self._source_rect = self._card_rect(frame)

        # Do only the cheap identity work before motion. Heavy table/text cloning
        # waits until the shell has finished sliding, so a large field table can
        # never block the drawer's first frames.
        self._clear_body()
        status = self._status_name(frame)
        title, eyebrow = self._card_identity(frame, status)
        self.title.setText(title)
        self.eyebrow.setText(eyebrow)

        target = self._drawer_rect()
        self.scrim.setGeometry(self.root.rect())
        self.scrim.show()
        self.scrim.raise_()

        self.drawer.setGeometry(target)
        self.drawer.move(target.x() + _DRAWER_TRAVEL, target.y())
        self.drawer.show()
        self.drawer.raise_()
        if self.drawer.layout() is not None:
            self.drawer.layout().activate()
        self._set_reveal_progress(0.0)

        # Small source pulse only: no card->drawer resize morph.
        self.ghost.setGeometry(self._source_rect)
        self.ghost_effect.setOpacity(0.30)
        self.ghost.show()
        self.ghost.raise_()

        self._start_motion("opening")

    def _start_content_reveal(self) -> None:
        frame = self._selected
        if frame is None or not self.drawer.isVisible():
            self._finish_close()
            return

        # Populate behind an opaque cover while the drawer is stationary. Any
        # QTableWidget construction cost is therefore isolated from motion.
        self._populate(frame)
        if self.drawer.layout() is not None:
            self.drawer.layout().activate()
        self.body_layout.activate()
        self.scroll.verticalScrollBar().setValue(0)
        self._set_reveal_progress(0.0)
        self._start_motion("revealing")

    def close(self) -> None:
        if not self.drawer.isVisible() and not self.scrim.isVisible():
            return
        self._stop_animation()
        self.drawer.raise_()
        self._close_start_x = self.drawer.x()
        self._source_rect = self._card_rect(self._selected)
        self._set_reveal_progress(1.0)
        self._start_motion("closing")

    def _tick_motion(self) -> None:
        mode = self._motion_mode
        if mode is None:
            self._motion_timer.stop()
            return

        target = self._drawer_rect()

        if mode == "opening":
            p = self._progress(self._motion_started, _DRAWER_OPEN_MS)
            eased = self._ease_out_cubic(p)
            x = int(round(target.x() + _DRAWER_TRAVEL * (1.0 - eased)))
            self.drawer.move(x, target.y())

            # The pulse expands only a few pixels around the source card and
            # fades quickly; it never becomes a full-screen translucent surface.
            pulse_phase = min(1.0, p / 0.78)
            pad = int(round(_CARD_PULSE_PAD * math.sin(math.pi * pulse_phase)))
            self.ghost.setGeometry(self._source_rect.adjusted(-pad, -pad, pad, pad))
            self.ghost_effect.setOpacity(max(0.0, 0.30 * (1.0 - pulse_phase)))

            if p >= 1.0:
                self._motion_timer.stop()
                self._motion_mode = None
                self.drawer.setGeometry(target)
                self.ghost.hide()
                self._start_content_reveal()
            return

        if mode == "revealing":
            p = self._progress(self._motion_started, _CONTENT_REVEAL_MS)
            self.drawer.setGeometry(target)
            self._set_reveal_progress(self._ease_out_cubic(p))
            if p >= 1.0:
                self._motion_timer.stop()
                self._motion_mode = None
                self._set_reveal_progress(1.0)
                self.close_button.setFocus(Qt.FocusReason.OtherFocusReason)
            return

        if mode == "closing":
            p = self._progress(self._motion_started, _DRAWER_CLOSE_MS)
            eased = self._ease_in_cubic(p)
            x = int(round(self._close_start_x + _DRAWER_TRAVEL * eased))
            self.drawer.move(x, target.y())

            # Cover the complex detail body as it exits instead of fading that
            # body. This keeps tables/logs static during the slide-out.
            cover = self._ease_out_cubic(min(1.0, p / 0.78))
            self._set_reveal_progress(1.0 - cover)

            # Tiny return pulse near the source card during the final third.
            if not self._source_rect.isEmpty() and p > 0.62:
                local = min(1.0, (p - 0.62) / 0.38)
                pad = int(round(_CARD_PULSE_PAD * (1.0 - local)))
                self.ghost.setGeometry(self._source_rect.adjusted(-pad, -pad, pad, pad))
                self.ghost_effect.setOpacity(0.20 * math.sin(math.pi * local))
                self.ghost.show()
                self.ghost.raise_()

            if p >= 1.0:
                self._finish_close()

    def _finish_open(self) -> None:
        # Kept for compatibility with the base class API; the fast path finishes
        # through the explicit opening -> revealing state machine above.
        self._start_content_reveal()

    def _finish_close(self) -> None:
        self._motion_timer.stop()
        self._motion_mode = None
        self.drawer.hide()
        self.ghost.hide()
        self.reveal_cover.hide()
        self.scrim.hide()
        self._selected = None


def install_card_details(window: QMainWindow) -> FastCardDetailController:
    controller = FastCardDetailController(window)
    window._card_details = controller  # type: ignore[attr-defined]
    window.destroyed.connect(controller._cleanup)
    return controller
