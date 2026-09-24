from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Property, QUrl, Signal, Slot, Qt
from PySide6.QtGui import QDesktopServices

from app.agent_runtime import (
    AgentEvent,
    AgentEventKind,
    AgentRunResult,
    AgentRuntime,
    AgentStatus,
    AgentTool,
    FileAgentSessionStore,
    ToolContext,
    ToolEffect,
    ToolResult,
    builtin_read_only_tools,
)
from app.ai import (
    AGENT_FAST_ROLE,
    AIConfiguration,
    CredentialRef,
    CredentialResolver,
    ModelBinding,
    ProviderAdapter,
    ProviderConnection,
    build_ai_platform,
)


_DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
_RUNTIME_KEY_ALIAS = "agent-lab-key"


class AgentLabController(QObject):
    """Qt bridge for the detached Agent Workspace development surface.

    Secrets exist only long enough to build the in-memory provider runtime. The
    controller never writes them into Agent session state, events, or application
    settings. The formal Listing GUI does not import this controller.
    """

    statusChanged = Signal()
    configuredChanged = Signal()
    runningChanged = Signal()
    waitingApprovalChanged = Signal()
    sessionChanged = Signal()
    usageChanged = Signal()
    errorChanged = Signal()

    messageAdded = Signal(str, str, str)
    timelineAdded = Signal(str, str, str)
    sessionReset = Signal()

    _workerEvent = Signal(object)
    _workerResult = Signal(object)
    _workerFailure = Signal(str)

    def __init__(self, runtime_root: str | Path) -> None:
        super().__init__()
        self._runtime_root = Path(runtime_root).expanduser().resolve()
        self._store = FileAgentSessionStore(self._runtime_root)
        self._runtime: AgentRuntime | None = None
        self._session_id = ""
        self._workspace_path = ""
        self._status = "未连接 AI Provider"
        self._configured = False
        self._running = False
        self._waiting_approval = False
        self._approval_call_id = ""
        self._approval_title = ""
        self._approval_detail = ""
        self._input_tokens = 0
        self._output_tokens = 0
        self._total_tokens = 0
        self._error = ""
        self._worker: threading.Thread | None = None

        self._workerEvent.connect(self._apply_event, Qt.ConnectionType.QueuedConnection)
        self._workerResult.connect(self._apply_result, Qt.ConnectionType.QueuedConnection)
        self._workerFailure.connect(self._apply_worker_failure, Qt.ConnectionType.QueuedConnection)

    @Property(str, notify=statusChanged)
    def statusText(self) -> str:
        return self._status

    @Property(bool, notify=configuredChanged)
    def configured(self) -> bool:
        return self._configured

    @Property(bool, notify=runningChanged)
    def running(self) -> bool:
        return self._running

    @Property(bool, notify=waitingApprovalChanged)
    def waitingApproval(self) -> bool:
        return self._waiting_approval

    @Property(str, notify=waitingApprovalChanged)
    def approvalTitle(self) -> str:
        return self._approval_title

    @Property(str, notify=waitingApprovalChanged)
    def approvalDetail(self) -> str:
        return self._approval_detail

    @Property(str, notify=sessionChanged)
    def sessionId(self) -> str:
        return self._session_id

    @Property(str, notify=sessionChanged)
    def workspacePath(self) -> str:
        return self._workspace_path

    @Property(int, notify=usageChanged)
    def inputTokens(self) -> int:
        return self._input_tokens

    @Property(int, notify=usageChanged)
    def outputTokens(self) -> int:
        return self._output_tokens

    @Property(int, notify=usageChanged)
    def totalTokens(self) -> int:
        return self._total_tokens

    @Property(str, notify=errorChanged)
    def errorText(self) -> str:
        return self._error

    @Property(str, constant=True)
    def dashscopeBaseUrl(self) -> str:
        return _DASHSCOPE_BASE_URL

    @Slot(str, str, str, str, result=bool)
    def configureProvider(self, adapter_value: str, base_url: str, model: str, api_key: str) -> bool:
        if self._running:
            self._set_error("Agent 正在运行，不能中途替换模型配置。")
            return False
        adapter_text = str(adapter_value or "").strip().casefold()
        model_text = str(model or "").strip()
        secret = str(api_key or "").strip()
        if not model_text:
            self._set_error("请填写模型名称。")
            return False
        if not secret:
            self._set_error("请填写 API Key；它只保存在本次运行内存中。")
            return False
        try:
            adapter = ProviderAdapter(adapter_text)
            normalized_base = "" if adapter is ProviderAdapter.OPENAI else str(base_url or "").strip()
            connection = ProviderConnection(
                provider_id="agent-lab",
                adapter=adapter,
                credential_ref=CredentialRef.runtime(_RUNTIME_KEY_ALIAS),
                base_url=normalized_base,
                display_name="Agent Lab",
            )
            binding = ModelBinding(
                role_id=AGENT_FAST_ROLE.role_id,
                provider_id=connection.provider_id,
                model=model_text,
                capabilities=AGENT_FAST_ROLE.required_capabilities,
            )
            configuration = AIConfiguration.build(
                roles=(AGENT_FAST_ROLE,),
                providers=(connection,),
                bindings=(binding,),
            )
            resolver = CredentialResolver(
                runtime_lookup=lambda alias: secret if alias == _RUNTIME_KEY_ALIAS else None
            )
            platform = build_ai_platform(
                configuration,
                credential_resolver=resolver,
                request_timeout_seconds=90.0,
            )
            tools = builtin_read_only_tools()
            tools.register(self._workspace_write_note_tool())
            runtime = AgentRuntime(platform=platform, store=self._store, tools=tools)
            runtime.subscribe(lambda event: self._workerEvent.emit(event))
            session = runtime.create_session(AGENT_FAST_ROLE.role_id)
        except Exception as exc:
            self._set_error(f"配置失败：{type(exc).__name__}: {exc}")
            return False

        self._runtime = runtime
        self._session_id = session.session_id
        self._workspace_path = session.workspace_dir
        self._configured = True
        self._waiting_approval = False
        self._approval_call_id = ""
        self._approval_title = ""
        self._approval_detail = ""
        self._input_tokens = 0
        self._output_tokens = 0
        self._total_tokens = 0
        self._set_error("")
        self._set_status(f"已连接 · {adapter.value} · {model_text} · agent.fast")
        self.configuredChanged.emit()
        self.waitingApprovalChanged.emit()
        self.sessionChanged.emit()
        self.usageChanged.emit()
        self.sessionReset.emit()
        self.timelineAdded.emit("SESSION", "新 Agent Session", session.session_id)
        return True

    @Slot()
    def newSession(self) -> None:
        runtime = self._runtime
        if runtime is None or not self._configured:
            self._set_error("请先配置 AI Provider。")
            return
        if self._running:
            self._set_error("Agent 正在运行，不能创建新 Session。")
            return
        try:
            session = runtime.create_session(AGENT_FAST_ROLE.role_id)
        except Exception as exc:
            self._set_error(f"创建 Session 失败：{type(exc).__name__}: {exc}")
            return
        self._session_id = session.session_id
        self._workspace_path = session.workspace_dir
        self._waiting_approval = False
        self._approval_call_id = ""
        self._approval_title = ""
        self._approval_detail = ""
        self._input_tokens = 0
        self._output_tokens = 0
        self._total_tokens = 0
        self.sessionChanged.emit()
        self.waitingApprovalChanged.emit()
        self.usageChanged.emit()
        self.sessionReset.emit()
        self._set_error("")
        self._set_status("新 Session 已就绪")
        self.timelineAdded.emit("SESSION", "新 Agent Session", session.session_id)

    @Slot(str)
    def sendMessage(self, text: str) -> None:
        prompt = str(text or "").strip()
        if not prompt:
            return
        runtime = self._runtime
        if runtime is None or not self._configured or not self._session_id:
            self._set_error("请先配置 AI Provider。")
            return
        if self._running:
            self._set_error("当前 Turn 仍在运行。")
            return
        if self._waiting_approval:
            self._set_error("请先批准或拒绝当前 Tool Call。")
            return
        self.messageAdded.emit("user", prompt, "YOU")
        self._set_error("")
        self._set_running(True)
        self._set_status("Agent 正在执行…")
        session_id = self._session_id

        def worker() -> None:
            try:
                result = runtime.start_turn(session_id, prompt)
                self._workerResult.emit(result)
            except Exception as exc:
                self._workerFailure.emit(f"{type(exc).__name__}: {exc}")

        self._start_worker(worker, "agent-lab-turn")

    @Slot(bool)
    def resolveApproval(self, approved: bool) -> None:
        runtime = self._runtime
        call_id = self._approval_call_id
        if runtime is None or not self._session_id or not call_id or not self._waiting_approval:
            return
        if self._running:
            return
        self._set_waiting_approval(False, "", "", "")
        self._set_running(True)
        self._set_status("已批准，Agent 继续执行…" if approved else "已拒绝，Agent 正在处理结果…")
        session_id = self._session_id

        def worker() -> None:
            try:
                result = runtime.resume_approval(session_id, call_id, approved=bool(approved))
                self._workerResult.emit(result)
            except Exception as exc:
                self._workerFailure.emit(f"{type(exc).__name__}: {exc}")

        self._start_worker(worker, "agent-lab-approval")

    @Slot()
    def cancelCurrent(self) -> None:
        runtime = self._runtime
        if runtime is None or not self._session_id:
            return
        try:
            result = runtime.cancel(self._session_id)
        except Exception as exc:
            self._set_error(f"停止失败：{type(exc).__name__}: {exc}")
            return
        if result.status is AgentStatus.CANCELLED:
            self._apply_result(result)
        else:
            self._set_status("正在停止 Agent…")

    @Slot()
    def openWorkspace(self) -> None:
        if not self._workspace_path:
            return
        path = Path(self._workspace_path)
        path.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    @Slot(object)
    def _apply_event(self, event_obj: object) -> None:
        if not isinstance(event_obj, AgentEvent):
            return
        event = event_obj
        data = event.data
        kind = event.kind
        if kind is AgentEventKind.MODEL_REQUESTED:
            detail = f"step {data.get('step', '—')} · {data.get('message_count', '—')} messages"
            self.timelineAdded.emit("MODEL", "请求模型", detail)
        elif kind is AgentEventKind.MODEL_RESPONSE:
            text = str(data.get("text") or "").strip()
            calls = data.get("tool_calls") or []
            if text:
                self.messageAdded.emit("assistant", text, "AGENT")
            detail = text[:240] if text else f"请求 {len(calls)} 个工具"
            self.timelineAdded.emit("MODEL", "模型响应", detail)
            usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
            if usage:
                self.timelineAdded.emit(
                    "USAGE",
                    "Token usage",
                    f"in {usage.get('input_tokens', 0)} · out {usage.get('output_tokens', 0)}",
                )
        elif kind is AgentEventKind.TOOL_REQUESTED:
            arguments = json.dumps(data.get("arguments") or {}, ensure_ascii=False)
            self.timelineAdded.emit("TOOL", f"请求 · {data.get('tool', '')}", arguments[:500])
        elif kind is AgentEventKind.TOOL_STARTED:
            self.timelineAdded.emit("TOOL", f"执行 · {data.get('tool', '')}", str(data.get("call_id") or ""))
        elif kind in {AgentEventKind.TOOL_COMPLETED, AgentEventKind.TOOL_FAILED}:
            title = "完成" if kind is AgentEventKind.TOOL_COMPLETED else "失败"
            content = str(data.get("content") or "")
            self.timelineAdded.emit("TOOL", f"{title} · {data.get('tool', '')}", content[:700])
        elif kind is AgentEventKind.TOOL_APPROVAL_REQUIRED:
            tool = str(data.get("tool") or "")
            arguments = json.dumps(data.get("arguments") or {}, ensure_ascii=False, indent=2)
            self._set_waiting_approval(
                True,
                str(data.get("call_id") or ""),
                f"{tool} 需要你的批准",
                f"Effect: {data.get('effect', '')}\n\n{arguments}",
            )
            self.timelineAdded.emit("APPROVAL", "等待批准", tool)
        elif kind is AgentEventKind.TOOL_APPROVED:
            self.timelineAdded.emit("APPROVAL", "已批准", str(data.get("tool") or ""))
        elif kind is AgentEventKind.TOOL_DENIED:
            self.timelineAdded.emit("APPROVAL", "已拒绝", str(data.get("tool") or ""))
        elif kind is AgentEventKind.TURN_CANCELLED:
            self.timelineAdded.emit("TURN", "Turn 已停止", "用户取消")
        elif kind is AgentEventKind.TURN_FAILED:
            self.timelineAdded.emit("TURN", "Turn 失败", str(data.get("error") or ""))
        elif kind is AgentEventKind.LIMIT_REACHED:
            self.timelineAdded.emit("TURN", "达到 Harness 限制", str(data.get("reason") or ""))

    @Slot(object)
    def _apply_result(self, result_obj: object) -> None:
        if not isinstance(result_obj, AgentRunResult):
            return
        result = result_obj
        self._set_running(False)
        self._input_tokens = result.usage.input_tokens
        self._output_tokens = result.usage.output_tokens
        self._total_tokens = result.usage.total_tokens
        self.usageChanged.emit()
        if result.status is AgentStatus.WAITING_APPROVAL and result.pending_approval is not None:
            pending = result.pending_approval
            detail = json.dumps(pending.arguments, ensure_ascii=False, indent=2)
            self._set_waiting_approval(
                True,
                pending.call_id,
                f"{pending.tool_name} 需要你的批准",
                f"Effect: {pending.effect.value}\n\n{detail}",
            )
            self._set_status("等待 Tool Approval")
        elif result.status is AgentStatus.COMPLETED:
            self._set_waiting_approval(False, "", "", "")
            self._set_status("Turn 完成")
            self._set_error("")
        elif result.status is AgentStatus.CANCELLED:
            self._set_waiting_approval(False, "", "", "")
            self._set_status("Turn 已停止")
        elif result.status is AgentStatus.LIMIT_REACHED:
            self._set_waiting_approval(False, "", "", "")
            self._set_status("达到 Harness 限制")
            self._set_error(result.error)
        elif result.status in {AgentStatus.FAILED, AgentStatus.INTERRUPTED}:
            self._set_waiting_approval(False, "", "", "")
            self._set_status("Turn 失败" if result.status is AgentStatus.FAILED else "Turn 中断")
            self._set_error(result.error)
        else:
            self._set_status(result.status.value)

    @Slot(str)
    def _apply_worker_failure(self, message: str) -> None:
        self._set_running(False)
        self._set_status("Agent Runtime 调用失败")
        self._set_error(message)

    def _start_worker(self, target: Any, name: str) -> None:
        if self._worker is not None and self._worker.is_alive():
            raise RuntimeError("agent lab worker is already running")
        worker = threading.Thread(target=target, name=name, daemon=True)
        self._worker = worker
        worker.start()

    def _set_status(self, value: str) -> None:
        value = str(value or "")
        if value == self._status:
            return
        self._status = value
        self.statusChanged.emit()

    def _set_running(self, value: bool) -> None:
        value = bool(value)
        if value == self._running:
            return
        self._running = value
        self.runningChanged.emit()

    def _set_waiting_approval(self, value: bool, call_id: str, title: str, detail: str) -> None:
        self._waiting_approval = bool(value)
        self._approval_call_id = str(call_id or "")
        self._approval_title = str(title or "")
        self._approval_detail = str(detail or "")
        self.waitingApprovalChanged.emit()

    def _set_error(self, value: str) -> None:
        value = str(value or "")
        if value == self._error:
            return
        self._error = value
        self.errorChanged.emit()

    @staticmethod
    def _workspace_write_note_tool() -> AgentTool:
        def write_note(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
            context.raise_if_cancelled()
            relative = str(arguments["path"] or "").strip()
            text = str(arguments["text"] or "")
            if not relative:
                raise ValueError("path must not be empty")
            if len(text) > 64_000:
                raise ValueError("workspace note exceeds 64,000 characters")
            target = context.resolve_workspace_path(relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            context.raise_if_cancelled()
            target.write_text(text, encoding="utf-8")
            return ToolResult(
                ok=True,
                content=f"Wrote workspace note: {relative}",
                data={"path": relative, "chars": len(text)},
            )

        return AgentTool(
            name="write_workspace_note",
            description=(
                "Write a UTF-8 note inside this Agent Session workspace. "
                "This diagnostic mutating tool always requires explicit user approval."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["path", "text"],
                "additionalProperties": False,
            },
            handler=write_note,
            effect=ToolEffect.MUTATING,
        )


__all__ = ["AgentLabController"]
