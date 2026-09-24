"""Pure classification helpers for Makro runtime interruptions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .page_observation import PageObservation
from .runtime_contract import (
    InterruptionKind,
    RecoveryAction,
    RuntimeEvent,
    RuntimeState,
)


@dataclass(slots=True, frozen=True)
class InterruptionAssessment:
    kind: InterruptionKind
    title: str
    detail: str
    suggestion: str
    action: RecoveryAction
    confidence: float


def _normalized_stage(value: str) -> str:
    return "".join(ch for ch in str(value or "").casefold() if ch.isalnum())


def assess_observation(observation: "PageObservation") -> InterruptionAssessment:
    if observation.human_verification:
        return InterruptionAssessment(
            InterruptionKind.HUMAN_VERIFICATION,
            "需要完成人机验证",
            "页面出现 CAPTCHA / human verification 信号。",
            "请在 Makro Browser 完成验证；恢复前不要创建新 Listing。",
            RecoveryAction.ASK_HUMAN_VERIFICATION,
            0.99,
        )
    if observation.login_required:
        return InterruptionAssessment(
            InterruptionKind.LOGIN_REQUIRED,
            "Makro 登录状态需要恢复",
            "页面出现登录表单或明确登录提示。",
            "请在当前专用 Makro Browser 登录；随后重新观察当前 Job 页面。",
            RecoveryAction.ASK_HUMAN_LOGIN,
            0.98,
        )

    overlays = " ".join(observation.overlays).casefold()
    if "joyride" in overlays:
        return InterruptionAssessment(
            InterruptionKind.KNOWN_OVERLAY,
            "Makro 引导层正在阻挡操作",
            "检测到已知 Joyride onboarding overlay。",
            "安全关闭引导层，然后用原状态检测器重新验证当前阶段。",
            RecoveryAction.DISMISS_OVERLAY,
            0.99,
        )
    if observation.overlays:
        return InterruptionAssessment(
            InterruptionKind.UNKNOWN_MODAL,
            "检测到未知弹窗或遮罩",
            "页面存在可见 modal / dialog，但当前规则尚未确认其用途。",
            "交给 Recovery AI 判断用途；第一阶段 Shadow Mode 不自动点击。",
            RecoveryAction.NONE,
            0.72,
        )

    expected = _normalized_stage(observation.expected_stage)
    actual = _normalized_stage(observation.detected_stage)
    if expected and actual and expected != actual:
        return InterruptionAssessment(
            InterruptionKind.ROUTE_DEVIATION,
            "页面阶段与预期路线不同",
            f"expected={observation.expected_stage!r}, detected={observation.detected_stage!r}",
            "先判断这是合法前进、页面 target 交接还是实际偏航，再由确定性检测器验收。",
            RecoveryAction.RECLASSIFY_STAGE,
            0.88,
        )

    return InterruptionAssessment(
        InterruptionKind.NONE,
        "页面状态正常",
        "",
        "",
        RecoveryAction.NONE,
        1.0,
    )


def classify_failure_text(message: str, *, phase: str = "", progress: int = 0) -> RuntimeEvent:
    """Best-effort UI classification for failures emitted by existing runners."""

    text = str(message or "").strip()
    lowered = text.casefold()

    if any(marker in lowered for marker in ("captcha", "human verification", "人机验证", "机器人验证")):
        return RuntimeEvent(
            RuntimeState.WAITING_FOR_USER,
            "需要人工验证",
            text,
            phase=phase,
            progress=progress,
            interruption=InterruptionKind.HUMAN_VERIFICATION,
            suggestion="请在 Makro Browser 完成人机验证；当前版本不会尝试绕过验证。",
            action=RecoveryAction.ASK_HUMAN_VERIFICATION,
            advisor="rules",
        )
    if any(marker in lowered for marker in ("登录", "login", "authentication", "sign in", "signin")):
        return RuntimeEvent(
            RuntimeState.WAITING_FOR_USER,
            "需要恢复 Makro 登录",
            text,
            phase=phase,
            progress=progress,
            interruption=InterruptionKind.LOGIN_REQUIRED,
            suggestion="在专用 Makro Browser 登录后重新运行；后续 Supervisor 会自动恢复。",
            action=RecoveryAction.ASK_HUMAN_LOGIN,
            advisor="rules",
        )
    if "joyride-overlay" in lowered or "intercepts pointer events" in lowered:
        return RuntimeEvent(
            RuntimeState.WARNING,
            "页面遮罩阻挡了浏览器操作",
            text,
            phase=phase,
            progress=progress,
            interruption=InterruptionKind.KNOWN_OVERLAY,
            suggestion="已知 Joyride 由 deterministic handler 优先处理；若仍存在则保留现场分析。",
            action=RecoveryAction.DISMISS_OVERLAY,
            advisor="rules",
        )
    if "step 3 did not appear" in lowered or "no unique step 3 page" in lowered:
        return RuntimeEvent(
            RuntimeState.WARNING,
            "Step 2 → Step 3 页面交接异常",
            text,
            phase=phase,
            progress=progress,
            interruption=InterruptionKind.PAGE_TRANSITION,
            suggestion="检查本次 transition 的 origin/new targets；只接管唯一、可验证的 Step 3 页面。",
            action=RecoveryAction.SWITCH_TAB,
            advisor="rules",
        )
    if any(marker in lowered for marker in ("cdp", "browser", "target page", "targetid")):
        return RuntimeEvent(
            RuntimeState.WARNING,
            "浏览器会话或页面 ownership 异常",
            text,
            phase=phase,
            progress=progress,
            interruption=InterruptionKind.BROWSER_OFFLINE,
            suggestion="保持当前 Job ownership；不要猜测其他标签页。Browser Session Manager 将负责会话恢复。",
            action=RecoveryAction.WAIT,
            advisor="rules",
        )
    return RuntimeEvent(
        RuntimeState.FAILED,
        "当前流程未完成",
        text,
        phase=phase,
        progress=progress,
        interruption=InterruptionKind.UNKNOWN,
        suggestion="Shadow Mode 已保留异常信息；未知异常只分析，不自动操作页面。",
        action=RecoveryAction.NONE,
        advisor="shadow",
    )


__all__ = [
    "InterruptionAssessment",
    "assess_observation",
    "classify_failure_text",
]
