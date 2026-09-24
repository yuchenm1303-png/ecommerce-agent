from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.agent_runtime import (
    AgentLimits,
    AgentRuntime,
    AgentStatus,
    AgentTool,
    FileAgentSessionStore,
    ToolContext,
    ToolEffect,
    ToolRegistry,
    ToolResult,
    builtin_read_only_tools,
)
from app.ai import AIMessage, ChatRequest, MessageRole, ModelResponse, ModelUsage, ToolCall
from app.ai.openai_runtime import _message_payload


class _ScriptedPlatform:
    def __init__(self, responses: list[ModelResponse]) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[str, ChatRequest]] = []

    def execute_chat(self, profile_id: str, request: ChatRequest) -> ModelResponse:
        self.requests.append((profile_id, request))
        if not self.responses:
            raise AssertionError("unexpected model request")
        return self.responses.pop(0)


def _runtime(
    tmp_path: Path,
    responses: list[ModelResponse],
    *,
    tools: ToolRegistry | None = None,
    limits: AgentLimits | None = None,
) -> tuple[AgentRuntime, _ScriptedPlatform]:
    platform = _ScriptedPlatform(responses)
    runtime = AgentRuntime(
        platform=platform,
        store=FileAgentSessionStore(tmp_path),
        tools=tools or builtin_read_only_tools(),
        limits=limits,
    )
    return runtime, platform


def test_openai_message_payload_preserves_assistant_tool_calls() -> None:
    message = AIMessage(
        role=MessageRole.ASSISTANT,
        content="",
        tool_calls=(ToolCall(call_id="call-1", name="calculator", arguments={"expression": "2+3"}),),
    )
    payload = _message_payload(message)

    assert payload["role"] == "assistant"
    assert payload["tool_calls"] == [
        {
            "id": "call-1",
            "type": "function",
            "function": {"name": "calculator", "arguments": '{"expression":"2+3"}'},
        }
    ]


def test_agent_turn_executes_tool_and_returns_observation_to_model(tmp_path: Path) -> None:
    runtime, platform = _runtime(
        tmp_path,
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(call_id="call-1", name="calculator", arguments={"expression": "2+3"}),
                ),
                usage=ModelUsage(input_tokens=10, output_tokens=5, total_tokens=15),
                finish_reason="tool_calls",
            ),
            ModelResponse(
                text="The result is 5.",
                usage=ModelUsage(input_tokens=20, output_tokens=6, total_tokens=26),
                finish_reason="stop",
            ),
        ],
    )
    session = runtime.create_session("agent.fast")

    result = runtime.start_turn(session.session_id, "Calculate 2+3 using the tool.")

    assert result.status is AgentStatus.COMPLETED
    assert result.final_text == "The result is 5."
    assert result.usage.total_tokens == 41
    assert len(platform.requests) == 2
    second_messages = platform.requests[1][1].messages
    assistant = next(message for message in second_messages if message.role is MessageRole.ASSISTANT)
    tool_result = next(message for message in second_messages if message.role is MessageRole.TOOL)
    assert assistant.tool_calls[0].call_id == "call-1"
    assert tool_result.tool_call_id == "call-1"
    assert '"value":5' in str(tool_result.content)

    persisted = runtime.get_session(session.session_id)
    assert persisted.status is AgentStatus.COMPLETED
    assert Path(persisted.workspace_dir).is_dir()
    event_kinds = [event.kind.value for event in runtime.store.events(session.session_id)]
    assert "tool_requested" in event_kinds
    assert "tool_completed" in event_kinds
    assert event_kinds[-1] == "turn_completed"


def test_mutating_tool_pauses_for_explicit_approval_then_resumes(tmp_path: Path) -> None:
    calls: list[str] = []

    def write_marker(_context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        calls.append(str(arguments["value"]))
        return ToolResult(ok=True, content="written")

    tools = ToolRegistry(
        (
            AgentTool(
                name="write_marker",
                description="Test mutating action.",
                input_schema={
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
                handler=write_marker,
                effect=ToolEffect.MUTATING,
            ),
        )
    )
    runtime, platform = _runtime(
        tmp_path,
        [
            ModelResponse(
                tool_calls=(ToolCall(call_id="write-1", name="write_marker", arguments={"value": "x"}),),
                finish_reason="tool_calls",
            ),
            ModelResponse(text="done", finish_reason="stop"),
        ],
        tools=tools,
    )
    session = runtime.create_session("agent.fast")

    paused = runtime.start_turn(session.session_id, "Write the marker.")
    assert paused.status is AgentStatus.WAITING_APPROVAL
    assert paused.pending_approval is not None
    assert paused.pending_approval.call_id == "write-1"
    assert calls == []
    assert len(platform.requests) == 1

    completed = runtime.resume_approval(session.session_id, "write-1", approved=True)
    assert completed.status is AgentStatus.COMPLETED
    assert completed.final_text == "done"
    assert calls == ["x"]
    assert len(platform.requests) == 2


def test_denied_tool_is_returned_to_model_without_execution(tmp_path: Path) -> None:
    calls: list[str] = []

    def destructive(_context: ToolContext, _arguments: dict[str, Any]) -> ToolResult:
        calls.append("executed")
        return ToolResult(ok=True, content="should not happen")

    tools = ToolRegistry(
        (
            AgentTool(
                name="dangerous_action",
                description="Approval test action.",
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                handler=destructive,
                effect=ToolEffect.SENSITIVE,
            ),
        )
    )
    runtime, platform = _runtime(
        tmp_path,
        [
            ModelResponse(
                tool_calls=(ToolCall(call_id="danger-1", name="dangerous_action", arguments={}),),
                finish_reason="tool_calls",
            ),
            ModelResponse(text="I did not perform the action.", finish_reason="stop"),
        ],
        tools=tools,
    )
    session = runtime.create_session("agent.fast")
    paused = runtime.start_turn(session.session_id, "Try the action.")

    result = runtime.resume_approval(session.session_id, "danger-1", approved=False)

    assert paused.status is AgentStatus.WAITING_APPROVAL
    assert result.status is AgentStatus.COMPLETED
    assert calls == []
    tool_message = next(
        message for message in platform.requests[1][1].messages if message.role is MessageRole.TOOL
    )
    assert "denied by the user" in str(tool_message.content)


def test_tool_errors_are_observations_not_harness_crashes(tmp_path: Path) -> None:
    runtime, platform = _runtime(
        tmp_path,
        [
            ModelResponse(
                tool_calls=(ToolCall(call_id="bad-1", name="missing_tool", arguments={}),),
                finish_reason="tool_calls",
            ),
            ModelResponse(text="Recovered after the tool error.", finish_reason="stop"),
        ],
    )
    session = runtime.create_session("agent.fast")

    result = runtime.start_turn(session.session_id, "Use the missing tool.")

    assert result.status is AgentStatus.COMPLETED
    assert result.final_text == "Recovered after the tool error."
    tool_message = next(
        message for message in platform.requests[1][1].messages if message.role is MessageRole.TOOL
    )
    assert "Unknown tool" in str(tool_message.content)


def test_invalid_tool_arguments_are_returned_to_model(tmp_path: Path) -> None:
    runtime, platform = _runtime(
        tmp_path,
        [
            ModelResponse(
                tool_calls=(ToolCall(call_id="bad-args", name="calculator", arguments={}),),
                finish_reason="tool_calls",
            ),
            ModelResponse(text="I corrected the request.", finish_reason="stop"),
        ],
    )
    session = runtime.create_session("agent.fast")

    result = runtime.start_turn(session.session_id, "Calculate something.")

    assert result.status is AgentStatus.COMPLETED
    tool_message = next(
        message for message in platform.requests[1][1].messages if message.role is MessageRole.TOOL
    )
    assert "Invalid tool arguments" in str(tool_message.content)


def test_model_step_limit_fails_closed_without_infinite_loop(tmp_path: Path) -> None:
    runtime, platform = _runtime(
        tmp_path,
        [
            ModelResponse(
                tool_calls=(ToolCall(call_id="echo-1", name="echo", arguments={"text": "x"}),),
                finish_reason="tool_calls",
            ),
        ],
        limits=AgentLimits(max_model_steps=1, max_tool_calls=4),
    )
    session = runtime.create_session("agent.fast")

    result = runtime.start_turn(session.session_id, "Keep using tools forever.")

    assert result.status is AgentStatus.LIMIT_REACHED
    assert "model step limit" in result.error
    assert len(platform.requests) == 1


def test_recovery_marks_only_active_turn_as_interrupted(tmp_path: Path) -> None:
    runtime, _platform = _runtime(tmp_path, [])
    session = runtime.create_session("agent.fast")
    persisted = runtime.get_session(session.session_id)
    persisted.status = AgentStatus.RUNNING
    persisted.current_turn_id = "turn-before-crash"
    runtime.store.save(persisted)

    result = runtime.recover_interrupted(session.session_id)

    assert result.status is AgentStatus.INTERRUPTED
    assert "stopped before" in result.error
    assert runtime.get_session(session.session_id).status is AgentStatus.INTERRUPTED


def test_cancel_clears_waiting_approval_without_executing_tool(tmp_path: Path) -> None:
    executed: list[bool] = []

    def write(_context: ToolContext, _arguments: dict[str, Any]) -> ToolResult:
        executed.append(True)
        return ToolResult(ok=True, content="done")

    tools = ToolRegistry(
        (
            AgentTool(
                name="write",
                description="Mutating test tool.",
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                handler=write,
                effect=ToolEffect.MUTATING,
            ),
        )
    )
    runtime, _platform = _runtime(
        tmp_path,
        [ModelResponse(tool_calls=(ToolCall(call_id="w1", name="write", arguments={}),))],
        tools=tools,
    )
    session = runtime.create_session("agent.fast")
    assert runtime.start_turn(session.session_id, "write").status is AgentStatus.WAITING_APPROVAL

    result = runtime.cancel(session.session_id)

    assert result.status is AgentStatus.CANCELLED
    assert executed == []
    persisted = runtime.get_session(session.session_id)
    assert persisted.pending_approval is None
    assert persisted.pending_tool_calls == []


def test_tool_context_cannot_escape_session_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = ToolContext(session_id="s", turn_id="t", workspace=workspace)

    with pytest.raises(ValueError, match="escapes"):
        context.resolve_workspace_path("../outside.txt")


def test_agent_runtime_remains_detached_from_existing_listing_production() -> None:
    root = Path(__file__).resolve().parents[1]
    registry = (root / "app" / "providers" / "registry.py").read_text(encoding="utf-8")
    gui_entry = (root / "run_local_gui.py").read_text(encoding="utf-8")

    assert "agent_runtime" not in registry
    assert "AgentRuntime" not in registry
    assert "agent_runtime" not in gui_entry
    assert "AgentRuntime" not in gui_entry
