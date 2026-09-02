from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.ai import AIMessage, ImagePart, MessageRole, ModelUsage, TextPart, ToolCall

from .contracts import AgentEvent, AgentEventKind, AgentSession, AgentStatus, PendingToolApproval, ToolEffect


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _tool_call_to_dict(call: ToolCall) -> dict[str, Any]:
    return {"call_id": call.call_id, "name": call.name, "arguments": call.arguments}


def _tool_call_from_dict(payload: dict[str, Any]) -> ToolCall:
    return ToolCall(
        call_id=str(payload.get("call_id") or ""),
        name=str(payload.get("name") or ""),
        arguments=dict(payload.get("arguments") or {}),
    )


def _message_to_dict(message: AIMessage) -> dict[str, Any]:
    if isinstance(message.content, str):
        content: Any = message.content
    else:
        content = []
        for part in message.content:
            if isinstance(part, TextPart):
                content.append({"type": "text", "text": part.text})
            elif isinstance(part, ImagePart):
                content.append(
                    {"type": "image", "image_url": part.image_url, "detail": part.detail}
                )
            else:  # pragma: no cover - AI contracts reject unsupported parts
                raise TypeError("unsupported AI message part")
    return {
        "role": message.role.value,
        "content": content,
        "name": message.name,
        "tool_call_id": message.tool_call_id,
        "tool_calls": [_tool_call_to_dict(call) for call in message.tool_calls],
    }


def _message_from_dict(payload: dict[str, Any]) -> AIMessage:
    raw_content = payload.get("content", "")
    if isinstance(raw_content, list):
        parts = []
        for item in raw_content:
            if not isinstance(item, dict):
                raise ValueError("invalid persisted AI multipart content")
            kind = str(item.get("type") or "")
            if kind == "text":
                parts.append(TextPart(str(item.get("text") or "")))
            elif kind == "image":
                parts.append(
                    ImagePart(
                        str(item.get("image_url") or ""),
                        detail=str(item.get("detail") or "auto"),
                    )
                )
            else:
                raise ValueError(f"unsupported persisted AI content type: {kind!r}")
        content: Any = tuple(parts)
    else:
        content = str(raw_content or "")
    return AIMessage(
        role=MessageRole(str(payload.get("role") or "")),
        content=content,
        name=str(payload.get("name") or ""),
        tool_call_id=str(payload.get("tool_call_id") or ""),
        tool_calls=tuple(
            _tool_call_from_dict(dict(item))
            for item in payload.get("tool_calls", [])
            if isinstance(item, dict)
        ),
    )


def _approval_to_dict(value: PendingToolApproval | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "call_id": value.call_id,
        "tool_name": value.tool_name,
        "arguments": value.arguments,
        "effect": value.effect.value,
        "reason": value.reason,
    }


def _approval_from_dict(value: Any) -> PendingToolApproval | None:
    if not isinstance(value, dict):
        return None
    return PendingToolApproval(
        call_id=str(value.get("call_id") or ""),
        tool_name=str(value.get("tool_name") or ""),
        arguments=dict(value.get("arguments") or {}),
        effect=ToolEffect(str(value.get("effect") or ToolEffect.READ_ONLY.value)),
        reason=str(value.get("reason") or ""),
    )


def _usage_to_dict(usage: ModelUsage) -> dict[str, int]:
    return {
        "input_tokens": int(usage.input_tokens),
        "output_tokens": int(usage.output_tokens),
        "total_tokens": int(usage.total_tokens),
    }


def _usage_from_dict(payload: Any) -> ModelUsage:
    data = payload if isinstance(payload, dict) else {}
    return ModelUsage(
        input_tokens=int(data.get("input_tokens") or 0),
        output_tokens=int(data.get("output_tokens") or 0),
        total_tokens=int(data.get("total_tokens") or 0),
    )


def session_to_dict(session: AgentSession) -> dict[str, Any]:
    return {
        "version": 1,
        "session_id": session.session_id,
        "profile_id": session.profile_id,
        "system_prompt": session.system_prompt,
        "workspace_dir": session.workspace_dir,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
        "status": session.status.value,
        "current_turn_id": session.current_turn_id,
        "messages": [_message_to_dict(message) for message in session.messages],
        "pending_tool_calls": [_tool_call_to_dict(call) for call in session.pending_tool_calls],
        "pending_approval": _approval_to_dict(session.pending_approval),
        "model_steps": session.model_steps,
        "tool_calls": session.tool_calls,
        "usage": _usage_to_dict(session.usage),
        "final_text": session.final_text,
        "error": session.error,
    }


def session_from_dict(payload: dict[str, Any]) -> AgentSession:
    return AgentSession(
        session_id=str(payload.get("session_id") or ""),
        profile_id=str(payload.get("profile_id") or ""),
        system_prompt=str(payload.get("system_prompt") or ""),
        workspace_dir=str(payload.get("workspace_dir") or ""),
        created_at=str(payload.get("created_at") or ""),
        updated_at=str(payload.get("updated_at") or ""),
        status=AgentStatus(str(payload.get("status") or AgentStatus.IDLE.value)),
        current_turn_id=str(payload.get("current_turn_id") or ""),
        messages=[
            _message_from_dict(dict(item))
            for item in payload.get("messages", [])
            if isinstance(item, dict)
        ],
        pending_tool_calls=[
            _tool_call_from_dict(dict(item))
            for item in payload.get("pending_tool_calls", [])
            if isinstance(item, dict)
        ],
        pending_approval=_approval_from_dict(payload.get("pending_approval")),
        model_steps=int(payload.get("model_steps") or 0),
        tool_calls=int(payload.get("tool_calls") or 0),
        usage=_usage_from_dict(payload.get("usage")),
        final_text=str(payload.get("final_text") or ""),
        error=str(payload.get("error") or ""),
    )


class FileAgentSessionStore:
    """Local durable state for the detached Agent Harness.

    ``session.json`` is an atomic resumable snapshot. ``events.jsonl`` is an
    append-only UI/audit feed. The harness stores observable messages, tool
    activity and lifecycle state only; it has no field for private model
    chain-of-thought.
    """

    def __init__(self, runtime_root: str | Path) -> None:
        self.root = Path(runtime_root).expanduser().resolve() / "agent_runtime" / "sessions"
        self.root.mkdir(parents=True, exist_ok=True)

    def session_dir(self, session_id: str) -> Path:
        value = str(session_id or "").strip()
        if not value or any(char not in "0123456789abcdef-" for char in value.casefold()):
            raise ValueError("invalid agent session id")
        return self.root / value

    def workspace_dir(self, session_id: str) -> Path:
        path = self.session_dir(session_id) / "workspace"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def create(self, session: AgentSession) -> None:
        directory = self.session_dir(session.session_id)
        if directory.exists():
            raise FileExistsError(f"agent session already exists: {session.session_id}")
        directory.mkdir(parents=True, exist_ok=False)
        self.workspace_dir(session.session_id)
        self.save(session)

    def save(self, session: AgentSession) -> None:
        session.updated_at = utc_now()
        directory = self.session_dir(session.session_id)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / "session.json"
        temp = directory / f".session.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        data = json.dumps(session_to_dict(session), ensure_ascii=False, indent=2, sort_keys=True)
        try:
            with temp.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, target)
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass

    def load(self, session_id: str) -> AgentSession:
        target = self.session_dir(session_id) / "session.json"
        payload = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("agent session snapshot must be a JSON object")
        return session_from_dict(payload)

    def append_event(self, event: AgentEvent) -> None:
        directory = self.session_dir(event.session_id)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "events.jsonl"
        payload = {
            "event_id": event.event_id,
            "session_id": event.session_id,
            "turn_id": event.turn_id,
            "kind": event.kind.value,
            "created_at": event.created_at,
            "data": event.data,
        }
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        with path.open("a", encoding="utf-8", newline="") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())

    def events(self, session_id: str) -> tuple[AgentEvent, ...]:
        path = self.session_dir(session_id) / "events.jsonl"
        if not path.is_file():
            return ()
        output: list[AgentEvent] = []
        for raw in path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            payload = json.loads(raw)
            output.append(
                AgentEvent(
                    event_id=str(payload.get("event_id") or ""),
                    session_id=str(payload.get("session_id") or ""),
                    turn_id=str(payload.get("turn_id") or ""),
                    kind=AgentEventKind(str(payload.get("kind") or "")),
                    created_at=str(payload.get("created_at") or ""),
                    data=dict(payload.get("data") or {}),
                )
            )
        return tuple(output)


__all__ = [
    "FileAgentSessionStore",
    "session_from_dict",
    "session_to_dict",
    "utc_now",
]
