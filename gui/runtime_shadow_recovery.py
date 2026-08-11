"""Read-only Recovery AI observer for failed Single workflows.

This is phase-1 Shadow Mode. It attaches to the already-managed Makro Edge
only after the existing workflow reports failure, captures one bounded page
observation, asks Recovery AI for a structured recommendation, and emits a
RuntimeEvent for the GUI. It never executes the proposed action.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from PySide6.QtCore import QObject, Signal
from playwright.sync_api import sync_playwright

from app.browser_page_owner import page_target_id
from app.makro.interruption_monitor import assess_observation
from app.makro.page_observation import observe_page
from app.makro.recovery_agent import RecoveryAgent
from app.makro.runtime_contract import (
    InterruptionKind,
    RecoveryAction,
    RuntimeEvent,
    RuntimeState,
)
from app.providers.registry import ProviderConfig, build_semantic_provider


_EXPECTED_STAGE = {
    "scan": "",
    "source capture": "",
    "cold": "vertical",
    "step 1": "vertical",
    "hot": "brand",
    "step 2": "brand",
    "plan": "product_info",
    "step 3": "product_info",
    "real execution": "product_info",
}


def _expected_stage(phase: str) -> str:
    lowered = str(phase or "").casefold()
    for marker, stage in _EXPECTED_STAGE.items():
        if marker in lowered:
            return stage
    return ""


class RuntimeShadowRecovery(QObject):
    """Analyze preserved failure scenes without mutating the browser."""

    event_emitted = Signal(object)

    def __init__(self, window: Any) -> None:
        super().__init__(window)
        self.window = window
        self._lock = threading.Lock()
        self._active = False

        window.runner.failed.connect(
            lambda message: self._start(
                str(message or ""),
                phase=str(getattr(window.runner, "current_phase", "") or ""),
                real=False,
            )
        )
        real = getattr(window, "execution_runner", None)
        if real is not None:
            real.failed.connect(
                lambda message: self._start(
                    str(message or ""),
                    phase="Real Execution",
                    real=True,
                )
            )

    def _overall_progress(self) -> int:
        activity = getattr(self.window, "_activity_presence_controller", None)
        widget = getattr(activity, "widget", None)
        try:
            if widget is not None:
                return max(0, min(100, int(widget.percent)))
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass
        return 0

    def _start(self, message: str, *, phase: str, real: bool) -> None:
        if not real and str(phase or "").casefold() in {"", "idle", "scan", "source"}:
            # Source capture failures belong to the supplier browser, not Makro.
            return
        with self._lock:
            if self._active:
                return
            self._active = True

        progress = self._overall_progress()
        self.event_emitted.emit(
            RuntimeEvent(
                RuntimeState.AI_ANALYZING,
                "Recovery AI 正在读取保留现场",
                "只读分析截图 / DOM / 页面阶段；Shadow Mode 不会执行 AI 建议。",
                phase=phase,
                progress=progress,
                advisor="ai",
            )
        )

        thread = threading.Thread(
            target=self._worker,
            args=(message, phase, real, progress),
            name="runtime-shadow-recovery",
            daemon=True,
        )
        thread.start()

    def _runner_context(self, *, real: bool) -> tuple[Any, Path | None]:
        if real:
            real_runner = getattr(self.window, "execution_runner", None)
            root = getattr(real_runner, "output_root", None)
            # RealExecutionConfig intentionally contains only browser/write
            # permissions. Reuse the completed read-only run's provider config
            # so Recovery AI uses the same user-configured service/model.
            config = getattr(self.window.runner, "config", None)
            return config, Path(root).resolve() if root else None
        runner = self.window.runner
        config = getattr(runner, "config", None)
        root = getattr(runner, "run_dir", None)
        return config, Path(root).resolve() if root else None

    @staticmethod
    def _provider(config: Any):
        if config is None:
            raise RuntimeError("Recovery Shadow 没有可复用的 AI provider 配置")
        provider_name = str(getattr(config, "provider", "openai-compatible") or "openai-compatible")
        model = str(
            getattr(config, "local_model", "")
            or getattr(config, "model", "")
            or ""
        ).strip()
        base_url = str(getattr(config, "base_url", "") or "")
        api_key_env = str(getattr(config, "api_key_env", "AI_API_KEY") or "AI_API_KEY")
        if not model:
            raise RuntimeError("Recovery Shadow 没有可用模型")
        return build_semantic_provider(
            ProviderConfig(
                provider=provider_name,
                model=model,
                api_key_env=api_key_env,
                base_url=base_url,
                structured_mode="json_object",
                request_timeout_seconds=30.0,
                enable_thinking=False if provider_name == "openai-compatible" else None,
            )
        )

    @staticmethod
    def _makro_pages(browser: Any) -> list[Any]:
        pages: list[Any] = []
        for context in list(getattr(browser, "contexts", []) or []):
            for page in list(getattr(context, "pages", []) or []):
                try:
                    host = urlparse(str(page.url or "")).hostname
                    if host == "seller.makro.co.za" and not page.is_closed():
                        pages.append(page)
                except Exception:
                    continue
        return pages

    def _worker(self, message: str, phase: str, real: bool, progress: int) -> None:
        try:
            config, output_root = self._runner_context(real=real)
            port = int(getattr(config, "makro_cdp_port", 9222) or 9222)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            root = (
                (output_root / "runtime-shadow" / stamp)
                if output_root is not None
                else Path.cwd() / "logs" / "runtime-shadow" / stamp
            )
            root.mkdir(parents=True, exist_ok=True)

            with sync_playwright() as playwright:
                browser = playwright.chromium.connect_over_cdp(
                    f"http://127.0.0.1:{port}",
                    timeout=5_000,
                )
                pages = self._makro_pages(browser)
                if len(pages) != 1:
                    self.event_emitted.emit(
                        RuntimeEvent(
                            RuntimeState.WARNING,
                            "Recovery Shadow 未选择页面",
                            f"当前可见 Makro 页面={len(pages)}；为避免跨 Job 猜 target，AI 分析已跳过。",
                            phase=phase,
                            progress=progress,
                            interruption=InterruptionKind.UNKNOWN,
                            suggestion="保留现场；后续由 exact target ownership 接入多标签页 Recovery。",
                            action=RecoveryAction.NONE,
                            advisor="shadow",
                        )
                    )
                    return

                page = pages[0]
                try:
                    target_id = page_target_id(page)
                except Exception:
                    target_id = ""
                observation = observe_page(
                    page,
                    expected_stage=_expected_stage(phase),
                    target_id=target_id,
                    output_dir=root,
                )
                (root / "observation.json").write_text(
                    json.dumps(observation.as_ai_context(), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

                assessment = assess_observation(observation)
                if assessment.kind in {
                    InterruptionKind.HUMAN_VERIFICATION,
                    InterruptionKind.LOGIN_REQUIRED,
                    InterruptionKind.KNOWN_OVERLAY,
                }:
                    state = (
                        RuntimeState.WAITING_FOR_USER
                        if assessment.kind
                        in {
                            InterruptionKind.HUMAN_VERIFICATION,
                            InterruptionKind.LOGIN_REQUIRED,
                        }
                        else RuntimeState.WARNING
                    )
                    self.event_emitted.emit(
                        RuntimeEvent(
                            state,
                            assessment.title,
                            assessment.detail,
                            phase=phase,
                            progress=progress,
                            interruption=assessment.kind,
                            suggestion=assessment.suggestion,
                            action=assessment.action,
                            confidence=assessment.confidence,
                            advisor="rules",
                            target_id=target_id,
                        )
                    )
                    return

                provider = self._provider(config)
                proposal = RecoveryAgent(provider).analyze(
                    observation,
                    last_successful_action=phase,
                    job_context={
                        "error": message,
                        "mode": "real" if real else str(getattr(self.window.runner, "mode", "full")),
                        "product_url": str(getattr(config, "product_url", "") or ""),
                    },
                )
                (root / "proposal.json").write_text(
                    json.dumps(proposal.as_dict(), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                state = (
                    RuntimeState.WAITING_FOR_USER
                    if proposal.requires_user
                    else RuntimeState.WARNING
                )
                expected = (
                    f"预期恢复后：{proposal.expected_after}"
                    if proposal.expected_after
                    else "等待 deterministic verifier 确认恢复结果"
                )
                self.event_emitted.emit(
                    RuntimeEvent(
                        state,
                        "Recovery AI 建议已生成",
                        proposal.explanation or message,
                        phase=phase,
                        progress=progress,
                        interruption=proposal.interruption,
                        suggestion=f"{proposal.action.value} · {expected}",
                        action=proposal.action,
                        confidence=proposal.confidence,
                        requires_user=proposal.requires_user,
                        advisor="ai",
                        target_id=target_id,
                    )
                )
        except Exception as exc:
            self.event_emitted.emit(
                RuntimeEvent(
                    RuntimeState.WARNING,
                    "Recovery Shadow 分析不可用",
                    str(exc),
                    phase=phase,
                    progress=progress,
                    interruption=InterruptionKind.UNKNOWN,
                    suggestion="原 workflow 的失败结果保持不变；AI 分析失败不会触发任何浏览器动作。",
                    action=RecoveryAction.NONE,
                    advisor="shadow",
                )
            )
        finally:
            with self._lock:
                self._active = False


def install_runtime_shadow_recovery(window: Any) -> RuntimeShadowRecovery:
    existing = getattr(window, "_runtime_shadow_recovery", None)
    if isinstance(existing, RuntimeShadowRecovery):
        return existing
    recovery = RuntimeShadowRecovery(window)
    window._runtime_shadow_recovery = recovery
    return recovery


__all__ = ["RuntimeShadowRecovery", "install_runtime_shadow_recovery"]
