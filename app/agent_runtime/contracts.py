from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.ai import AIMessage, ModelUsage, ToolCall


class AgentStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
    LIMIT_REACHED = "limit_reached"


class AgentEventKind(str, Enum):
    SESSION_CREATED = "session_created"
    TURN_STARTED = "turn_started"
    USER_MESSAGE = "user_message"
    MODEL_REQUESTED = "model_requested"
    MODEL_RESPONSE = "model_response"
    TOOL_REQUESTED = "tool_requested"
    TOOL_APPROVAL_REQUIRED = "tool_approval_required"
    TOOL_APPROVED = "tool_approved"
    TOOL_DENIED = "tool_denied"
    TOOL_STARTED = "tool_started"
    TOOL_COMPLETED = "tool_completed"
    TOOL_FAILED = "tool_failed"
    TURN_COMPLETED = "turn_completed"
    TURN_FAILED = "turn_failed"
    TURN_CANCELLED = "turn_cancelled"
    TURN_INTERRUPTED = "turn_interrupted"
    LIMIT_REACHED = "limit_reached"


class ToolEffect(str, Enum):
    READ_ONLY = "read_only"
    MUTATING = "mutating"
    SENSITIVE = "sensitive"


@dataclass(frozen=True, slots=True)
class AgentLimits:
    max_model_steps: int = 16
    max_tool_calls: int = 32
    max_messages: int = 160
    max_tool_result_chars: int = 20_000

    def __post_init__(self) -> None:
        for name in (
            "max_model_steps",
            "max_tool_calls",
            "max_messages",
            "max_tool_result_chars",
        ):
            value = int(getattr(self, name))
            if value < 1:
                raise ValueError(f"{name} must be positive")
            object.__setattr__(self, name, value)


@dataclass(frozen=True, slots=True)
class PendingToolApproval:
    call_id: str
    tool_name: str
    arguments: dict[str, Any]
    effect: ToolEffect
    reason: str

    def __post_init__(self) -> None:
        call_id = str(self.call_id or "").strip()
        tool_name = str(self.tool_name or "").strip()
        reason = str(self.reason or "").strip()
        if not call_id or not tool_name:
            raise ValueError("pending approval requires call_id and tool_name")
        if not isinstance(self.arguments, dict):
            raise TypeError("pending approval arguments must be a JSON object")
        object.__setattr__(self, "call_id", call_id)
        object.__setattr__(self, "tool_name", tool_name)
        object.__setattr__(self, "effect", ToolEffect(self.effect))
        object.__setattr__(self, "reason", reason)


@dataclass(slots=True)
class AgentSession:
    session_id: str
    profile_id: str
    system_prompt: str
    workspace_dir: str
    created_at: str
    updated_at: str
    status: AgentStatus = AgentStatus.IDLE
    current_turn_id: str = ""
    messages: list[AIMessage] = field(default_factory=list)
    pending_tool_calls: list[ToolCall] = field(default_factory=list)
    pending_approval: PendingToolApproval | None = None
    model_steps: int = 0
    tool_calls: int = 0
    usage: ModelUsage = field(default_factory=ModelUsage)
    final_text: str = ""
    error: str = ""

    def __post_init__(self) -> None:
        self.session_id = str(self.session_id or "").strip()
        self.profile_id = str(self.profile_id or "").strip().casefold()
        self.system_prompt = str(self.system_prompt or "").strip()
        self.workspace_dir = str(self.workspace_dir or "").strip()
        self.status = AgentStatus(self.status)
        self.messages = list(self.messages)
        self.pending_tool_calls = list(self.pending_tool_calls)
        if not self.session_id or not self.profile_id or not self.workspace_dir:
            raise ValueError("agent session requires session_id, profile_id and workspace_dir")
        if not self.system_prompt:
            raise ValueError("agent session system_prompt must not be empty")


@dataclass(frozen=True, slots=True)
class AgentEvent:
    event_id: str
    session_id: str
    turn_id: str
    kind: AgentEventKind
    created_at: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    session_id: str
    turn_id: str
    status: AgentStatus
    final_text: str = ""
    pending_approval: PendingToolApproval | None = None
    usage: ModelUsage = field(default_factory=ModelUsage)
    error: str = ""


__all__ = [
    "AgentEvent",
    "AgentEventKind",
    "AgentLimits",
    "AgentRunResult",
    "AgentSession",
    "AgentStatus",
    "PendingToolApproval",
    "ToolEffect",
]
