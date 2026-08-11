"""Shared runtime/recovery contracts for Makro browser supervision.

This module is intentionally UI- and Playwright-free. It defines the small,
auditable vocabulary used by the runtime supervisor, Recovery AI and GUI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4


class RuntimeState(str, Enum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    READY = "READY"
    AI_ANALYZING = "AI_ANALYZING"
    RECOVERING = "RECOVERING"
    WAITING_FOR_USER = "WAITING_FOR_USER"
    RECOVERED = "RECOVERED"
    WARNING = "WARNING"
    FAILED = "FAILED"
    COMPLETE = "COMPLETE"


class InterruptionKind(str, Enum):
    NONE = "NONE"
    KNOWN_OVERLAY = "KNOWN_OVERLAY"
    UNKNOWN_MODAL = "UNKNOWN_MODAL"
    LOGIN_REQUIRED = "LOGIN_REQUIRED"
    HUMAN_VERIFICATION = "HUMAN_VERIFICATION"
    ROUTE_DEVIATION = "ROUTE_DEVIATION"
    PAGE_TRANSITION = "PAGE_TRANSITION"
    BROWSER_OFFLINE = "BROWSER_OFFLINE"
    UNKNOWN = "UNKNOWN"


class RecoveryAction(str, Enum):
    NONE = "NONE"
    WAIT = "WAIT"
    DISMISS_OVERLAY = "DISMISS_OVERLAY"
    CLOSE_POPUP = "CLOSE_POPUP"
    SWITCH_TAB = "SWITCH_TAB"
    RECLASSIFY_STAGE = "RECLASSIFY_STAGE"
    RECOVER_JOB_PAGE = "RECOVER_JOB_PAGE"
    RELOAD_PAGE = "RELOAD_PAGE"
    GO_BACK_SAFE = "GO_BACK_SAFE"
    ASK_HUMAN_LOGIN = "ASK_HUMAN_LOGIN"
    ASK_HUMAN_VERIFICATION = "ASK_HUMAN_VERIFICATION"
    ABORT = "ABORT"


class RecoveryPermission(str, Enum):
    AUTO_SAFE = "AUTO_SAFE"
    AUTO_VERIFY = "AUTO_VERIFY"
    USER_CONFIRM = "USER_CONFIRM"
    HUMAN_ONLY = "HUMAN_ONLY"
    FORBIDDEN = "FORBIDDEN"


_ACTION_PERMISSIONS = {
    RecoveryAction.NONE: RecoveryPermission.AUTO_SAFE,
    RecoveryAction.WAIT: RecoveryPermission.AUTO_SAFE,
    RecoveryAction.DISMISS_OVERLAY: RecoveryPermission.AUTO_SAFE,
    RecoveryAction.CLOSE_POPUP: RecoveryPermission.AUTO_VERIFY,
    RecoveryAction.SWITCH_TAB: RecoveryPermission.AUTO_VERIFY,
    RecoveryAction.RECLASSIFY_STAGE: RecoveryPermission.AUTO_VERIFY,
    RecoveryAction.RECOVER_JOB_PAGE: RecoveryPermission.AUTO_VERIFY,
    RecoveryAction.RELOAD_PAGE: RecoveryPermission.USER_CONFIRM,
    RecoveryAction.GO_BACK_SAFE: RecoveryPermission.USER_CONFIRM,
    RecoveryAction.ASK_HUMAN_LOGIN: RecoveryPermission.HUMAN_ONLY,
    RecoveryAction.ASK_HUMAN_VERIFICATION: RecoveryPermission.HUMAN_ONLY,
    RecoveryAction.ABORT: RecoveryPermission.AUTO_SAFE,
}

FORBIDDEN_RECOVERY_ACTION_NAMES = frozenset(
    {
        "SEND_TO_QC",
        "DELETE_LISTING",
        "SUBMIT_UNKNOWN_FORM",
        "CHANGE_VERTICAL",
        "CHANGE_BRAND",
        "CREATE_NEW_LISTING",
        "CREATE_RANDOM_NEW_LISTING",
        "CHANGE_PRODUCT_DATA",
    }
)


def permission_for(action: RecoveryAction | str) -> RecoveryPermission:
    resolved = action if isinstance(action, RecoveryAction) else RecoveryAction(str(action))
    return _ACTION_PERMISSIONS[resolved]


@dataclass(slots=True, frozen=True)
class RuntimeEvent:
    state: RuntimeState
    title: str
    detail: str = ""
    phase: str = ""
    progress: int = 0
    interruption: InterruptionKind = InterruptionKind.NONE
    suggestion: str = ""
    action: RecoveryAction = RecoveryAction.NONE
    permission: RecoveryPermission = RecoveryPermission.AUTO_SAFE
    confidence: float = 0.0
    requires_user: bool = False
    advisor: str = "system"
    job_id: str = ""
    target_id: str = ""
    request_id: str = ""
    event_id: str = field(default_factory=lambda: uuid4().hex)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "progress", max(0, min(100, int(self.progress))))
        object.__setattr__(self, "confidence", max(0.0, min(1.0, float(self.confidence))))
        object.__setattr__(self, "permission", permission_for(self.action))
        if self.permission in {
            RecoveryPermission.USER_CONFIRM,
            RecoveryPermission.HUMAN_ONLY,
        }:
            object.__setattr__(self, "requires_user", True)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RuntimeEvent":
        if not isinstance(payload, dict):
            raise ValueError("runtime event payload must be a JSON object")
        return cls(
            state=RuntimeState(str(payload.get("state") or "IDLE").upper()),
            title=str(payload.get("title") or ""),
            detail=str(payload.get("detail") or ""),
            phase=str(payload.get("phase") or ""),
            progress=int(payload.get("progress") or 0),
            interruption=InterruptionKind(
                str(payload.get("interruption") or "NONE").upper()
            ),
            suggestion=str(payload.get("suggestion") or ""),
            action=RecoveryAction(str(payload.get("action") or "NONE").upper()),
            confidence=float(payload.get("confidence") or 0.0),
            requires_user=bool(payload.get("requires_user", False)),
            advisor=str(payload.get("advisor") or "system"),
            job_id=str(payload.get("job_id") or ""),
            target_id=str(payload.get("target_id") or ""),
            request_id=str(payload.get("request_id") or ""),
            event_id=str(payload.get("event_id") or uuid4().hex),
            created_at=str(
                payload.get("created_at")
                or datetime.now(timezone.utc).isoformat(timespec="seconds")
            ),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "created_at": self.created_at,
            "state": self.state.value,
            "title": self.title,
            "detail": self.detail,
            "phase": self.phase,
            "progress": self.progress,
            "interruption": self.interruption.value,
            "suggestion": self.suggestion,
            "action": self.action.value,
            "permission": self.permission.value,
            "confidence": self.confidence,
            "requires_user": self.requires_user,
            "advisor": self.advisor,
            "job_id": self.job_id,
            "target_id": self.target_id,
            "request_id": self.request_id,
        }


@dataclass(slots=True, frozen=True)
class RecoveryProposal:
    observed_state: str
    interruption: InterruptionKind
    action: RecoveryAction
    target_element_id: str = ""
    explanation: str = ""
    expected_after: str = ""
    confidence: float = 0.0
    requires_user: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "confidence", max(0.0, min(1.0, float(self.confidence))))
        if permission_for(self.action) in {
            RecoveryPermission.USER_CONFIRM,
            RecoveryPermission.HUMAN_ONLY,
        }:
            object.__setattr__(self, "requires_user", True)

    @property
    def permission(self) -> RecoveryPermission:
        return permission_for(self.action)

    def as_dict(self) -> dict[str, Any]:
        return {
            "observed_state": self.observed_state,
            "interruption": self.interruption.value,
            "action": self.action.value,
            "permission": self.permission.value,
            "target_element_id": self.target_element_id,
            "explanation": self.explanation,
            "expected_after": self.expected_after,
            "confidence": self.confidence,
            "requires_user": self.requires_user,
        }


__all__ = [
    "FORBIDDEN_RECOVERY_ACTION_NAMES",
    "InterruptionKind",
    "RecoveryAction",
    "RecoveryPermission",
    "RecoveryProposal",
    "RuntimeEvent",
    "RuntimeState",
    "permission_for",
]
