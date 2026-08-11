from __future__ import annotations

import math
from typing import Any

from PySide6.QtCore import QObject, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen
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
    """Small, low-cost heartbeat for real workflow progress.

    The percentage is always supplied by the real runner. Only the shimmer and
    pulse are decorative, so motion communicates liveness without inventing
    progress. Repaints are confined to this 30px-high widget.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("activityPresence")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFixedHeight(30)
        self.setMinimumWidth(320)

        self.mode = "STANDBY"
        self.detail = "等待任务"
        self.percent = 0
        self.active = False
        self._sweep = 0.0
        self._pulse_phase = 0.0

        self._timer = QTimer(self)
        self._timer.setInterval(90)
        self._timer.timeout.connect(self._animate)

    def set_activity(self, mode: str, detail: str, percent: int, *, active: bool) -> None:
        self.mode = str(mode or "STANDBY").upper()
        self.detail = str(detail or "").strip() or "等待任务"
        self.percent = max(0, min(100, int(percent)))
        self.active = bool(active)
        if self.active:
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()
            self._sweep = 0.0
            self._pulse_phase = 0.0
        self.update()

    def _animate(self) -> None:
        self._sweep = (self._sweep + 0.045) % 1.0
        self._pulse_phase = (self._pulse_phase + 0.34) % math.tau
        self.update()

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        outer = QRectF(0.5, 0.5, max(1.0, self.width() - 1.0), max(1.0, self.height() - 1.0))
        painter.setPen(QPen(QColor(255, 255, 255, 30), 1.0))
        painter.setBrush(QColor(8, 18, 35, 74))
        painter.drawRoundedRect(outer, 8.0, 8.0)

        color = QColor(_MODE_COLORS.get(self.mode, QColor("#d8e8ff")))
        pulse = 0.78
        if self.active:
            pulse = 0.68 + 0.30 * ((math.sin(self._pulse_phase) + 1.0) * 0.5)
        dot = QColor(color)
        dot.setAlpha(int(255 * pulse))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(dot)
        painter.drawEllipse(QRectF(10.0, 10.0, 8.0, 8.0))

        font = painter.font()
        font.setBold(True)
        font.setPointSizeF(max(8.0, font.pointSizeF() - 0.5))
        painter.setFont(font)
        painter.setPen(color)
        painter.drawText(QRectF(25.0, 2.0, 92.0, 22.0), Qt.AlignmentFlag.AlignVCenter, self.mode)

        detail_font = painter.font()
        detail_font.setBold(False)
        painter.setFont(detail_font)
        painter.setPen(QColor(255, 255, 255, 205))
        detail_left = 112.0
        detail_right = max(detail_left + 20.0, self.width() - 68.0)
        detail_width = max(20, int(detail_right - detail_left))
        elided = painter.fontMetrics().elidedText(
            self.detail,
            Qt.TextElideMode.ElideRight,
            detail_width,
        )
        painter.drawText(
            QRectF(detail_left, 2.0, float(detail_width), 22.0),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            elided,
        )

        painter.setPen(QColor(255, 255, 255, 185))
        painter.drawText(
            QRectF(max(0.0, self.width() - 61.0), 2.0, 50.0, 22.0),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
            f"{self.percent}%",
        )

        track_x = 10.0
        track_y = self.height() - 4.0
        track_w = max(1.0, self.width() - 20.0)
        track_h = 2.0
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 24))
        painter.drawRoundedRect(QRectF(track_x, track_y, track_w, track_h), 1.0, 1.0)

        completed_w = track_w * (self.percent / 100.0)
        if completed_w > 0.0:
            fill = QColor(color)
            fill.setAlpha(120)
            painter.setBrush(fill)
            painter.drawRoundedRect(QRectF(track_x, track_y, completed_w, track_h), 1.0, 1.0)

        if self.active:
            sweep_x = track_x + track_w * self._sweep
            glow_w = 54.0
            left = max(track_x, sweep_x - glow_w)
            right = min(track_x + track_w, sweep_x + glow_w)
            if right > left:
                shimmer = QLinearGradient(left, 0.0, right, 0.0)
                transparent = QColor(color)
                transparent.setAlpha(0)
                bright = QColor(color)
                bright.setAlpha(220)
                shimmer.setColorAt(0.0, transparent)
                shimmer.setColorAt(0.5, bright)
                shimmer.setColorAt(1.0, transparent)
                painter.setBrush(shimmer)
                painter.drawRoundedRect(QRectF(left, track_y, right - left, track_h), 1.0, 1.0)

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
