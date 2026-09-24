from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path
from typing import Callable, Protocol

from app.ai import AIMessage, ChatRequest, MessageRole, ModelResponse, ModelUsage, ToolCall, ToolChoice

from .contracts import (
    AgentEvent,
    AgentEventKind,
    AgentLimits,
    AgentRunResult,
    AgentSession,
    AgentStatus,
    PendingToolApproval,
)
from .storage import FileAgentSessionStore, utc_now
from .tools import ToolContext, ToolPolicy, ToolRegistry, ToolResult, validate_tool_arguments


DEFAULT_AGENT_SYSTEM_PROMPT = (
    "You are an execution agent operating inside a controlled tool harness. "
    "Use only the tools provided to you, never invent tool results, and treat tool errors as observations "
    "you may correct on the next step. Keep private reasoning private; communicate only useful conclusions, "
    "requests for user decisions, and concise action/status summaries."
)


class AgentModelPlatform(Protocol):
    def execute_chat(self, profile_id: str, request: ChatRequest) -> ModelResponse:
        ...


class CancellationToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()


EventListener = Callable[[AgentEvent], None]


class AgentRuntime:
    """Detached Codex-style turn/tool harness over the provider-neutral AIPlatform.

    One turn repeatedly samples the model, executes advertised tools, returns
    observations, and stops on a final assistant response. Durable state contains
    observable messages/events only; private model chain-of-thought is neither
    requested nor persisted.
    """

    def __init__(
        self,
        *,
        platform: AgentModelPlatform,
        store: FileAgentSessionStore,
        tools: ToolRegistry | None = None,
        policy: ToolPolicy | None = None,
        limits: AgentLimits | None = None,
    ) -> None:
        self.platform = platform
        self.store = store
        self.tools = tools or ToolRegistry()
        self.policy = policy or ToolPolicy()
        self.limits = limits or AgentLimits()
        self._listeners: list[EventListener] = []
        self._session_locks: dict[str, threading.RLock] = {}
        self._session_locks_guard = threading.Lock()
        self._active_tokens: dict[str, CancellationToken] = {}
        self._active_tokens_guard = threading.Lock()

    def subscribe(self, listener: EventListener) -> None:
        if not callable(listener):
            raise TypeError("agent event listener must be callable")
        self._listeners.append(listener)

    def create_session(
        self,
        profile_id: str,
        *,
        system_prompt: str = DEFAULT_AGENT_SYSTEM_PROMPT,
    ) -> AgentSession:
        profile = str(profile_id or "").strip().casefold()
        prompt = str(system_prompt or "").strip()
        if not profile or not prompt:
            raise ValueError("agent session requires profile_id and system_prompt")
        session_id = str(uuid.uuid4())
        now = utc_now()
        workspace = self.store.session_dir(session_id) / "workspace"
        session = AgentSession(
            session_id=session_id,
            profile_id=profile,
            system_prompt=prompt,
            workspace_dir=str(workspace),
            created_at=now,
            updated_at=now,
        )
        self.store.create(session)
        self._record(session, AgentEventKind.SESSION_CREATED, data={"profile_id": profile})
        return session

    def get_session(self, session_id: str) -> AgentSession:
        return self.store.load(session_id)

    def start_turn(self, session_id: str, user_text: str) -> AgentRunResult:
        text = str(user_text or "").strip()
        if not text:
            raise ValueError("agent turn input must not be empty")
        lock = self._session_lock(session_id)
        with lock:
            session = self.store.load(session_id)
            if session.status is AgentStatus.WAITING_APPROVAL:
                raise RuntimeError("agent session is waiting for tool approval")
            turn_id = str(uuid.uuid4())
            session.current_turn_id = turn_id
            session.status = AgentStatus.RUNNING
            session.model_steps = 0
            session.tool_calls = 0
            session.pending_tool_calls.clear()
            session.pending_approval = None
            session.final_text = ""
            session.error = ""
            session.messages.append(AIMessage(role=MessageRole.USER, content=text))
            self._record(session, AgentEventKind.TURN_STARTED, data={})
            self._record(session, AgentEventKind.USER_MESSAGE, data={"text": text})
            token = self._activate(session.session_id)
            try:
                return self._drive(session, token)
            finally:
                self._deactivate(session.session_id, token)

    def resume_approval(
        self,
        session_id: str,
        call_id: str,
        *,
        approved: bool,
    ) -> AgentRunResult:
        lock = self._session_lock(session_id)
        with lock:
            session = self.store.load(session_id)
            pending = session.pending_approval
            if session.status is not AgentStatus.WAITING_APPROVAL or pending is None:
                raise RuntimeError("agent session is not waiting for approval")
            if pending.call_id != str(call_id or "").strip():
                raise ValueError("approval call_id does not match pending tool call")
            if not session.pending_tool_calls or session.pending_tool_calls[0].call_id != pending.call_id:
                raise RuntimeError("pending tool approval state is inconsistent")

            session.status = AgentStatus.RUNNING
            session.pending_approval = None
            call = session.pending_tool_calls.pop(0)
            token = self._activate(session.session_id)
            try:
                if approved:
                    self._record(
                        session,
                        AgentEventKind.TOOL_APPROVED,
                        data={"call_id": call.call_id, "tool": call.name},
                    )
                    if not self._consume_tool_call(
                        session,
                        call,
                        token=token,
                        approval_granted=True,
                    ):
                        return self._result(session)
                else:
                    self._record(
                        session,
                        AgentEventKind.TOOL_DENIED,
                        data={"call_id": call.call_id, "tool": call.name},
                    )
                    self._append_tool_result(
                        session,
                        call,
                        ToolResult(ok=False, content="Tool call denied by the user."),
                        failed=True,
                    )

                if not self._process_pending_tools(session, token):
                    return self._result(session)
                return self._drive(session, token)
            finally:
                self._deactivate(session.session_id, token)

    def cancel(self, session_id: str) -> AgentRunResult:
        with self._active_tokens_guard:
            token = self._active_tokens.get(session_id)
        if token is not None:
            token.cancel()
            session = self.store.load(session_id)
            return self._result(session)

        lock = self._session_lock(session_id)
        with lock:
            session = self.store.load(session_id)
            if session.status in {AgentStatus.RUNNING, AgentStatus.WAITING_APPROVAL}:
                session.status = AgentStatus.CANCELLED
                session.pending_approval = None
                session.pending_tool_calls.clear()
                session.error = "cancelled by user"
                self._record(session, AgentEventKind.TURN_CANCELLED, data={})
            return self._result(session)

    def recover_interrupted(self, session_id: str) -> AgentRunResult:
        lock = self._session_lock(session_id)
        with lock:
            session = self.store.load(session_id)
            if session.status is AgentStatus.RUNNING:
                session.status = AgentStatus.INTERRUPTED
                session.error = "Agent process stopped before the active turn reached a durable terminal state."
                self._record(session, AgentEventKind.TURN_INTERRUPTED, data={"error": session.error})
            return self._result(session)

    def _drive(self, session: AgentSession, token: CancellationToken) -> AgentRunResult:
        try:
            while True:
                if self._cancel_if_requested(session, token):
                    return self._result(session)
                if session.pending_tool_calls:
                    if not self._process_pending_tools(session, token):
                        return self._result(session)
                if session.model_steps >= self.limits.max_model_steps:
                    return self._limit(session, "model step limit reached")

                messages = [AIMessage(role=MessageRole.SYSTEM, content=session.system_prompt), *session.messages]
                if len(messages) > self.limits.max_messages:
                    return self._limit(
                        session,
                        "context message limit reached; semantic compaction is not configured",
                    )

                self._record(
                    session,
                    AgentEventKind.MODEL_REQUESTED,
                    data={
                        "profile_id": session.profile_id,
                        "step": session.model_steps + 1,
                        "message_count": len(messages),
                        "tool_count": len(self.tools.all()),
                    },
                )
                response = self.platform.execute_chat(
                    session.profile_id,
                    ChatRequest(
                        messages=tuple(messages),
                        tools=self.tools.definitions(),
                        tool_choice=ToolChoice.AUTO,
                    ),
                )
                if not isinstance(response, ModelResponse):
                    raise TypeError("agent model platform must return ModelResponse")
                if not response.text and not response.tool_calls:
                    raise RuntimeError("agent model response contained neither text nor tool calls")
                if self._cancel_if_requested(session, token):
                    return self._result(session)

                session.model_steps += 1
                session.usage = _add_usage(session.usage, response.usage)
                assistant = AIMessage(
                    role=MessageRole.ASSISTANT,
                    content=response.text,
                    tool_calls=response.tool_calls,
                )
                session.messages.append(assistant)
                self._record(
                    session,
                    AgentEventKind.MODEL_RESPONSE,
                    data={
                        "text": response.text,
                        "finish_reason": response.finish_reason,
                        "response_id": response.response_id,
                        "tool_calls": [
                            {"call_id": call.call_id, "name": call.name, "arguments": call.arguments}
                            for call in response.tool_calls
                        ],
                        "usage": {
                            "input_tokens": response.usage.input_tokens,
                            "output_tokens": response.usage.output_tokens,
                            "total_tokens": response.usage.total_tokens,
                        },
                    },
                )

                if response.tool_calls:
                    session.tool_calls += len(response.tool_calls)
                    if session.tool_calls > self.limits.max_tool_calls:
                        return self._limit(session, "tool call limit reached")
                    session.pending_tool_calls.extend(response.tool_calls)
                    for call in response.tool_calls:
                        self._record(
                            session,
                            AgentEventKind.TOOL_REQUESTED,
                            data={"call_id": call.call_id, "tool": call.name, "arguments": call.arguments},
                        )
                    continue

                session.status = AgentStatus.COMPLETED
                session.final_text = response.text
                session.error = ""
                self._record(
                    session,
                    AgentEventKind.TURN_COMPLETED,
                    data={"text": response.text},
                )
                return self._result(session)
        except Exception as exc:
            session.status = AgentStatus.FAILED
            session.error = f"{type(exc).__name__}: {exc}"
            self._record(session, AgentEventKind.TURN_FAILED, data={"error": session.error})
            return self._result(session)

    def _process_pending_tools(self, session: AgentSession, token: CancellationToken) -> bool:
        while session.pending_tool_calls:
            if self._cancel_if_requested(session, token):
                return False
            call = session.pending_tool_calls[0]
            tool = self.tools.get(call.name)
            if tool is None:
                session.pending_tool_calls.pop(0)
                self._append_tool_result(
                    session,
                    call,
                    ToolResult(ok=False, content=f"Unknown tool: {call.name}"),
                    failed=True,
                )
                continue
            try:
                validate_tool_arguments(tool.input_schema, call.arguments)
            except ValueError as exc:
                session.pending_tool_calls.pop(0)
                self._append_tool_result(
                    session,
                    call,
                    ToolResult(ok=False, content=f"Invalid tool arguments: {exc}"),
                    failed=True,
                )
                continue
            if self.policy.requires_approval(tool):
                session.pending_approval = PendingToolApproval(
                    call_id=call.call_id,
                    tool_name=call.name,
                    arguments=call.arguments,
                    effect=tool.effect,
                    reason=f"Tool effect {tool.effect.value} requires explicit user approval.",
                )
                session.status = AgentStatus.WAITING_APPROVAL
                self._record(
                    session,
                    AgentEventKind.TOOL_APPROVAL_REQUIRED,
                    data={
                        "call_id": call.call_id,
                        "tool": call.name,
                        "arguments": call.arguments,
                        "effect": tool.effect.value,
                    },
                )
                return False

            session.pending_tool_calls.pop(0)
            if not self._consume_tool_call(
                session,
                call,
                token=token,
                approval_granted=False,
            ):
                return False
        return True

    def _consume_tool_call(
        self,
        session: AgentSession,
        call: ToolCall,
        *,
        token: CancellationToken,
        approval_granted: bool,
    ) -> bool:
        if self._cancel_if_requested(session, token):
            return False
        tool = self.tools.get(call.name)
        if tool is None:
            self._append_tool_result(
                session,
                call,
                ToolResult(ok=False, content=f"Unknown tool: {call.name}"),
                failed=True,
            )
            return True
        try:
            validate_tool_arguments(tool.input_schema, call.arguments)
        except ValueError as exc:
            self._append_tool_result(
                session,
                call,
                ToolResult(ok=False, content=f"Invalid tool arguments: {exc}"),
                failed=True,
            )
            return True
        if self.policy.requires_approval(tool) and not approval_granted:
            raise RuntimeError("mutating/sensitive tool reached executor without approval")

        self._record(
            session,
            AgentEventKind.TOOL_STARTED,
            data={"call_id": call.call_id, "tool": call.name},
        )
        context = ToolContext(
            session_id=session.session_id,
            turn_id=session.current_turn_id,
            workspace=Path(session.workspace_dir),
            is_cancelled=lambda: token.cancelled,
        )
        try:
            result = tool.handler(context, call.arguments)
            if not isinstance(result, ToolResult):
                raise TypeError("agent tool handler must return ToolResult")
        except Exception as exc:
            result = ToolResult(ok=False, content=f"{type(exc).__name__}: {exc}")
        self._append_tool_result(session, call, result, failed=not result.ok)
        if self._cancel_if_requested(session, token):
            return False
        return True

    def _append_tool_result(
        self,
        session: AgentSession,
        call: ToolCall,
        result: ToolResult,
        *,
        failed: bool,
    ) -> None:
        model_payload = result.model_payload(max_chars=self.limits.max_tool_result_chars)
        session.messages.append(
            AIMessage(
                role=MessageRole.TOOL,
                content=model_payload,
                name=call.name,
                tool_call_id=call.call_id,
            )
        )
        self._record(
            session,
            AgentEventKind.TOOL_FAILED if failed else AgentEventKind.TOOL_COMPLETED,
            data={
                "call_id": call.call_id,
                "tool": call.name,
                "ok": result.ok,
                "content": result.content,
                "data": result.data,
            },
        )

    def _limit(self, session: AgentSession, reason: str) -> AgentRunResult:
        session.status = AgentStatus.LIMIT_REACHED
        session.error = reason
        session.pending_approval = None
        session.pending_tool_calls.clear()
        self._record(session, AgentEventKind.LIMIT_REACHED, data={"reason": reason})
        return self._result(session)

    def _cancel_if_requested(self, session: AgentSession, token: CancellationToken) -> bool:
        if not token.cancelled:
            return False
        if session.status is AgentStatus.CANCELLED:
            return True
        session.status = AgentStatus.CANCELLED
        session.pending_approval = None
        session.pending_tool_calls.clear()
        session.error = "cancelled by user"
        self._record(session, AgentEventKind.TURN_CANCELLED, data={})
        return True

    def _record(
        self,
        session: AgentSession,
        kind: AgentEventKind,
        *,
        data: dict[str, object],
    ) -> AgentEvent:
        json.dumps(data, ensure_ascii=False)
        event = AgentEvent(
            event_id=str(uuid.uuid4()),
            session_id=session.session_id,
            turn_id=session.current_turn_id,
            kind=kind,
            created_at=utc_now(),
            data=dict(data),
        )
        self.store.append_event(event)
        self.store.save(session)
        for listener in tuple(self._listeners):
            try:
                listener(event)
            except Exception:
                continue
        return event

    def _result(self, session: AgentSession) -> AgentRunResult:
        return AgentRunResult(
            session_id=session.session_id,
            turn_id=session.current_turn_id,
            status=session.status,
            final_text=session.final_text,
            pending_approval=session.pending_approval,
            usage=session.usage,
            error=session.error,
        )

    def _session_lock(self, session_id: str) -> threading.RLock:
        key = str(session_id or "").strip()
        with self._session_locks_guard:
            return self._session_locks.setdefault(key, threading.RLock())

    def _activate(self, session_id: str) -> CancellationToken:
        token = CancellationToken()
        with self._active_tokens_guard:
            if session_id in self._active_tokens:
                raise RuntimeError("agent session already has an active turn")
            self._active_tokens[session_id] = token
        return token

    def _deactivate(self, session_id: str, token: CancellationToken) -> None:
        with self._active_tokens_guard:
            if self._active_tokens.get(session_id) is token:
                self._active_tokens.pop(session_id, None)


def _add_usage(left: ModelUsage, right: ModelUsage) -> ModelUsage:
    return ModelUsage(
        input_tokens=left.input_tokens + right.input_tokens,
        output_tokens=left.output_tokens + right.output_tokens,
        total_tokens=left.total_tokens + right.total_tokens,
    )


__all__ = [
    "AgentModelPlatform",
    "AgentRuntime",
    "CancellationToken",
    "DEFAULT_AGENT_SYSTEM_PROMPT",
]
