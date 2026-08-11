from __future__ import annotations

import math
import time
from typing import Any

from PySide6.QtCore import QObject, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QVBoxLayout, QWidget


_STAGE_LABELS = {
    "scan": "Source Capture",
    "cold": "Step 1 · Vertical",
    "hot": "Step 2 · Brand",
    "plan": "Step 3 · Resolve / Fill Plan",
}

_MODE_COLORS = {
    "STANDBY": QColor("#d8e8ff"),
    "PREPARING": QColor("#9bdcff"),
    "READY": QColor("#b8b6ef"),
    "FILLING": QColor("#8fe1b9"),
    "COMPLETE": QColor("#8fe1b9"),
    "FAILED": QColor("#f18da0"),
}


class ActivityPresence(QWidget):
    """Compact time-driven heartbeat for real workflow progress.

    Runner events own the target percentage. The widget only eases the painted
    fill toward that target and animates decorative liveness cues. No synthetic
    progress is ever added. Rendering stays local to this tiny strip.
    """

    _FRAME_MS = 16
    _PROGRESS_TAU_S = 0.18
    _SWEEP_PERIOD_S = 2.25
    _PULSE_PERIOD_S = 1.75

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("activityPresence")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFixedHeight(32)
        self.setMinimumWidth(320)

        self.mode = "STANDBY"
        self.detail = "等待任务"
        self.target_percent = 0.0
        self.display_percent = 0.0
        self.active = False

        self._motion_time_s = 0.0
        self._last_frame_s = time.perf_counter()

        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(self._FRAME_MS)
        self._timer.timeout.connect(self._animate)

    @property
    def percent(self) -> int:
        """Compatibility/readback: exact runner-owned target percentage."""
        return int(round(self.target_percent))

    def set_activity(self, mode: str, detail: str, percent: int, *, active: bool) -> None:
        next_target = float(max(0, min(100, int(percent))))
        was_active = self.active

        self.mode = str(mode or "STANDBY").upper()
        self.detail = str(detail or "").strip() or "等待任务"
        self.target_percent = next_target
        self.active = bool(active)

        # A new run legitimately resets 100 -> 0. Do not animate backwards
        # through stale progress from the previous product.
        if self.active and (not was_active or self.target_percent + 0.5 < self.display_percent):
            self.display_percent = self.target_percent
            self._motion_time_s = 0.0

        if self.active:
            self._last_frame_s = time.perf_counter()
            if not self._timer.isActive():
                self._timer.start()
        else:
            # Final/idle states are exact, static and cost zero CPU.
            self._timer.stop()
            self.display_percent = self.target_percent
            self._motion_time_s = 0.0

        self.update()

    def _animate(self) -> None:
        now = time.perf_counter()
        dt = max(0.0, min(0.050, now - self._last_frame_s))
        self._last_frame_s = now
        self._motion_time_s += dt

        # Frame-rate-independent exponential catch-up. The painted fill never
        # advances beyond the runner's real target.
        delta = self.target_percent - self.display_percent
        if abs(delta) <= 0.015:
            self.display_percent = self.target_percent
        else:
            alpha = 1.0 - math.exp(-dt / self._PROGRESS_TAU_S)
            self.display_percent += delta * alpha
            if delta > 0.0:
                self.display_percent = min(self.display_percent, self.target_percent)
            else:
                self.display_percent = max(self.display_percent, self.target_percent)

        self.update()

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        width = float(max(1, self.width()))
        height = float(max(1, self.height()))
        outer = QRectF(0.5, 0.5, width - 1.0, height - 1.0)

        # Very restrained glass shell: one border and one shallow vertical tint.
        shell = QLinearGradient(0.0, 0.0, 0.0, height)
        shell.setColorAt(0.0, QColor(14, 29, 50, 76))
        shell.setColorAt(1.0, QColor(5, 14, 29, 92))
        painter.setPen(QPen(QColor(255, 255, 255, 28), 1.0))
        painter.setBrush(shell)
        painter.drawRoundedRect(outer, 9.0, 9.0)

        color = QColor(_MODE_COLORS.get(self.mode, QColor("#d8e8ff")))

        # Pulse is time-based, not tick-based, so it remains smooth if a frame
        # is delayed by other GUI work.
        pulse_phase = (self._motion_time_s / self._PULSE_PERIOD_S) * math.tau
        pulse = 0.76 if not self.active else 0.72 + 0.22 * ((math.sin(pulse_phase) + 1.0) * 0.5)

        if self.active:
            halo_color = QColor(color)
            halo_color.setAlpha(int(58 * pulse))
            halo = QRadialGradient(14.0, 13.0, 9.5)
            halo.setColorAt(0.0, halo_color)
            halo_edge = QColor(halo_color)
            halo_edge.setAlpha(0)
            halo.setColorAt(1.0, halo_edge)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(halo)
            painter.drawEllipse(QRectF(4.5, 3.5, 19.0, 19.0))

        dot = QColor(color)
        dot.setAlpha(int(245 * pulse))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(dot)
        painter.drawEllipse(QRectF(10.5, 9.5, 7.0, 7.0))

        font = painter.font()
        font.setBold(True)
        font.setPointSizeF(max(8.0, font.pointSizeF() - 0.5))
        painter.setFont(font)
        painter.setPen(color)
        painter.drawText(
            QRectF(25.0, 1.0, 94.0, 22.0),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self.mode,
        )

        detail_font = painter.font()
        detail_font.setBold(False)
        painter.setFont(detail_font)
        painter.setPen(QColor(255, 255, 255, 205))
        detail_left = 114.0
        detail_right = max(detail_left + 20.0, width - 73.0)
        detail_width = max(20, int(detail_right - detail_left))
        elided = painter.fontMetrics().elidedText(
            self.detail,
            Qt.TextElideMode.ElideRight,
            detail_width,
        )
        painter.drawText(
            QRectF(detail_left, 1.0, float(detail_width), 22.0),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            elided,
        )

        # Keep the text truthful to the runner target while the bar itself
        # visually catches up over a few hundred milliseconds.
        painter.setPen(QColor(255, 255, 255, 190))
        painter.drawText(
            QRectF(max(0.0, width - 66.0), 1.0, 55.0, 22.0),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
            f"{int(round(self.target_percent))}%",
        )

        track_x = 10.0
        track_y = height - 5.5
        track_w = max(1.0, width - 20.0)
        track_h = 3.0
        track = QRectF(track_x, track_y, track_w, track_h)

        painter.setPen(Qt.PenStyle.NoPen)
        track_bg = QLinearGradient(track_x, 0.0, track_x + track_w, 0.0)
        track_bg.setColorAt(0.0, QColor(255, 255, 255, 16))
        track_bg.setColorAt(0.5, QColor(255, 255, 255, 27))
        track_bg.setColorAt(1.0, QColor(255, 255, 255, 16))
        painter.setBrush(track_bg)
        painter.drawRoundedRect(track, 1.5, 1.5)

        completed_w = track_w * (self.display_percent / 100.0)
        if completed_w > 0.0:
            fill_rect = QRectF(track_x, track_y, completed_w, track_h)
            fill = QLinearGradient(track_x, 0.0, track_x + max(1.0, completed_w), 0.0)
            start_color = QColor(color)
            start_color.setAlpha(112)
            end_color = QColor(color)
            end_color.setAlpha(205)
            fill.setColorAt(0.0, start_color)
            fill.setColorAt(0.72, end_color)
            fill.setColorAt(1.0, end_color)
            painter.setBrush(fill)
            painter.drawRoundedRect(fill_rect, 1.5, 1.5)

            # A tiny luminous leading edge makes progress changes feel continuous
            # without a large blurred/glowing layer.
            head_x = track_x + completed_w
            head = QLinearGradient(max(track_x, head_x - 18.0), 0.0, head_x, 0.0)
            head_clear = QColor(color)
            head_clear.setAlpha(0)
            head_bright = QColor(color)
            head_bright.setAlpha(235)
            head.setColorAt(0.0, head_clear)
            head.setColorAt(1.0, head_bright)
            painter.setBrush(head)
            painter.drawRoundedRect(
                QRectF(max(track_x, head_x - 18.0), track_y, min(18.0, completed_w), track_h),
                1.5,
                1.5,
            )

        # Independent activity shimmer: its position comes from elapsed time.
        # It does not alter completed_w and therefore cannot fake progress.
        if self.active:
            sweep_phase = (self._motion_time_s / self._SWEEP_PERIOD_S) % 1.0
            glow_w = min(92.0, max(54.0, track_w * 0.075))
            travel_w = track_w + glow_w * 2.0
            center = track_x - glow_w + travel_w * sweep_phase
            left = max(track_x, center - glow_w)
            right = min(track_x + track_w, center + glow_w)

            if right > left:
                shimmer = QLinearGradient(left, 0.0, right, 0.0)
                clear = QColor(color)
                clear.setAlpha(0)
                soft = QColor(color)
                soft.setAlpha(55)
                bright = QColor(255, 255, 255, 185)
                shimmer.setColorAt(0.0, clear)
                shimmer.setColorAt(0.34, soft)
                shimmer.setColorAt(0.5, bright)
                shimmer.setColorAt(0.66, soft)
                shimmer.setColorAt(1.0, clear)
                painter.setBrush(shimmer)
                painter.drawRoundedRect(
                    QRectF(left, track_y - 0.5, right - left, track_h + 1.0),
                    2.0,
                    2.0,
                )

        painter.end()


class ActivityPresenceController(QObject):
    """Bind one presence strip to preparation and real browser execution."""

    def __init__(self, window: Any) -> None:
        super().__init__(window)
        self.window = window
        self.console = window.console
        self.widget = ActivityPresence(self.console)
        self._prep_running = False
        self._real_running = False
        self._prep_percent = 0
        self._real_percent = 0
        self._prep_detail = "等待准备流程"
        self._real_detail = "等待真实填写"

        layout = self.console.layout()
        if not isinstance(layout, QVBoxLayout):
            raise RuntimeError("AcceptanceConsole expected QVBoxLayout")
        layout.insertWidget(1, self.widget)
        self.console.setMinimumHeight(self.console.minimumHeight() + self.widget.height() + 4)
        if self.console.maximumHeight() < 16_000_000:
            self.console.setMaximumHeight(self.console.maximumHeight() + self.widget.height() + 4)

        prep = window.runner
        prep.running_changed.connect(self._on_prep_running)
        prep.progress_changed.connect(self._on_prep_progress)
        prep.phase_event.connect(self._on_phase_event)
        prep.completed.connect(self._on_prep_completed)
        prep.failed.connect(self._on_prep_failed)

        real = getattr(window, "execution_runner", None)
        if real is not None:
            real.running_changed.connect(self._on_real_running)
            real.progress_changed.connect(self._on_real_progress)
            real.completed.connect(self._on_real_completed)
            real.failed.connect(self._on_real_failed)

        self.widget.set_activity("STANDBY", "等待准备流程", 0, active=False)

    def _on_prep_running(self, running: bool) -> None:
        self._prep_running = bool(running)
        if running:
            self._prep_percent = 0
            self._prep_detail = "启动商品准备流程"
            self.widget.set_activity("PREPARING", self._prep_detail, 0, active=True)
        elif not self._real_running and self._prep_percent < 100:
            self.widget.set_activity("STANDBY", self._prep_detail, self._prep_percent, active=False)

    def _on_prep_progress(self, percent: int, text: str) -> None:
        self._prep_percent = max(0, min(100, int(percent)))
        self._prep_detail = str(text or "准备中")
        self.widget.set_activity(
            "PREPARING",
            self._prep_detail,
            self._prep_percent,
            active=self._prep_running,
        )

    def _on_phase_event(self, event: dict[str, Any]) -> None:
        if str(event.get("status") or "").casefold() != "running":
            return
        phase = str(event.get("phase") or "")
        title = _STAGE_LABELS.get(phase, phase or "workflow")
        detail = str(event.get("detail") or "").strip()
        self._prep_detail = f"{title} · {detail}" if detail else title
        self.widget.set_activity(
            "PREPARING",
            self._prep_detail,
            self._prep_percent,
            active=True,
        )

    def _on_prep_completed(self, result: Any) -> None:
        ready = int(getattr(result, "ready", 0) or 0)
        blocked = int(getattr(result, "blocked", 0) or 0)
        self._prep_percent = 100
        detail = f"准备完成 · READY {ready} · BLOCKED {blocked} · 等待真实填写授权"
        self.widget.set_activity("READY", detail, 100, active=False)

    def _on_prep_failed(self, message: str) -> None:
        self.widget.set_activity("FAILED", str(message or "准备流程失败"), self._prep_percent, active=False)

    def _on_real_running(self, running: bool) -> None:
        self._real_running = bool(running)
        if running:
            self._real_percent = 0
            self._real_detail = "strict rebind / browser execution"
            self.widget.set_activity("FILLING", self._real_detail, 0, active=True)
        else:
            self.widget.set_activity("FILLING", self._real_detail, self._real_percent, active=False)

    def _on_real_progress(self, percent: int, text: str) -> None:
        self._real_percent = max(0, min(100, int(percent)))
        self._real_detail = str(text or "真实填写中")
        self.widget.set_activity(
            "FILLING",
            self._real_detail,
            self._real_percent,
            active=self._real_running,
        )

    def _on_real_completed(self, report: dict[str, Any]) -> None:
        totals = report.get("field_totals") or {}
        if not isinstance(totals, dict):
            totals = {}
        attempted = int(totals.get("writes_attempted", 0) or 0)
        persisted = int(totals.get("persisted_verified", 0) or 0)
        photos = report.get("photo_upload") or {}
        photo_count = 0
        if isinstance(photos, dict):
            photo_count = int(photos.get("persisted", 0) or 0)
        detail = f"完成 · {attempted} writes · {persisted} persisted · {photo_count} photos · QC locked"
        self._real_percent = 100
        self.widget.set_activity("COMPLETE", detail, 100, active=False)

    def _on_real_failed(self, message: str) -> None:
        self.widget.set_activity("FAILED", str(message or "真实填写失败"), self._real_percent, active=False)


def install_activity_presence(window: Any) -> ActivityPresenceController:
    existing = getattr(window, "_activity_presence_controller", None)
    if isinstance(existing, ActivityPresenceController):
        return existing
    controller = ActivityPresenceController(window)
    window._activity_presence_controller = controller
    return controller
