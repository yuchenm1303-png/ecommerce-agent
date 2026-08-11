from __future__ import annotations

import math
import re
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

# The read-only preparation owns the first 45% of the end-to-end experience.
# Real browser execution resumes from exactly that point instead of resetting to
# zero, so the user sees one monotonic product-level 0 -> 100 timeline.
_PREP_OVERALL_END = 45
_REAL_OVERALL_START = 45
_REAL_OVERALL_SPAN = 55

_PREP_PHASE_START = {
    "scan": 2,
    "cold": 26,
    "hot": 36,
    "plan": 46,
}
_PREP_PHASE_COMPLETE = {
    "scan": 24,
    "cold": 34,
    "hot": 44,
    "plan": 100,
}

_SECTION_LINE = re.compile(
    r"^(Price, Stock and Shipping Information|Product Description|Additional Description):"
)
_PHOTO_LINE = re.compile(
    r"^photos:\s+status=(\S+)\s+requested=(\d+)\s+attempted=(\d+)\s+staged=(\d+)"
)


class ActivityPresence(QWidget):
    """Compact time-driven heartbeat for truthful end-to-end progress.

    Business telemetry owns the target percentage. The widget only eases its
    painted fill toward that target and animates decorative liveness cues. It
    never increments work progress on a timer.
    """

    _FRAME_MS = 16
    _PROGRESS_TAU_S = 0.18
    _SWEEP_PERIOD_S = 2.25
    _PULSE_PERIOD_S = 1.75

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("activityPresence")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFixedHeight(42)
        self.setMinimumWidth(320)

        self.mode = "STANDBY"
        self.detail = "等待任务"
        self.meta = "总进度 · 0%"
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
        return int(round(self.target_percent))

    def set_activity(
        self,
        mode: str,
        detail: str,
        percent: int,
        *,
        active: bool,
        meta: str = "",
    ) -> None:
        next_target = float(max(0, min(100, int(percent))))
        was_active = self.active

        self.mode = str(mode or "STANDBY").upper()
        self.detail = str(detail or "").strip() or "等待任务"
        self.meta = str(meta or "").strip() or f"总进度 · {int(round(next_target))}%"
        self.target_percent = next_target
        self.active = bool(active)

        # A new product may legitimately reset a previous 100% state. Within one
        # run the controller is monotonic, so backwards animation is never used
        # as fake work.
        if self.active and (not was_active or self.target_percent + 0.5 < self.display_percent):
            self.display_percent = self.target_percent
            self._motion_time_s = 0.0

        if self.active:
            self._last_frame_s = time.perf_counter()
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()
            self.display_percent = self.target_percent
            self._motion_time_s = 0.0

        self.update()

    def _animate(self) -> None:
        now = time.perf_counter()
        dt = max(0.0, min(0.050, now - self._last_frame_s))
        self._last_frame_s = now
        self._motion_time_s += dt

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

        shell = QLinearGradient(0.0, 0.0, 0.0, height)
        shell.setColorAt(0.0, QColor(14, 29, 50, 76))
        shell.setColorAt(1.0, QColor(5, 14, 29, 92))
        painter.setPen(QPen(QColor(255, 255, 255, 28), 1.0))
        painter.setBrush(shell)
        painter.drawRoundedRect(outer, 9.0, 9.0)

        color = QColor(_MODE_COLORS.get(self.mode, QColor("#d8e8ff")))
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
            QRectF(25.0, 0.0, 94.0, 22.0),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self.mode,
        )

        detail_font = painter.font()
        detail_font.setBold(False)
        painter.setFont(detail_font)
        painter.setPen(QColor(255, 255, 255, 215))
        detail_left = 114.0
        detail_right = max(detail_left + 20.0, width - 73.0)
        detail_width = max(20, int(detail_right - detail_left))
        detail_text = painter.fontMetrics().elidedText(
            self.detail,
            Qt.TextElideMode.ElideRight,
            detail_width,
        )
        painter.drawText(
            QRectF(detail_left, 0.0, float(detail_width), 22.0),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            detail_text,
        )

        painter.setPen(QColor(255, 255, 255, 205))
        painter.drawText(
            QRectF(max(0.0, width - 66.0), 0.0, 55.0, 22.0),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
            f"{int(round(self.target_percent))}%",
        )

        meta_font = painter.font()
        meta_font.setPointSizeF(max(7.0, meta_font.pointSizeF() - 1.0))
        painter.setFont(meta_font)
        painter.setPen(QColor(218, 232, 250, 150))
        meta_width = max(20, int(width - 37.0))
        meta_text = painter.fontMetrics().elidedText(
            self.meta,
            Qt.TextElideMode.ElideRight,
            meta_width,
        )
        painter.drawText(
            QRectF(25.0, 17.0, float(meta_width), 16.0),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            meta_text,
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

        # This shimmer communicates liveness while a real operation is waiting;
        # it never changes completed_w or target_percent.
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
    """Project preparation + real execution into one truthful 0-100 timeline."""

    def __init__(self, window: Any) -> None:
        super().__init__(window)
        self.window = window
        self.console = window.console
        self.widget = ActivityPresence(self.console)

        self._prep_running = False
        self._real_running = False
        self._prep_internal = 0
        self._real_internal = 0
        self._prep_detail = "等待准备流程"
        self._real_detail = "等待真实填写"
        self._real_field_seen = False
        self._real_field_done = 0
        self._real_field_total = 0
        self._real_sections_done: set[str] = set()

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
        prep.log.connect(self._on_prep_log)
        prep.completed.connect(self._on_prep_completed)
        prep.failed.connect(self._on_prep_failed)

        real = getattr(window, "execution_runner", None)
        if real is not None:
            real.running_changed.connect(self._on_real_running)
            real.progress_changed.connect(self._on_real_progress)
            real.log.connect(self._on_real_log)
            real.completed.connect(self._on_real_completed)
            real.failed.connect(self._on_real_failed)

        self.widget.set_activity("STANDBY", "等待准备流程", 0, active=False, meta="总进度 · 等待商品任务")

    @staticmethod
    def _prep_overall(internal: int) -> int:
        return round(_PREP_OVERALL_END * max(0, min(100, internal)) / 100)

    @staticmethod
    def _real_overall(internal: int) -> int:
        bounded = max(0, min(100, internal))
        return _REAL_OVERALL_START + round(_REAL_OVERALL_SPAN * bounded / 100)

    def _set_prep(self, internal: int, detail: str, *, active: bool, meta: str = "") -> None:
        self._prep_internal = max(self._prep_internal, max(0, min(100, int(internal))))
        self._prep_detail = detail
        overall = self._prep_overall(self._prep_internal)
        self.widget.set_activity(
            "PREPARING" if active else "STANDBY",
            detail,
            overall,
            active=active,
            meta=meta or f"准备阶段 {self._prep_internal}% · 总进度 {overall}%",
        )

    def _set_real(self, internal: int, detail: str, *, active: bool, meta: str = "") -> None:
        self._real_internal = max(self._real_internal, max(0, min(100, int(internal))))
        self._real_detail = detail
        overall = self._real_overall(self._real_internal)
        self.widget.set_activity(
            "FILLING",
            detail,
            overall,
            active=active,
            meta=meta or f"真实填写 {self._real_internal}% · 总进度 {overall}%",
        )

    def _on_prep_running(self, running: bool) -> None:
        self._prep_running = bool(running)
        if running:
            self._prep_internal = 0
            self._set_prep(0, "启动商品准备流程", active=True, meta="准备阶段 · 初始化")
        elif not self._real_running and self._prep_internal < 100:
            overall = self._prep_overall(self._prep_internal)
            self.widget.set_activity(
                "STANDBY",
                self._prep_detail,
                overall,
                active=False,
                meta=f"准备阶段已停止 · 总进度 {overall}%",
            )

    def _on_prep_progress(self, percent: int, text: str) -> None:
        # Existing phase percentage remains a fallback for partial/diagnostic
        # modes. Full-mode detail comes from phase/log telemetry below.
        if str(getattr(self.window.runner, "mode", "full")) != "full":
            self._set_prep(int(percent), str(text or "准备中"), active=self._prep_running)

    def _on_phase_event(self, event: dict[str, Any]) -> None:
        phase = str(event.get("phase") or "")
        status = str(event.get("status") or "").casefold()
        if phase not in _STAGE_LABELS:
            return
        title = _STAGE_LABELS[phase]
        index = int(event.get("index") or 0)
        elapsed = float(event.get("elapsed_s") or 0.0)

        if status == "running":
            detail = {
                "scan": "Source Capture · 正在采集供应商商品证据",
                "cold": "Step 1 · 正在确认 Makro Vertical",
                "hot": "Step 2 · 正在确认 Brand",
                "plan": "Step 3 · 正在扫描 live schema",
            }[phase]
            self._set_prep(
                _PREP_PHASE_START[phase],
                detail,
                active=True,
                meta=f"准备阶段 · milestone {index}/4 · {title}",
            )
            return

        if status in {"completed", "skipped"}:
            self._set_prep(
                _PREP_PHASE_COMPLETE[phase],
                f"{title} · 完成",
                active=self._prep_running,
                meta=f"准备阶段 · milestone {index}/4 · {elapsed:.1f}s",
            )

    def _on_prep_log(self, line: str) -> None:
        text = str(line or "")
        if "STEP 3 CURRENT RESOLVER · COLD" in text:
            self._set_prep(60, "Resolver · Cold · 正在解析商品字段", active=True, meta="准备阶段 · live schema 已就绪 · Resolver cold")
        elif "STEP 3 CURRENT RESOLVER · HOT/CACHE" in text:
            self._set_prep(78, "Resolver · Hot/Cache · 正在补充剩余字段", active=True, meta="准备阶段 · Resolver hot/cache")
        elif "STEP 3 CURRENT READ-ONLY FILL PLAN" in text:
            self._set_prep(90, "Fill Plan · 正在生成最终 READY / BLOCKED 计划", active=True, meta="准备阶段 · 构建最终 Fill Plan")

    def _on_prep_completed(self, result: Any) -> None:
        ready = int(getattr(result, "ready", 0) or 0)
        blocked = int(getattr(result, "blocked", 0) or 0)
        self._prep_internal = 100
        detail = f"准备完成 · READY {ready} · BLOCKED {blocked} · 等待真实填写授权"
        self.widget.set_activity(
            "READY",
            detail,
            _PREP_OVERALL_END,
            active=False,
            meta=f"准备阶段 100% · 总进度 {_PREP_OVERALL_END}% · 下一步：真实填写",
        )

    def _on_prep_failed(self, message: str) -> None:
        overall = self._prep_overall(self._prep_internal)
        self.widget.set_activity(
            "FAILED",
            str(message or "准备流程失败"),
            overall,
            active=False,
            meta=f"失败位置 · 准备阶段 {self._prep_internal}% · 总进度 {overall}%",
        )

    def _expected_real_field_total(self) -> int:
        result = getattr(self.window, "current_result", None)
        ready = int(getattr(result, "ready", 0) or 0) if result is not None else 0
        support = getattr(self.window, "_required_input_support", None)
        required_inputs = getattr(support, "inputs", {}) if support is not None else {}
        fallback_count = len(required_inputs) if isinstance(required_inputs, dict) else 0
        return max(0, ready + fallback_count)

    def _real_meta(self) -> str:
        field_part = (
            f"字段 {self._real_field_done}/{self._real_field_total}"
            if self._real_field_total > 0
            else f"字段 {self._real_field_done}"
        )
        return f"{field_part} · Section {len(self._real_sections_done)}/3 · 总进度 {self._real_overall(self._real_internal)}%"

    def _on_real_running(self, running: bool) -> None:
        self._real_running = bool(running)
        if running:
            self._real_internal = 0
            self._real_field_seen = False
            self._real_field_done = 0
            self._real_field_total = self._expected_real_field_total()
            self._real_sections_done.clear()
            self._set_real(0, "Pre-write · strict rebind / live schema verification", active=True, meta=self._real_meta())
        elif self._real_internal < 100:
            self._set_real(self._real_internal, self._real_detail, active=False, meta=self._real_meta())

    def _on_real_progress(self, percent: int, text: str) -> None:
        # Before field telemetry appears, retain the canonical runner's pre-write
        # milestones. Once GUI_EXEC_FIELD starts, field-level events are more
        # precise than the old section-level 20-point jumps and take precedence.
        if not self._real_field_seen:
            fallback = min(10, max(0, int(percent)))
            self._set_real(fallback, str(text or "真实填写中"), active=self._real_running, meta=self._real_meta())
        elif int(percent) >= 95:
            self._set_real(98, str(text or "写入报告"), active=self._real_running, meta=self._real_meta())

    def _on_real_log(self, line: str) -> None:
        text = str(line or "").strip()
        if text.startswith("GUI_EXEC_FIELD\t"):
            parts = text.split("\t")
            if len(parts) < 4:
                return
            state = parts[1].upper()
            section = parts[2] or "Makro Step 3"
            label = parts[3] or "field"
            self._real_field_seen = True
            if self._real_field_total <= 0:
                self._real_field_total = max(1, self._real_field_done + 1)

            if state == "START":
                next_index = min(self._real_field_done + 1, self._real_field_total)
                progress = 10 + round(68 * self._real_field_done / max(1, self._real_field_total))
                self._set_real(
                    progress,
                    f"{section} · 正在填写 {label}",
                    active=True,
                    meta=f"字段 {next_index}/{self._real_field_total} · 当前：{label} · 总进度 {self._real_overall(progress)}%",
                )
                return

            if state == "COMPLETE":
                self._real_field_done += 1
                if self._real_field_done > self._real_field_total:
                    self._real_field_total = self._real_field_done
                progress = 10 + round(68 * self._real_field_done / max(1, self._real_field_total))
                result = parts[4] if len(parts) > 4 else "done"
                self._set_real(
                    progress,
                    f"{section} · {label} · {result}",
                    active=True,
                    meta=self._real_meta(),
                )
                return

        section_match = _SECTION_LINE.match(text)
        if section_match:
            section = section_match.group(1)
            self._real_sections_done.add(section)
            self._set_real(
                self._real_internal,
                f"{section} · Save / reopen verify 完成",
                active=True,
                meta=self._real_meta(),
            )
            return

        photo_match = _PHOTO_LINE.match(text)
        if photo_match:
            status, requested, attempted, staged = photo_match.groups()
            self._set_real(
                95,
                f"Product Photos · {status}",
                active=True,
                meta=f"图片 {staged}/{requested} staged · attempted {attempted} · 总进度 {self._real_overall(95)}%",
            )
            return

        if "MAKRO STEP 3 DIRECT ACCEPTANCE" in text:
            self._set_real(8, "Pre-write checks passed · 开始浏览器真实填写", active=True, meta=self._real_meta())
        elif "ACCEPTANCE COMPLETE" in text or "PREVIEW READY" in text:
            self._set_real(98, "最终校验完成 · 正在写执行报告", active=True, meta=self._real_meta())

    def _on_real_completed(self, report: dict[str, Any]) -> None:
        totals = report.get("field_totals") or {}
        if not isinstance(totals, dict):
            totals = {}
        attempted = int(totals.get("writes_attempted", 0) or 0)
        persisted = int(totals.get("persisted_verified", 0) or 0)
        photos = report.get("photo_upload") or {}
        photo_count = int(photos.get("persisted", 0) or 0) if isinstance(photos, dict) else 0
        detail = f"完成 · {attempted} writes · {persisted} persisted · {photo_count} photos · QC locked"
        self._real_internal = 100
        self.widget.set_activity(
            "COMPLETE",
            detail,
            100,
            active=False,
            meta=f"字段 {attempted} attempted · {persisted} persisted · 图片 {photo_count} · Send to QC 0",
        )

    def _on_real_failed(self, message: str) -> None:
        overall = self._real_overall(self._real_internal)
        self.widget.set_activity(
            "FAILED",
            str(message or "真实填写失败"),
            overall,
            active=False,
            meta=f"失败位置 · 字段 {self._real_field_done}/{max(self._real_field_total, self._real_field_done)} · 总进度 {overall}%",
        )


def install_activity_presence(window: Any) -> ActivityPresenceController:
    existing = getattr(window, "_activity_presence_controller", None)
    if isinstance(existing, ActivityPresenceController):
        return existing
    controller = ActivityPresenceController(window)
    window._activity_presence_controller = controller
    return controller