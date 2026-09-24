from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from app.agent_runtime import (
    AgentRuntime,
    AgentStatus,
    AgentTool,
    FileAgentSessionStore,
    ToolContext,
    ToolEffect,
    ToolRegistry,
    ToolResult,
)
from app.ai import ChatRequest, ModelResponse, ToolCall


class _ScriptedPlatform:
    def __init__(self, responses: list[ModelResponse]) -> None:
        self.responses = list(responses)
        self.requests: list[ChatRequest] = []

    def execute_chat(self, _profile_id: str, request: ChatRequest) -> ModelResponse:
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("unexpected model request")
        return self.responses.pop(0)


def test_approved_tool_receives_live_cancellation_token(tmp_path: Path) -> None:
    entered = threading.Event()
    observed_cancel = threading.Event()

    def mutating_tool(context: ToolContext, _arguments: dict[str, Any]) -> ToolResult:
        entered.set()
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if context.cancelled:
                observed_cancel.set()
                return ToolResult(ok=False, content="cancelled cooperatively")
            time.sleep(0.01)
        return ToolResult(ok=True, content="completed without cancellation")

    tools = ToolRegistry(
        (
            AgentTool(
                name="mutating_tool",
                description="Blocking mutating tool used to verify cancellation propagation.",
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                handler=mutating_tool,
                effect=ToolEffect.MUTATING,
            ),
        )
    )
    platform = _ScriptedPlatform(
        [
            ModelResponse(
                tool_calls=(ToolCall(call_id="mutate-1", name="mutating_tool", arguments={}),),
                finish_reason="tool_calls",
            )
        ]
    )
    runtime = AgentRuntime(
        platform=platform,
        store=FileAgentSessionStore(tmp_path),
        tools=tools,
    )
    session = runtime.create_session("agent.fast")
    paused = runtime.start_turn(session.session_id, "Run the mutating tool.")
    assert paused.status is AgentStatus.WAITING_APPROVAL

    holder: list[Any] = []

    def resume() -> None:
        holder.append(runtime.resume_approval(session.session_id, "mutate-1", approved=True))

    worker = threading.Thread(target=resume, daemon=True)
    worker.start()
    assert entered.wait(1.5), "approved tool did not start"

    runtime.cancel(session.session_id)
    worker.join(timeout=3.0)

    assert not worker.is_alive(), "approved tool did not cooperate with cancellation"
    assert observed_cancel.is_set()
    assert holder and holder[0].status is AgentStatus.CANCELLED
    assert runtime.get_session(session.session_id).status is AgentStatus.CANCELLED


def test_empty_model_response_fails_closed(tmp_path: Path) -> None:
    platform = _ScriptedPlatform([ModelResponse(text="", tool_calls=())])
    runtime = AgentRuntime(
        platform=platform,
        store=FileAgentSessionStore(tmp_path),
        tools=ToolRegistry(),
    )
    session = runtime.create_session("agent.fast")

    result = runtime.start_turn(session.session_id, "Respond.")

    assert result.status is AgentStatus.FAILED
    assert "neither text nor tool calls" in result.error
