from __future__ import annotations

import re
from typing import Any

from PySide6.QtCore import QEasingCurve, QObject, QVariantAnimation
from PySide6.QtWidgets import QSizePolicy


_PHASE_START = {
    "scan": 2,
    "cold": 26,
    "hot": 36,
    "plan": 46,
}
_PHASE_COMPLETE = {
    "scan": 24,
    "cold": 34,
    "hot": 44,
    "plan": 100,
}
_PHASE_DETAIL = {
    "scan": "Source Capture · 正在采集供应商商品证据",
    "cold": "Step 1 · 正在确认 Makro Vertical",
    "hot": "Step 2 · 正在确认 Brand",
    "plan": "Step 3 · 正在准备 Resolver / Fill Plan",
}

# Detailed work bands inside the preparation-only 0 -> 100 bar. These are
# telemetry boundaries, not elapsed-time guesses. The target only advances when
# a real phase/log event is observed.
_SEGMENTS = {
    "scan": (2, 24),
    "cold": (26, 34),
    "hot": (36, 44),
    "plan_scan": (46, 58),
    "resolver_cold": (60, 76),
    "resolver_hot": (78, 88),
    "fill_plan": (90, 98),
}

_AI_STILL_RUNNING = re.compile(r"AI still running:\s*elapsed=([0-9.]+)s", re.IGNORECASE)
_AI_CONNECTION = re.compile(r"AI connection established at\s*([0-9.]+)s", re.IGNORECASE)
_AI_FIRST_OUTPUT = re.compile(r"AI first output received at\s*([0-9.]+)s", re.IGNORECASE)
_AI_RESPONSE_COMPLETE = re.compile(r"AI response complete at\s*([0-9.]+)s", re.IGNORECASE)


class DetailedPreparationProgress(QObject):
    """Drive the legacy preparation bar from real sub-task telemetry.

    The upper Activity Presence remains the end-to-end 0 -> 100 timeline where
    preparation owns 0 -> 45 and real execution owns 45 -> 100. This controller
    intentionally touches only AcceptanceConsole.progress, which remains a
    preparation-only 0 -> 100 view.

    The old ReadOnlyRunner percentage is phase-count based (0/25/50/75/100).
    In full mode we disconnect only that legacy console slot and derive a more
    granular target from phase boundaries, Resolver/Fill-Plan banners and real
    provider liveness messages. QVariantAnimation visually interpolates between
    truthful targets; no timer invents additional work progress.
    """

    _ANIMATION_BASE_MS = 180
    _ANIMATION_MAX_MS = 620

    def __init__(self, window: Any) -> None:
        super().__init__(window)
        self.window = window
        self.runner = window.runner
        self.console = window.console

        self._target = 0
        self._current_phase = ""
        self._segment = "scan"
        self._running = False

        self._animation = QVariantAnimation(self)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(self._on_animation_value)

        # AcceptanceConsole connected this construction-time slot directly to
        # the runner. Replace only that one consumer; ActivityPresence and all
        # other progress listeners remain connected.
        try:
            self.runner.progress_changed.disconnect(self.console._on_progress)
        except (TypeError, RuntimeError):
            pass

        self.console.progress.setRange(0, 100)
        self.console.progress.setValue(0)
        self.console.progress.setFormat("%p%")

        # Failure payloads can contain very long provider messages/URLs. The
        # Single workspace lives in a vertically scrolling QScrollArea whose
        # content layout uses SetMinimumSize. If this label contributes its full
        # text width, one long API error can inflate the whole page minimum width
        # and every expanding card is then clipped beyond the right edge while
        # the horizontal scrollbar is intentionally disabled. Keep the full text
        # available in the label/tooltip, but never let its sizeHint own page
        # geometry.
        self.console.progress_detail.setMinimumWidth(0)
        self.console.progress_detail.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )
        self.console.progress_detail.setText("准备 0/100 · 等待任务")

        self.runner.running_changed.connect(self._on_running_changed)
        self.runner.progress_changed.connect(self._on_runner_progress)
        self.runner.phase_event.connect(self._on_phase_event)
        self.runner.log.connect(self._on_log)
        self.runner.completed.connect(self._on_completed)
        self.runner.failed.connect(self._on_failed)

    def _set_progress_detail(self, text: str) -> None:
        value = str(text or "")
        self.console.progress_detail.setText(value)
        self.console.progress_detail.setToolTip(value)

    def _full_mode(self) -> bool:
        return str(getattr(self.runner, "mode", "full") or "full") == "full"

    def _sync_overall(self, internal: int, detail: str) -> None:
        if not self._running:
            return
        controller = getattr(self.window, "_activity_presence_controller", None)
        setter = getattr(controller, "_set_prep", None)
        if callable(setter):
            setter(
                internal,
                detail,
                active=True,
                meta=f"准备阶段 {internal}% · {detail}",
            )

    def _set_direct(self, value: int, detail: str) -> None:
        bounded = max(0, min(100, int(value)))
        self._animation.stop()
        self._target = bounded
        self.console.progress.setValue(bounded)
        self._set_progress_detail(f"准备 {bounded}/100 · {detail}")
        self._sync_overall(bounded, detail)

    def _set_target(self, value: int, detail: str, *, force: bool = False) -> None:
        bounded = max(0, min(100, int(value)))
        if not force:
            bounded = max(self._target, bounded)

        if bounded == self._target and not force:
            self._set_progress_detail(f"准备 {bounded}/100 · {detail}")
            return

        current = int(self.console.progress.value())
        self._target = bounded
        self._set_progress_detail(f"准备 {bounded}/100 · {detail}")

        if bounded < current:
            self._set_direct(bounded, detail)
            return

        delta = max(1, bounded - current)
        duration = min(
            self._ANIMATION_MAX_MS,
            self._ANIMATION_BASE_MS + delta * 14,
        )
        self._animation.stop()
        self._animation.setDuration(int(duration))
        self._animation.setStartValue(current)
        self._animation.setEndValue(bounded)
        self._animation.start()
        self._sync_overall(bounded, detail)

    def _on_animation_value(self, value: Any) -> None:
        self.console.progress.setValue(max(0, min(100, int(round(float(value))))))

    def _on_running_changed(self, running: bool) -> None:
        self._running = bool(running)
        if running:
            self._current_phase = ""
            self._segment = "scan"
            self._set_direct(0, "初始化商品准备流程")

    def _on_runner_progress(self, percent: int, text: str) -> None:
        # Diagnostic/partial modes retain the runner's normalized progress.
        # Full mode deliberately ignores the old completed_phases/4 percentage.
        if not self._full_mode():
            self._set_target(int(percent), str(text or "准备中"), force=False)
        elif int(percent) >= 100:
            self._set_target(100, "准备完成 · 等待真实填写授权")

    def _on_phase_event(self, event: dict[str, Any]) -> None:
        phase = str(event.get("phase") or "")
        if phase not in _PHASE_START:
            return
        status = str(event.get("status") or "").casefold()

        if not self._full_mode():
            return

        if status == "running":
            self._current_phase = phase
            self._segment = "plan_scan" if phase == "plan" else phase
            self._set_target(_PHASE_START[phase], _PHASE_DETAIL[phase])
            return

        if status in {"completed", "skipped"}:
            self._set_target(
                _PHASE_COMPLETE[phase],
                f"{_PHASE_DETAIL[phase].split(' · ')[0]} · 完成",
            )
            return

        if status == "failed":
            message = str(event.get("error") or _PHASE_DETAIL[phase])
            self._set_progress_detail(
                f"准备 {self._target}/100 · FAILED · {message}"
            )

    def _segment_bounds(self) -> tuple[int, int]:
        return _SEGMENTS.get(self._segment, (self._target, self._target))

    def _nudge_ai(self, kind: str, seconds: str = "") -> None:
        start, cap = self._segment_bounds()
        if cap <= start:
            return

        offsets = {
            "request": 2,
            "connection": 4,
            "first_output": 7,
        }
        label = {
            "request": "AI 请求已发送",
            "connection": "AI 已建立连接",
            "first_output": "AI 已返回首段结果",
        }.get(kind, "AI 正在处理")

        if kind == "complete":
            # Every real completed model response advances the current work band
            # a little, while remaining below the next authoritative milestone.
            target = min(cap, max(start + 9, self._target + 2))
            label = "AI 响应完成"
        else:
            target = min(cap, max(self._target, start + offsets.get(kind, 0)))

        suffix = f" · {seconds}s" if seconds else ""
        self._set_target(target, f"{self._work_label()} · {label}{suffix}")

    def _work_label(self) -> str:
        return {
            "scan": "Source Capture / Product Identity",
            "cold": "Step 1 · Vertical",
            "hot": "Step 2 · Brand",
            "plan_scan": "Step 3 · Live Schema",
            "resolver_cold": "Resolver Cold",
            "resolver_hot": "Resolver Hot/Cache",
            "fill_plan": "Fill Plan",
        }.get(self._segment, "准备流程")

    def _on_log(self, line: str) -> None:
        if not self._full_mode() or not self._running:
            return
        text = str(line or "").strip()
        if not text:
            return

        # Step 3 exposes several truthful internal milestones that used to be
        # hidden behind the single fourth phase.
        if "vertical 安全校验通过" in text:
            self._segment = "plan_scan"
            self._set_target(50, "Step 3 · Vertical 安全校验通过 · 扫描 live schema")
            return
        if "STEP 3 CURRENT RESOLVER · COLD" in text:
            self._segment = "resolver_cold"
            self._set_target(60, "Resolver Cold · 正在解析商品事实与字段")
            return
        if "STEP 3 CURRENT RESOLVER · HOT/CACHE" in text:
            self._segment = "resolver_hot"
            self._set_target(78, "Resolver Hot/Cache · 正在补充剩余字段")
            return
        if "STEP 3 CURRENT READ-ONLY FILL PLAN" in text:
            self._segment = "fill_plan"
            self._set_target(90, "Fill Plan · 正在绑定 live schema 与最终决策")
            return
        if text.startswith("GUI WORKFLOW COMPLETE"):
            self._set_target(100, "准备完成 · 等待真实填写授权")
            return

        # Most subprocess lines are ordinary diagnostics. Avoid running four
        # regular expressions for every one of them; only actual AI telemetry
        # can match the patterns below. This changes no progress semantics.
        if "AI " not in text:
            return
        if "AI request started;" in text:
            self._nudge_ai("request")
            return
        if "AI connection established at" in text:
            match = _AI_CONNECTION.search(text)
            if match is not None:
                self._nudge_ai("connection", match.group(1))
            return
        if "AI first output received at" in text:
            match = _AI_FIRST_OUTPUT.search(text)
            if match is not None:
                self._nudge_ai("first_output", match.group(1))
            return
        if "AI still running:" in text:
            match = _AI_STILL_RUNNING.search(text)
            if match is not None:
                self._set_progress_detail(
                    f"准备 {self._target}/100 · {self._work_label()} · AI处理中 {match.group(1)}s"
                )
            return
        if "AI response complete at" in text:
            match = _AI_RESPONSE_COMPLETE.search(text)
            if match is not None:
                self._nudge_ai("complete", match.group(1))

    def _on_completed(self, _result: Any) -> None:
        if self._full_mode():
            self._set_target(100, "准备完成 · 等待真实填写授权")

    def _on_failed(self, message: str) -> None:
        if not self._full_mode():
            return
        self._animation.stop()
        self._set_progress_detail(
            f"准备 {self._target}/100 · FAILED · {str(message or '准备流程失败')}"
        )


def install_detailed_preparation_progress(window: Any) -> DetailedPreparationProgress:
    existing = getattr(window, "_detailed_preparation_progress", None)
    if isinstance(existing, DetailedPreparationProgress):
        return existing
    controller = DetailedPreparationProgress(window)
    window._detailed_preparation_progress = controller
    return controller
