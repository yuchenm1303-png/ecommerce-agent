from __future__ import annotations

import math
import re
import time
from typing import Any

from PySide6.QtCore import QObject, QTimer, Qt
from PySide6.QtWidgets import QSizePolicy


_PHASE_START = {"scan": 2.0, "cold": 26.0, "hot": 36.0, "plan": 46.0}
_PHASE_COMPLETE = {"scan": 24.0, "cold": 34.0, "hot": 44.0, "plan": 100.0}
_PHASE_DETAIL = {
    "scan": "Source Capture · 采集供应商证据",
    "cold": "Step 1 · 确认 Makro Vertical",
    "hot": "Step 2 · 确认 Brand",
    "plan": "Step 3 · Resolver / Fill Plan",
}

# Hard ceilings are real backend checkpoints. The visible activity head may move
# inside the current band, but it never crosses the next checkpoint until the
# backend emits evidence that the step actually advanced.
_SEGMENTS = {
    "scan": (2.0, 24.0, "Source Capture / Product Identity"),
    "cold": (26.0, 34.0, "Step 1 · Vertical"),
    "hot": (36.0, 44.0, "Step 2 · Brand"),
    "plan_scan": (46.0, 58.0, "Step 3 · Live Schema"),
    "resolver_cold_capture": (58.0, 62.0, "Resolver Cold · Source"),
    "resolver_cold_image": (62.0, 67.0, "Resolver Cold · Image Evidence"),
    "resolver_cold_facts": (67.0, 72.0, "Resolver Cold · Product Facts"),
    "resolver_cold_web": (72.0, 74.0, "Resolver Cold · Web Fill"),
    "resolver_cold_inference": (74.0, 76.0, "Resolver Cold · Inference"),
    "resolver_hot_capture": (77.0, 80.0, "Resolver Hot · Source Cache"),
    "resolver_hot_image": (80.0, 84.0, "Resolver Hot · Image Evidence"),
    "resolver_hot_facts": (84.0, 88.0, "Resolver Hot · Product Facts"),
    "resolver_hot_web": (88.0, 90.0, "Resolver Hot · Web Fill"),
    "resolver_hot_inference": (90.0, 92.0, "Resolver Hot · Inference"),
    "fill_plan": (93.0, 99.0, "Fill Plan"),
}

# key -> (sub-segment, cold checkpoint, hot checkpoint, short label)
_RESOLVER_CHECKPOINTS = {
    "capture_start": ("capture", 58.0, 77.0, "1/5 · Source"),
    "capture_done": ("capture", 61.0, 79.0, "1/5 · Source 完成"),
    "direct_start": ("image", 62.0, 80.0, "2/5 · Image Evidence"),
    "image_start": ("image", 63.0, 81.0, "2/5 · Image Evidence · AI"),
    "image_done": ("image", 66.0, 83.0, "2/5 · Image Evidence 完成"),
    "compact_done": ("facts", 67.0, 84.0, "Compact Evidence 完成"),
    "facts_start": ("facts", 68.0, 85.0, "3/5 · Product Facts · AI"),
    "facts_done": ("facts", 71.0, 87.5, "3/5 · Product Facts 完成"),
    "web_start": ("web", 72.0, 88.0, "4/5 · Web Fill"),
    "web_done": ("web", 73.5, 89.5, "4/5 · Web Fill 完成"),
    "inference_start": ("inference", 74.0, 90.0, "5/5 · Inference · AI"),
    "inference_done": ("inference", 75.0, 91.0, "5/5 · Inference 完成"),
    "complete": ("inference", 76.0, 92.0, "Resolver 本轮完成"),
}

_AI_STILL_RUNNING = re.compile(
    r"AI still running:\s*elapsed=([0-9.]+)s(?:\s*/\s*deadline=([0-9.]+)s)?",
    re.IGNORECASE,
)
_AI_CONNECTION = re.compile(r"AI connection established at\s*([0-9.]+)s", re.IGNORECASE)
_AI_FIRST_OUTPUT = re.compile(r"AI first output received at\s*([0-9.]+)s", re.IGNORECASE)
_AI_RESPONSE_COMPLETE = re.compile(r"AI response complete at\s*([0-9.]+)s", re.IGNORECASE)
_AI_PREFIX = re.compile(r"^\[(AI|IMAGE|LOCAL|WEB|INFERENCE)\]\s+(.*)$", re.IGNORECASE)


class DetailedPreparationProgress(QObject):
    """Dense live progress for the preparation-only bar.

    ``_confirmed`` is business truth. ``_live`` is a visual activity head that
    smoothly approaches the current hard checkpoint ceiling while work is alive.
    The label always shows both values. The upper end-to-end Activity Presence
    receives only ``_confirmed`` and therefore never advances on timer pacing.
    """

    _TICK_MS = 80
    _FOLLOW_TAU_S = 0.22
    _SOFT_RESERVE = 0.55

    def __init__(self, window: Any) -> None:
        super().__init__(window)
        self.window = window
        self.runner = window.runner
        self.console = window.console

        self._confirmed = 0.0
        self._live = 0.0
        self._segment = "scan"
        self._resolver_pass = ""
        self._running = False
        self._detail = "等待任务"
        self._step_started = time.perf_counter()
        self._last_tick = self._step_started
        self._last_label = ""

        try:
            self.runner.progress_changed.disconnect(self.console._on_progress)
        except (TypeError, RuntimeError):
            pass

        # 0.1% render resolution removes the old whole-number visual stepping.
        self.console.progress.setRange(0, 1000)
        self.console.progress.setValue(0)
        self.console.progress.setFormat("%p%")
        self.console.progress_detail.setMinimumWidth(0)
        self.console.progress_detail.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )

        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(self._TICK_MS)
        self._timer.timeout.connect(self._tick)

        self.runner.running_changed.connect(self._on_running_changed)
        self.runner.progress_changed.connect(self._on_runner_progress)
        self.runner.phase_event.connect(self._on_phase_event)
        self.runner.log.connect(self._on_log)
        self.runner.completed.connect(self._on_completed)
        self.runner.failed.connect(self._on_failed)
        self._render(self._step_started)

    def _full_mode(self) -> bool:
        return str(getattr(self.runner, "mode", "full") or "full") == "full"

    def _segment_info(self) -> tuple[float, float, str]:
        return _SEGMENTS.get(self._segment, (self._confirmed, self._confirmed, "准备流程"))

    def _set_detail(self, text: str, *, reset_clock: bool = True) -> None:
        value = str(text or "").strip() or self._segment_info()[2]
        if reset_clock and value != self._detail:
            self._step_started = time.perf_counter()
        self._detail = value

    def _sync_overall(self) -> None:
        if not self._running:
            return
        controller = getattr(self.window, "_activity_presence_controller", None)
        setter = getattr(controller, "_set_prep", None)
        if callable(setter):
            confirmed = max(0, min(100, int(round(self._confirmed))))
            setter(
                confirmed,
                self._detail,
                active=True,
                meta=f"准备阶段 · 已确认 {confirmed}% · {self._detail}",
            )

    def _confirm(self, value: float, detail: str, *, force: bool = False) -> None:
        next_value = max(0.0, min(100.0, float(value)))
        if not force:
            next_value = max(self._confirmed, next_value)
        changed = abs(next_value - self._confirmed) > 0.001
        self._confirmed = next_value
        self._set_detail(detail)
        if changed:
            self._sync_overall()
        if self._confirmed >= 100.0:
            self._live = 100.0
            self.console.progress.setValue(1000)

    def _soft_target(self, now: float) -> float:
        if not self._running or not self._full_mode() or self._confirmed >= 100.0:
            return self._confirmed
        _start, cap, _label = self._segment_info()
        ceiling = max(self._confirmed, cap - self._SOFT_RESERVE)
        if ceiling <= self._confirmed:
            return self._confirmed
        elapsed = max(0.0, now - self._step_started)
        span = ceiling - self._confirmed
        tau = min(16.0, max(5.5, 4.5 + span * 0.55))
        return self._confirmed + span * (1.0 - math.exp(-elapsed / tau))

    def _tick(self) -> None:
        if not self._running:
            return
        now = time.perf_counter()
        dt = max(0.001, min(0.08, now - self._last_tick))
        self._last_tick = now
        desired = max(self._live, self._confirmed, self._soft_target(now))
        delta = desired - self._live
        if delta > 0.001:
            alpha = 1.0 - math.exp(-dt / self._FOLLOW_TAU_S)
            self._live = min(100.0, self._live + delta * alpha)
            self.console.progress.setValue(int(round(self._live * 10.0)))
        self._render(now)

    def _render(self, now: float) -> None:
        elapsed = max(0.0, now - self._step_started)
        pulse = ("●··", "·●·", "··●")[int(elapsed * 3.0) % 3]
        if self._running and self._full_mode():
            text = (
                f"准备 {self._live:04.1f}% · 已确认 {self._confirmed:02.0f}% · "
                f"{self._detail} · {elapsed:04.1f}s · {pulse}"
            )
        else:
            text = f"准备 {self._confirmed:02.0f}% · {self._detail}"
        if text != self._last_label:
            self._last_label = text
            self.console.progress_detail.setText(text)
            self.console.progress_detail.setToolTip(text)

    def _on_running_changed(self, running: bool) -> None:
        self._running = bool(running)
        now = time.perf_counter()
        self._last_tick = now
        if running:
            self._confirmed = 0.0
            self._live = 0.0
            self._segment = "scan"
            self._resolver_pass = ""
            self._set_detail("初始化商品准备流程")
            self.console.progress.setValue(0)
            self._sync_overall()
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()
            if self._confirmed >= 100.0:
                self._live = 100.0
                self.console.progress.setValue(1000)
            self._render(now)

    def _on_runner_progress(self, percent: int, text: str) -> None:
        if not self._full_mode():
            self._confirm(float(percent), str(text or "准备中"))
        elif int(percent) >= 100:
            self._confirm(100.0, "准备完成 · 等待真实填写授权")

    def _on_phase_event(self, event: dict[str, Any]) -> None:
        phase = str(event.get("phase") or "")
        if phase not in _PHASE_START or not self._full_mode():
            return
        status = str(event.get("status") or "").casefold()

        if status == "running":
            self._segment = "plan_scan" if phase == "plan" else phase
            if phase != "plan":
                self._resolver_pass = ""
            self._confirm(_PHASE_START[phase], _PHASE_DETAIL[phase])
        elif status in {"completed", "skipped"}:
            self._confirm(
                _PHASE_COMPLETE[phase],
                f"{_PHASE_DETAIL[phase].split(' · ')[0]} · 完成",
            )
        elif status == "failed":
            self._set_detail(
                "FAILED · " + str(event.get("error") or _PHASE_DETAIL[phase]),
                reset_clock=False,
            )

    def _resolver_checkpoint(self, key: str, detail: str = "") -> None:
        if self._resolver_pass not in {"cold", "hot"}:
            return
        part, cold, hot, label = _RESOLVER_CHECKPOINTS[key]
        self._segment = f"resolver_{self._resolver_pass}_{part}"
        value = cold if self._resolver_pass == "cold" else hot
        prefix = "Resolver Cold" if self._resolver_pass == "cold" else "Resolver Hot/Cache"
        self._confirm(value, detail or f"{prefix} · {label}")

    def _ai_event(self, kind: str, seconds: str = "", deadline: str = "") -> None:
        _start, cap, label = self._segment_info()
        # These are real model-call milestones. Small confirmations are allowed,
        # but they remain below the next backend checkpoint.
        increments = {
            "request": 0.0,
            "connection": 0.25,
            "first_output": 0.45,
            "heartbeat": 0.15,
            "complete": 0.65,
        }
        confirmed = min(cap - 0.7, self._confirmed + increments[kind])
        confirmed = max(self._confirmed, confirmed)
        state = {
            "request": "AI 请求已发送",
            "connection": "AI 已建立连接",
            "first_output": "AI 已返回首段结果",
            "heartbeat": "AI 正在处理",
            "complete": "AI 响应完成",
        }[kind]
        timing = f" · {seconds}s" if seconds else ""
        if seconds and deadline:
            timing = f" · {seconds}s / {deadline}s"
        self._confirm(
            confirmed,
            f"{label} · {state}{timing}",
        )

    def _handle_ai_text(self, text: str) -> bool:
        if "AI request started;" in text:
            self._ai_event("request")
            return True
        if "AI connection established at" in text:
            match = _AI_CONNECTION.search(text)
            if match:
                self._ai_event("connection", match.group(1))
            return True
        if "AI first output received at" in text:
            match = _AI_FIRST_OUTPUT.search(text)
            if match:
                self._ai_event("first_output", match.group(1))
            return True
        if "AI still running:" in text:
            match = _AI_STILL_RUNNING.search(text)
            if match:
                self._ai_event("heartbeat", match.group(1), match.group(2) or "")
            return True
        if "AI response complete at" in text:
            match = _AI_RESPONSE_COMPLETE.search(text)
            if match:
                self._ai_event("complete", match.group(1))
            return True
        return False

    def _on_log(self, line: str) -> None:
        if not self._full_mode() or not self._running:
            return
        text = str(line or "").strip()
        if not text:
            return

        # Step 3 top-level checkpoints.
        if "vertical 安全校验通过" in text:
            self._segment = "plan_scan"
            self._confirm(50.0, "Step 3 · Vertical 安全校验通过 · 扫描 live schema")
            return
        if "STEP 3 CURRENT RESOLVER · COLD" in text:
            self._resolver_pass = "cold"
            self._resolver_checkpoint("capture_start")
            return
        if "STEP 3 CURRENT RESOLVER · HOT/CACHE" in text:
            self._resolver_pass = "hot"
            self._resolver_checkpoint("capture_start")
            return
        if "STEP 3 CURRENT READ-ONLY FILL PLAN" in text:
            self._resolver_pass = ""
            self._segment = "fill_plan"
            self._confirm(93.0, "Fill Plan · 1/3 · 重新绑定 live schema 与最终决策")
            return
        if text.startswith("===== MAKRO AI-DECISION FILL PLAN"):
            self._confirm(96.0, "Fill Plan · 2/3 · live schema 二次校验通过")
            return
        if text.startswith("live_fields=") and self._segment == "fill_plan":
            self._confirm(98.0, "Fill Plan · 3/3 · 汇总 READY / BLOCKED 字段")
            return
        if text.startswith("Manifest=") and self._segment == "fill_plan":
            self._confirm(99.0, "Fill Plan · 产物已写入 · 等待流程收尾")
            return
        if text.startswith("GUI WORKFLOW COMPLETE"):
            self._confirm(100.0, "准备完成 · 等待真实填写授权")
            return

        # Canonical resolver already exposes these internal milestones in stdout.
        if self._resolver_pass in {"cold", "hot"}:
            if "===== PRIMARY PRODUCT SOURCE CAPTURE =====" in text:
                self._resolver_checkpoint("capture_start")
                return
            if text.startswith("captured exact product page:"):
                self._resolver_checkpoint("capture_done")
                return
            if "===== DIRECT PRODUCT RESOLUTION =====" in text:
                self._resolver_checkpoint("direct_start")
                return
            if text.startswith("image_evidence=DONE"):
                self._resolver_checkpoint("image_done")
                return
            if text.startswith("compact_evidence=DONE"):
                self._resolver_checkpoint("compact_done")
                return
            if text.startswith("product_facts=DONE"):
                self._resolver_checkpoint("facts_done")
                return
            if text.startswith("web_fill=START"):
                self._resolver_checkpoint("web_start")
                return
            if text.startswith(("web_fill=DONE", "web_fill=SKIP")):
                self._resolver_checkpoint("web_done")
                return
            if text.startswith("best_effort_inference=DONE"):
                self._resolver_checkpoint("inference_done")
                return
            if "===== DIRECT RESOLUTION COMPLETE =====" in text:
                self._resolver_checkpoint("complete")
                return

            if text.startswith("["):
                match = _AI_PREFIX.match(text)
                if match:
                    owner, message = match.group(1).upper(), match.group(2)
                    key = {
                        "IMAGE": "image_start",
                        "LOCAL": "facts_start",
                        "WEB": "web_start",
                        "INFERENCE": "inference_start",
                    }.get(owner)
                    if key:
                        self._resolver_checkpoint(key)
                    self._handle_ai_text(message)
                    return

        # Keep ordinary diagnostics cheap: only AI liveness reaches the regexes.
        if "AI " not in text:
            return
        if "AI connection established at" in text:
            match = _AI_CONNECTION.search(text)
            if match:
                self._ai_event("connection", match.group(1))
            return
        if "AI first output received at" in text:
            match = _AI_FIRST_OUTPUT.search(text)
            if match:
                self._ai_event("first_output", match.group(1))
            return
        if "AI still running:" in text:
            match = _AI_STILL_RUNNING.search(text)
            if match:
                self._ai_event("heartbeat", match.group(1), match.group(2) or "")
            return
        if "AI response complete at" in text:
            match = _AI_RESPONSE_COMPLETE.search(text)
            if match:
                self._ai_event("complete", match.group(1))
            return
        if "AI request started;" in text:
            self._ai_event("request")

    def _on_completed(self, _result: Any) -> None:
        if self._full_mode():
            self._confirm(100.0, "准备完成 · 等待真实填写授权")

    def _on_failed(self, message: str) -> None:
        if self._full_mode():
            self._set_detail(
                f"FAILED · {str(message or '准备流程失败')}",
                reset_clock=False,
            )
            self._render(time.perf_counter())


def install_detailed_preparation_progress(window: Any) -> DetailedPreparationProgress:
    existing = getattr(window, "_detailed_preparation_progress", None)
    if isinstance(existing, DetailedPreparationProgress):
        return existing
    controller = DetailedPreparationProgress(window)
    window._detailed_preparation_progress = controller
    return controller
