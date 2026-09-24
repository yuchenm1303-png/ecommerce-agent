"""Bridge existing GUI runner signals into the runtime/recovery event contract."""

from __future__ import annotations

import json
from typing import Any

from PySide6.QtCore import QObject, Signal

from app.makro.interruption_monitor import classify_failure_text
from app.makro.runtime_contract import (
    InterruptionKind,
    RecoveryAction,
    RuntimeEvent,
    RuntimeState,
)


SHADOW_MODE = True


class RuntimeEventBridge(QObject):
    """Observe existing workflows without changing their business behavior."""

    event_emitted = Signal(object)

    def __init__(self, window: Any) -> None:
        super().__init__(window)
        self.window = window
        self._prep_percent = 0
        self._real_percent = 0
        self._phase = ""
        self._prep_running = False
        self._real_running = False

        prep = window.runner
        prep.running_changed.connect(self._on_prep_running)
        prep.progress_changed.connect(self._on_prep_progress)
        prep.phase_event.connect(self._on_phase_event)
        prep.log.connect(self._on_log)
        prep.completed.connect(self._on_prep_completed)
        prep.failed.connect(self._on_prep_failed)

        real = getattr(window, "execution_runner", None)
        if real is not None:
            real.running_changed.connect(self._on_real_running)
            real.progress_changed.connect(self._on_real_progress)
            real.log.connect(self._on_log)
            real.completed.connect(self._on_real_completed)
            real.failed.connect(self._on_real_failed)

        browser = getattr(window, "_managed_makro_browser", None)
        if browser is not None:
            browser.status_changed.connect(self._on_browser_status)

    def _emit(self, event: RuntimeEvent) -> None:
        self.event_emitted.emit(event)

    def _overall_progress(self) -> int:
        activity = getattr(self.window, "_activity_presence_controller", None)
        widget = getattr(activity, "widget", None)
        try:
            if widget is not None:
                return max(0, min(100, int(widget.percent)))
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass
        if self._real_running or self._real_percent > 0:
            return 45 + round(55 * max(0, min(100, self._real_percent)) / 100)
        return round(45 * max(0, min(100, self._prep_percent)) / 100)

    def _on_prep_running(self, running: bool) -> None:
        self._prep_running = bool(running)
        if running:
            self._prep_percent = 0
            self._real_percent = 0
            self._emit(
                RuntimeEvent(
                    RuntimeState.RUNNING,
                    "正在准备商品",
                    "Runtime Supervisor · Shadow Mode 仅观察，不改变正常流程",
                    phase="Source Capture",
                    progress=0,
                    advisor="shadow",
                )
            )

    def _on_prep_progress(self, percent: int, text: str) -> None:
        self._prep_percent = max(self._prep_percent, max(0, min(100, int(percent))))
        self._emit(
            RuntimeEvent(
                RuntimeState.RUNNING,
                "正在准备商品",
                str(text or ""),
                phase=self._phase,
                progress=self._overall_progress(),
                advisor="system",
            )
        )

    def _on_phase_event(self, payload: dict[str, Any]) -> None:
        self._phase = str(payload.get("label") or payload.get("phase") or "")
        if str(payload.get("status") or "") == "running":
            self._emit(
                RuntimeEvent(
                    RuntimeState.RUNNING,
                    self._phase or "准备中",
                    "正常 workflow 正在运行",
                    phase=self._phase,
                    progress=self._overall_progress(),
                )
            )

    def _on_prep_completed(self, _result: Any) -> None:
        self._prep_percent = 100
        self._emit(
            RuntimeEvent(
                RuntimeState.READY,
                "准备完成 · 等待真实填写授权",
                "正常流程已到达 Fill Plan；Recovery 系统没有介入业务决策。",
                phase="Step 3 · Resolve / Fill Plan",
                progress=45,
                advisor="system",
            )
        )

    def _on_prep_failed(self, message: str) -> None:
        self._emit(
            classify_failure_text(
                message,
                phase=self._phase,
                progress=self._overall_progress(),
            )
        )

    def _on_real_running(self, running: bool) -> None:
        self._real_running = bool(running)
        if running:
            self._real_percent = 0
            self._emit(
                RuntimeEvent(
                    RuntimeState.RUNNING,
                    "正在真实填写 Makro",
                    "Recovery Shadow Mode 正在旁路观察",
                    phase="Real Execution",
                    progress=45,
                    advisor="shadow",
                )
            )

    def _on_real_progress(self, percent: int, text: str) -> None:
        self._real_percent = max(self._real_percent, max(0, min(100, int(percent))))
        self._emit(
            RuntimeEvent(
                RuntimeState.RUNNING,
                "正在真实填写 Makro",
                str(text or ""),
                phase="Real Execution",
                progress=self._overall_progress(),
            )
        )

    def _on_real_completed(self, _report: dict[str, Any]) -> None:
        self._real_percent = 100
        self._emit(
            RuntimeEvent(
                RuntimeState.COMPLETE,
                "商品任务完成",
                "字段 / Save / Photos 已结束 · Send to QC 仍保持锁定",
                phase="Complete",
                progress=100,
            )
        )

    def _on_real_failed(self, message: str) -> None:
        self._emit(
            classify_failure_text(
                message,
                phase="Real Execution",
                progress=self._overall_progress(),
            )
        )

    def _on_browser_status(self, state: str, detail: str) -> None:
        normalized = str(state or "").upper()
        if normalized == "LOGIN":
            self._emit(
                RuntimeEvent(
                    RuntimeState.WAITING_FOR_USER,
                    "Makro 需要登录",
                    detail,
                    phase=self._phase,
                    progress=self._overall_progress(),
                    interruption=InterruptionKind.LOGIN_REQUIRED,
                    suggestion="在专用 Makro Browser 完成登录；当前 Shadow Mode 不替你提交认证。",
                    action=RecoveryAction.ASK_HUMAN_LOGIN,
                    advisor="rules",
                )
            )
        elif normalized in {"OFFLINE", "ERROR"}:
            self._emit(
                RuntimeEvent(
                    RuntimeState.WARNING,
                    "Makro Browser 会话异常",
                    detail,
                    phase=self._phase,
                    progress=self._overall_progress(),
                    interruption=InterruptionKind.BROWSER_OFFLINE,
                    suggestion="Browser Session Manager 负责恢复浏览器；当前任务不会猜测其他标签页。",
                    action=RecoveryAction.WAIT,
                    advisor="rules",
                )
            )

    def _on_log(self, line: str) -> None:
        text = str(line or "").strip()
        if not text:
            return
        if text.startswith("RUNTIME_EVENT "):
            try:
                payload = json.loads(text[len("RUNTIME_EVENT "):])
                self._emit(RuntimeEvent.from_dict(payload))
            except (ValueError, TypeError, json.JSONDecodeError):
                # A malformed diagnostic line must never break the normal runner.
                pass
            return
        lowered = text.casefold()
        markers = (
            "joyride-overlay",
            "intercepts pointer events",
            "captcha",
            "human verification",
            "人机验证",
            "step 3 did not appear",
            "no unique step 3 page",
        )
        if not any(marker in lowered for marker in markers):
            return
        self._emit(
            classify_failure_text(
                text,
                phase=self._phase or ("Real Execution" if self._real_running else ""),
                progress=self._overall_progress(),
            )
        )


def install_runtime_event_bridge(window: Any) -> RuntimeEventBridge:
    existing = getattr(window, "_runtime_event_bridge", None)
    if isinstance(existing, RuntimeEventBridge):
        return existing
    bridge = RuntimeEventBridge(window)
    window._runtime_event_bridge = bridge
    return bridge


__all__ = ["RuntimeEventBridge", "SHADOW_MODE", "install_runtime_event_bridge"]
