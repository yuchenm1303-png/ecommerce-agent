from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, QObject, QPoint, Property, QTimer, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtQml import QQmlComponent
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtWidgets import QHBoxLayout, QMainWindow, QPushButton, QSizePolicy, QVBoxLayout, QWidget

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
    MessageRole,
    ModelBinding,
    ProviderAdapter,
    ProviderConnection,
    build_ai_platform,
)


_DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
_RUNTIME_KEY_ALIAS = "main-agent-workspace-key"
_DEFAULT_MODEL = "qwen-plus"


class AgentWorkspaceController(QObject):
    """Quick-owned Agent workspace attached to the existing Listing Studio window.

    Listing stays alive underneath as the stable business workspace. Opening Agent
    only changes presentation routing inside the existing QQuickWindow; it does not
    import the Agent runtime into the legacy Listing provider registry or alter
    Single/Batch execution semantics.
    """

    openChanged = Signal()
    configuredChanged = Signal()
    runningChanged = Signal()
    approvalChanged = Signal()
    statusChanged = Signal()
    sessionsChanged = Signal()
    conversationChanged = Signal()
    activityChanged = Signal()
    filesChanged = Signal()
    usageChanged = Signal()
    sessionChanged = Signal()
    errorChanged = Signal()
    geometryChanged = Signal()

    _workerEvent = Signal(object)
    _workerResult = Signal(object)
    _workerFailure = Signal(str)

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self.window = window
        self.runtime_root = Path(getattr(window, "project_root", Path.cwd())).resolve()
        # Formal Agent sessions are isolated from the standalone Agent Lab while
        # using the same durable Harness storage contract.
        self.store = FileAgentSessionStore(self.runtime_root / ".agent_workspace")
        self.runtime: AgentRuntime | None = None
        self._open = False
        self._configured = False
        self._running = False
        self._waiting_approval = False
        self._status = "Agent 未初始化"
        self._error = ""
        self._session_id = ""
        self._workspace_path = ""
        self._approval_call_id = ""
        self._approval_title = ""
        self._approval_detail = ""
        self._provider_label = "DashScope"
        self._model_label = str(os.getenv("ECOMMERCE_AGENT_AGENT_MODEL", "") or _DEFAULT_MODEL).strip()
        self._input_tokens = 0
        self._output_tokens = 0
        self._total_tokens = 0
        self._sessions: list[dict[str, Any]] = []
        self._conversation: list[dict[str, Any]] = []
        self._activity: list[dict[str, Any]] = []
        self._files: list[dict[str, Any]] = []
        self._tool_rows: dict[str, int] = {}
        self._worker: threading.Thread | None = None
        self._entry_button: QPushButton | None = None
        self._qml_item: QQuickItem | None = None
        self._qml_component: QQmlComponent | None = None
        self._static_view: Any = None
        self._content_top = 86

        self._workerEvent.connect(self._apply_event, Qt.ConnectionType.QueuedConnection)
        self._workerResult.connect(self._apply_result, Qt.ConnectionType.QueuedConnection)
        self._workerFailure.connect(self._apply_worker_failure, Qt.ConnectionType.QueuedConnection)

        self._install_header_entry()
        self.window.installEventFilter(self)
        central = self.window.centralWidget()
        if central is not None:
            central.installEventFilter(self)
        self.window.destroyed.connect(self._cleanup)
        QTimer.singleShot(0, self._refresh_content_top)

    @Property(bool, notify=openChanged)
    def open(self) -> bool:
        return self._open

    @Property(bool, notify=configuredChanged)
    def configured(self) -> bool:
        return self._configured

    @Property(bool, notify=runningChanged)
    def running(self) -> bool:
        return self._running

    @Property(bool, notify=approvalChanged)
    def waitingApproval(self) -> bool:
        return self._waiting_approval

    @Property(str, notify=statusChanged)
    def statusText(self) -> str:
        return self._status

    @Property(str, notify=errorChanged)
    def errorText(self) -> str:
        return self._error

    @Property(str, notify=sessionChanged)
    def currentSessionId(self) -> str:
        return self._session_id

    @Property(str, notify=sessionChanged)
    def workspacePath(self) -> str:
        return self._workspace_path

    @Property(str, notify=approvalChanged)
    def approvalTitle(self) -> str:
        return self._approval_title

    @Property(str, notify=approvalChanged)
    def approvalDetail(self) -> str:
        return self._approval_detail

    @Property(str, notify=configuredChanged)
    def modelLabel(self) -> str:
        return self._model_label

    @Property(str, notify=configuredChanged)
    def providerLabel(self) -> str:
        return self._provider_label

    @Property(int, notify=usageChanged)
    def inputTokens(self) -> int:
        return self._input_tokens

    @Property(int, notify=usageChanged)
    def outputTokens(self) -> int:
        return self._output_tokens

    @Property(int, notify=usageChanged)
    def totalTokens(self) -> int:
        return self._total_tokens

    @Property(int, notify=geometryChanged)
    def contentTop(self) -> int:
        return self._content_top

    @Property("QVariantList", notify=sessionsChanged)
    def sessions(self):  # noqa: ANN201
        return self._sessions

    @Property("QVariantList", notify=conversationChanged)
    def conversationItems(self):  # noqa: ANN201
        return self._conversation

    @Property("QVariantList", notify=activityChanged)
    def activityItems(self):  # noqa: ANN201
        return self._activity

    @Property("QVariantList", notify=filesChanged)
    def workspaceFiles(self):  # noqa: ANN201
        return self._files

    def attach_quick(self, static_view: Any) -> None:
        if self._qml_item is not None:
            return
        quick = getattr(static_view, "quick", None)
        engine = getattr(static_view, "engine", None)
        if not isinstance(quick, QQuickWindow) or engine is None:
            raise RuntimeError("Agent workspace requires the existing unified QQuickWindow")
        self._static_view = static_view
        engine.rootContext().setContextProperty("agentWorkspace", self)
        component = QQmlComponent(engine, self)
        self._qml_component = component
        component.statusChanged.connect(self._qml_status_changed)
        component.loadUrl(QUrl.fromLocalFile(str(Path(__file__).resolve().parent / "qml" / "AgentWorkspace.qml")))
        self._finish_qml_attach()
        bridge = getattr(static_view, "bridge", None)
        schedule = getattr(bridge, "schedule_structure_refresh", None)
        if callable(schedule):
            schedule()

    def _qml_status_changed(self, _status: QQmlComponent.Status) -> None:
        self._finish_qml_attach()

    def _finish_qml_attach(self) -> None:
        component = self._qml_component
        static_view = self._static_view
        if component is None or static_view is None or self._qml_item is not None:
            return
        status = component.status()
        if status in {QQmlComponent.Status.Null, QQmlComponent.Status.Loading}:
            return
        if status is QQmlComponent.Status.Error:
            detail = "\n".join(error.toString() for error in component.errors())
            raise RuntimeError(f"AgentWorkspace.qml failed to load:\n{detail}")
        created = component.create(static_view.engine.rootContext())
        if not isinstance(created, QQuickItem):
            raise RuntimeError("AgentWorkspace.qml root must be a QQuickItem")
        created.setParent(self)
        created.setParentItem(static_view.quick.contentItem())
        created.setZ(22000.0)
        self._qml_item = created

    def _install_header_entry(self) -> None:
        root = self.window.centralWidget()
        outer = root.layout() if root is not None else None
        if root is None or not isinstance(outer, QVBoxLayout) or outer.count() < 1:
            raise RuntimeError("Agent workspace expected the preserved main root layout")
        header_item = outer.itemAt(0)
        header = header_item.layout() if header_item is not None else None
        if not isinstance(header, QHBoxLayout):
            raise RuntimeError("Agent workspace expected the common header row")

        button = QPushButton("AGENT", root)
        button.setObjectName("quietButton")
        button.setMinimumWidth(82)
        button.setFixedHeight(32)
        button.setToolTip("打开 AI Agent 工作空间")
        button.clicked.connect(self.toggleWorkspace)
        header.addWidget(button, 0, Qt.AlignmentFlag.AlignBottom)
        self._entry_button = button

    def _ensure_runtime(self) -> bool:
        if self.runtime is not None and self._configured:
            return True
        secret = str(os.getenv("DASHSCOPE_API_KEY", "") or os.getenv("AI_API_KEY", "") or "").strip()
        if not secret:
            self._set_error("未找到 DASHSCOPE_API_KEY / AI_API_KEY。请先配置 Agent 模型凭据。")
            self._set_status("Agent 需要 AI 凭据")
            return False
        model = str(os.getenv("ECOMMERCE_AGENT_AGENT_MODEL", "") or _DEFAULT_MODEL).strip()
        try:
            connection = ProviderConnection(
                provider_id="main-agent",
                adapter=ProviderAdapter.OPENAI_COMPATIBLE,
                credential_ref=CredentialRef.runtime(_RUNTIME_KEY_ALIAS),
                base_url=_DASHSCOPE_BASE_URL,
                display_name="DashScope",
            )
            binding = ModelBinding(
                role_id=AGENT_FAST_ROLE.role_id,
                provider_id=connection.provider_id,
                model=model,
                capabilities=AGENT_FAST_ROLE.required_capabilities,
            )
            configuration = AIConfiguration.build(
                roles=(AGENT_FAST_ROLE,),
                providers=(connection,),
                bindings=(binding,),
            )
            resolver = CredentialResolver(runtime_lookup=lambda alias: secret if alias == _RUNTIME_KEY_ALIAS else None)
            platform = build_ai_platform(
                configuration,
                credential_resolver=resolver,
                request_timeout_seconds=90.0,
            )
            tools = builtin_read_only_tools()
            tools.register(self._workspace_write_note_tool())
            runtime = AgentRuntime(platform=platform, store=self.store, tools=tools)
            runtime.subscribe(lambda event: self._workerEvent.emit(event))
        except Exception as exc:
            self._set_error(f"Agent Runtime 初始化失败：{type(exc).__name__}: {exc}")
            self._set_status("Agent 初始化失败")
            return False
        self.runtime = runtime
        self._configured = True
        self._model_label = model
        self._provider_label = "DashScope"
        self.configuredChanged.emit()
        self._set_error("")
        self._refresh_sessions()
        if self._sessions:
            self._load_session(str(self._sessions[0]["sessionId"]))
        else:
            self._create_session()
        return True

    @Slot()
    def toggleWorkspace(self) -> None:
        if self._open:
            self.openListing()
        else:
            self.openWorkspaceView()

    @Slot()
    def openWorkspaceView(self) -> None:
        if not self._ensure_runtime():
            # Open the surface anyway so the missing-credential state is visible.
            pass
        self._open = True
        self._sync_header_route()
        self.openChanged.emit()

    @Slot()
    def openListing(self) -> None:
        if not self._open:
            return
        self._open = False
        self._sync_header_route()
        self.openChanged.emit()
        set_mode = getattr(self.window, "_set_workspace_mode", None)
        mode_stack = getattr(self.window, "mode_stack", None)
        if callable(set_mode) and mode_stack is not None:
            set_mode(int(mode_stack.currentIndex()))

    @Slot()
    def newSession(self) -> None:
        if not self._ensure_runtime() or self._running:
            return
        self._create_session()

    def _create_session(self) -> None:
        runtime = self.runtime
        if runtime is None:
            return
        try:
            session = runtime.create_session(AGENT_FAST_ROLE.role_id)
        except Exception as exc:
            self._set_error(f"创建 Agent Session 失败：{type(exc).__name__}: {exc}")
            return
        self._load_session(session.session_id)
        self._refresh_sessions()
        self._set_status("新 Agent 任务已就绪")

    @Slot(str)
    def selectSession(self, session_id: str) -> None:
        if self._running:
            self._set_error("当前 Agent 任务仍在运行，暂时不能切换 Session。")
            return
        self._load_session(str(session_id or "").strip())

    def _session_snapshots(self) -> list[Any]:
        output = []
        for directory in self.store.root.iterdir() if self.store.root.is_dir() else ():
            target = directory / "session.json"
            if not target.is_file():
                continue
            try:
                output.append(self.store.load(directory.name))
            except Exception:
                continue
        output.sort(key=lambda item: str(item.updated_at or ""), reverse=True)
        return output

    def _refresh_sessions(self) -> None:
        rows: list[dict[str, Any]] = []
        for session in self._session_snapshots():
            title = "新 Agent 任务"
            for message in session.messages:
                if message.role is MessageRole.USER and isinstance(message.content, str) and message.content.strip():
                    title = message.content.strip().replace("\n", " ")[:32]
                    break
            rows.append(
                {
                    "sessionId": session.session_id,
                    "title": title,
                    "meta": str(session.updated_at or "").replace("T", " ")[:16],
                    "status": session.status.value,
                    "active": session.session_id == self._session_id,
                }
            )
        self._sessions = rows
        self.sessionsChanged.emit()

    def _load_session(self, session_id: str) -> None:
        if not session_id:
            return
        try:
            session = self.store.load(session_id)
            events = self.store.events(session_id)
        except Exception as exc:
            self._set_error(f"读取 Agent Session 失败：{type(exc).__name__}: {exc}")
            return
        if session.status is AgentStatus.RUNNING and self.runtime is not None:
            session = self.runtime.recover_interrupted(session_id)
        self._session_id = session.session_id
        self._workspace_path = session.workspace_dir
        self._input_tokens = session.usage.input_tokens
        self._output_tokens = session.usage.output_tokens
        self._total_tokens = session.usage.total_tokens
        self._waiting_approval = session.status is AgentStatus.WAITING_APPROVAL and session.pending_approval is not None
        if session.pending_approval is not None:
            pending = session.pending_approval
            self._approval_call_id = pending.call_id
            self._approval_title = f"{pending.tool_name} 需要你的批准"
            self._approval_detail = json.dumps(pending.arguments, ensure_ascii=False, indent=2)
        else:
            self._approval_call_id = ""
            self._approval_title = ""
            self._approval_detail = ""
        self._rebuild_from_events(events)
        self._refresh_files()
        self.sessionChanged.emit()
        self.usageChanged.emit()
        self.approvalChanged.emit()
        self._refresh_sessions()
        self._set_status(self._friendly_status(session.status))
        self._set_error(session.error if session.status in {AgentStatus.FAILED, AgentStatus.INTERRUPTED, AgentStatus.LIMIT_REACHED} else "")

    @staticmethod
    def _friendly_status(status: AgentStatus) -> str:
        return {
            AgentStatus.IDLE: "Agent 已就绪",
            AgentStatus.RUNNING: "Agent 正在工作",
            AgentStatus.WAITING_APPROVAL: "等待你的批准",
            AgentStatus.COMPLETED: "任务已完成",
            AgentStatus.CANCELLED: "任务已停止",
            AgentStatus.FAILED: "任务失败",
            AgentStatus.INTERRUPTED: "任务已中断",
            AgentStatus.LIMIT_REACHED: "达到 Harness 限制",
        }.get(status, status.value)

    def _rebuild_from_events(self, events: tuple[AgentEvent, ...]) -> None:
        self._conversation = []
        self._activity = []
        self._tool_rows = {}
        for event in events:
            self._apply_event_models(event, emit=False)
        self.conversationChanged.emit()
        self.activityChanged.emit()

    @Slot(str)
    def sendMessage(self, text: str) -> None:
        prompt = str(text or "").strip()
        runtime = self.runtime
        if not prompt or runtime is None or not self._session_id or self._running or self._waiting_approval:
            return
        self._set_error("")
        self._set_running(True)
        self._set_status("Agent 正在工作…")
        session_id = self._session_id

        def worker() -> None:
            try:
                self._workerResult.emit(runtime.start_turn(session_id, prompt))
            except Exception as exc:
                self._workerFailure.emit(f"{type(exc).__name__}: {exc}")

        self._start_worker(worker, "main-agent-turn")

    @Slot(bool)
    def resolveApproval(self, approved: bool) -> None:
        runtime = self.runtime
        if runtime is None or not self._session_id or not self._approval_call_id or self._running:
            return
        call_id = self._approval_call_id
        self._set_approval(False, "", "", "")
        self._set_running(True)
        self._set_status("Agent 正在继续…")
        session_id = self._session_id

        def worker() -> None:
            try:
                self._workerResult.emit(runtime.resume_approval(session_id, call_id, approved=bool(approved)))
            except Exception as exc:
                self._workerFailure.emit(f"{type(exc).__name__}: {exc}")

        self._start_worker(worker, "main-agent-approval")

    @Slot()
    def cancelCurrent(self) -> None:
        runtime = self.runtime
        if runtime is None or not self._session_id:
            return
        try:
            result = runtime.cancel(self._session_id)
        except Exception as exc:
            self._set_error(f"停止 Agent 失败：{type(exc).__name__}: {exc}")
            return
        if result.status is AgentStatus.CANCELLED:
            self._apply_result(result)
        else:
            self._set_status("正在停止 Agent…")

    @Slot()
    def openWorkspaceFolder(self) -> None:
        if not self._workspace_path:
            return
        path = Path(self._workspace_path)
        path.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    @Slot(str)
    def openWorkspaceFile(self, relative_path: str) -> None:
        if not self._workspace_path:
            return
        root = Path(self._workspace_path).resolve()
        target = (root / str(relative_path or "")).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            return
        if target.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    @Slot()
    def refreshWorkspaceFiles(self) -> None:
        self._refresh_files()

    def _refresh_files(self) -> None:
        rows: list[dict[str, Any]] = []
        if self._workspace_path:
            root = Path(self._workspace_path)
            if root.is_dir():
                for path in sorted(root.rglob("*")):
                    if not path.is_file():
                        continue
                    try:
                        size = path.stat().st_size
                        relative = path.relative_to(root).as_posix()
                    except OSError:
                        continue
                    rows.append({"name": path.name, "path": relative, "meta": self._size_text(size)})
                    if len(rows) >= 200:
                        break
        self._files = rows
        self.filesChanged.emit()

    @staticmethod
    def _size_text(size: int) -> str:
        if size < 1024:
            return f"{size} B"
        if size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        return f"{size / (1024 * 1024):.1f} MB"

    @Slot(object)
    def _apply_event(self, event_obj: object) -> None:
        if not isinstance(event_obj, AgentEvent) or event_obj.session_id != self._session_id:
            return
        self._apply_event_models(event_obj, emit=True)

    def _apply_event_models(self, event: AgentEvent, *, emit: bool) -> None:
        data = event.data
        kind = event.kind
        clock = str(event.created_at or "")[11:19]
        if kind is AgentEventKind.USER_MESSAGE:
            self._conversation.append({"kind": "message", "role": "user", "title": "你", "body": str(data.get("text") or ""), "meta": clock, "state": ""})
        elif kind is AgentEventKind.MODEL_RESPONSE:
            text = str(data.get("text") or "").strip()
            if text:
                self._conversation.append({"kind": "message", "role": "assistant", "title": "Agent", "body": text, "meta": clock, "state": ""})
        elif kind is AgentEventKind.TOOL_REQUESTED:
            call_id = str(data.get("call_id") or "")
            row = {
                "kind": "tool",
                "role": "tool",
                "title": self._friendly_tool(str(data.get("tool") or "")),
                "body": "准备执行",
                "meta": str(data.get("tool") or ""),
                "state": "requested",
                "callId": call_id,
            }
            self._tool_rows[call_id] = len(self._conversation)
            self._conversation.append(row)
        elif kind is AgentEventKind.TOOL_STARTED:
            self._update_tool(str(data.get("call_id") or ""), state="running", body="正在执行…")
        elif kind in {AgentEventKind.TOOL_COMPLETED, AgentEventKind.TOOL_FAILED}:
            state = "done" if kind is AgentEventKind.TOOL_COMPLETED else "failed"
            body = str(data.get("content") or "")[:500]
            self._update_tool(str(data.get("call_id") or ""), state=state, body=body or ("完成" if state == "done" else "执行失败"))
        elif kind is AgentEventKind.TOOL_APPROVAL_REQUIRED:
            self._update_tool(str(data.get("call_id") or ""), state="approval", body="等待你的批准")
            arguments = json.dumps(data.get("arguments") or {}, ensure_ascii=False, indent=2)
            self._set_approval(True, str(data.get("call_id") or ""), f"{self._friendly_tool(str(data.get('tool') or ''))}需要你的批准", arguments)
        elif kind is AgentEventKind.TOOL_APPROVED:
            self._update_tool(str(data.get("call_id") or ""), state="approved", body="已批准，正在继续")
        elif kind is AgentEventKind.TOOL_DENIED:
            self._update_tool(str(data.get("call_id") or ""), state="denied", body="你已拒绝此操作")

        title, detail, tone = self._activity_copy(event)
        if title:
            self._activity.append({"kind": kind.value, "title": title, "detail": detail, "time": clock, "tone": tone})
            self._activity = self._activity[-120:]
        if emit:
            self.conversationChanged.emit()
            self.activityChanged.emit()

    @staticmethod
    def _friendly_tool(name: str) -> str:
        return {
            "calculator": "计算",
            "echo": "处理文本",
            "list_workspace_files": "查看工作区文件",
            "read_workspace_text": "读取工作区文件",
            "write_workspace_note": "写入工作区文件",
        }.get(name, name or "执行工具")

    @staticmethod
    def _activity_copy(event: AgentEvent) -> tuple[str, str, str]:
        data = event.data
        kind = event.kind
        if kind is AgentEventKind.MODEL_REQUESTED:
            return "分析任务", f"Model step {data.get('step', '—')}", "model"
        if kind is AgentEventKind.MODEL_RESPONSE:
            calls = data.get("tool_calls") or []
            text = str(data.get("text") or "").strip()
            return "模型响应", text[:120] if text else f"请求 {len(calls)} 个工具", "model"
        if kind is AgentEventKind.TOOL_REQUESTED:
            return f"请求 · {data.get('tool', '')}", "准备工具调用", "tool"
        if kind is AgentEventKind.TOOL_STARTED:
            return f"执行 · {data.get('tool', '')}", "Tool running", "tool"
        if kind is AgentEventKind.TOOL_COMPLETED:
            return f"完成 · {data.get('tool', '')}", str(data.get("content") or "")[:120], "success"
        if kind is AgentEventKind.TOOL_FAILED:
            return f"失败 · {data.get('tool', '')}", str(data.get("content") or "")[:120], "error"
        if kind is AgentEventKind.TOOL_APPROVAL_REQUIRED:
            return "等待批准", str(data.get("tool") or ""), "approval"
        if kind is AgentEventKind.TOOL_APPROVED:
            return "已批准", str(data.get("tool") or ""), "approval"
        if kind is AgentEventKind.TOOL_DENIED:
            return "已拒绝", str(data.get("tool") or ""), "error"
        if kind is AgentEventKind.TURN_COMPLETED:
            return "任务完成", "Agent 已完成当前 Turn", "success"
        if kind is AgentEventKind.TURN_CANCELLED:
            return "任务停止", "用户取消", "muted"
        if kind is AgentEventKind.TURN_FAILED:
            return "任务失败", str(data.get("error") or ""), "error"
        if kind is AgentEventKind.LIMIT_REACHED:
            return "达到 Harness 限制", str(data.get("reason") or ""), "error"
        return "", "", "muted"

    def _update_tool(self, call_id: str, *, state: str, body: str) -> None:
        index = self._tool_rows.get(call_id)
        if index is None or not 0 <= index < len(self._conversation):
            return
        row = dict(self._conversation[index])
        row["state"] = state
        row["body"] = body
        self._conversation[index] = row

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
            self._set_approval(True, pending.call_id, f"{self._friendly_tool(pending.tool_name)}需要你的批准", json.dumps(pending.arguments, ensure_ascii=False, indent=2))
        else:
            self._set_approval(False, "", "", "")
        self._set_status(self._friendly_status(result.status))
        self._set_error(result.error if result.status in {AgentStatus.FAILED, AgentStatus.INTERRUPTED, AgentStatus.LIMIT_REACHED} else "")
        self._refresh_files()
        self._refresh_sessions()
        self._sync_header_route()

    @Slot(str)
    def _apply_worker_failure(self, message: str) -> None:
        self._set_running(False)
        self._set_status("Agent Runtime 调用失败")
        self._set_error(message)
        self._sync_header_route()

    def _start_worker(self, target: Any, name: str) -> None:
        if self._worker is not None and self._worker.is_alive():
            raise RuntimeError("Agent workspace worker is already running")
        worker = threading.Thread(target=target, name=name, daemon=True)
        self._worker = worker
        worker.start()

    def _set_running(self, value: bool) -> None:
        value = bool(value)
        if value == self._running:
            return
        self._running = value
        self.runningChanged.emit()
        self._sync_header_route()

    def _set_approval(self, value: bool, call_id: str, title: str, detail: str) -> None:
        self._waiting_approval = bool(value)
        self._approval_call_id = str(call_id or "")
        self._approval_title = str(title or "")
        self._approval_detail = str(detail or "")
        self.approvalChanged.emit()
        self._sync_header_route()

    def _set_status(self, value: str) -> None:
        value = str(value or "")
        if value == self._status:
            return
        self._status = value
        self.statusChanged.emit()
        if self._open:
            badge = getattr(self.window, "phase_badge", None)
            if badge is not None:
                try:
                    badge.setText(f"AGENT · {value}")
                except RuntimeError:
                    pass

    def _set_error(self, value: str) -> None:
        value = str(value or "")
        if value == self._error:
            return
        self._error = value
        self.errorChanged.emit()

    def _sync_header_route(self) -> None:
        button = self._entry_button
        if button is not None:
            suffix = " !" if self._waiting_approval else " •" if self._running else ""
            button.setText("LISTING" if self._open else f"AGENT{suffix}")
            button.setToolTip("返回 Listing 工作区" if self._open else "打开 AI Agent 工作空间")
        toggle = getattr(self.window, "_workspace_mode_switch", None)
        if isinstance(toggle, QWidget):
            policy = toggle.sizePolicy()
            policy.setRetainSizeWhenHidden(True)
            toggle.setSizePolicy(policy)
            toggle.setVisible(not self._open)
        if self._open:
            self._set_status(self._status)
        static_view = self._static_view
        bridge = getattr(static_view, "bridge", None) if static_view is not None else None
        schedule = getattr(bridge, "schedule_structure_refresh", None)
        if callable(schedule):
            schedule()

    def _refresh_content_top(self) -> None:
        value = 86
        badge = getattr(self.window, "phase_badge", None)
        if isinstance(badge, QWidget):
            try:
                point = badge.mapTo(self.window, QPoint(0, badge.height()))
                value = max(72, int(point.y()) + 12)
            except RuntimeError:
                pass
        if value != self._content_top:
            self._content_top = value
            self.geometryChanged.emit()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() in {QEvent.Type.Resize, QEvent.Type.LayoutRequest, QEvent.Type.Show}:
            QTimer.singleShot(0, self._refresh_content_top)
        return False

    def _cleanup(self) -> None:
        try:
            self.window.removeEventFilter(self)
        except RuntimeError:
            pass
        item = self._qml_item
        self._qml_item = None
        if item is not None:
            try:
                item.setParentItem(None)
                item.deleteLater()
            except RuntimeError:
                pass

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
            return ToolResult(ok=True, content=f"Wrote workspace note: {relative}", data={"path": relative, "chars": len(text)})

        return AgentTool(
            name="write_workspace_note",
            description="Write a UTF-8 note inside the current Agent workspace. Requires explicit user approval.",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}, "text": {"type": "string"}},
                "required": ["path", "text"],
                "additionalProperties": False,
            },
            handler=write_note,
            effect=ToolEffect.MUTATING,
        )


def install_agent_workspace(window: QMainWindow) -> AgentWorkspaceController:
    existing = getattr(window, "_agent_workspace_controller", None)
    if isinstance(existing, AgentWorkspaceController):
        return existing
    controller = AgentWorkspaceController(window)
    static_view = getattr(window, "_static_qml_view_controller", None)
    if static_view is None:
        raise RuntimeError("Agent workspace requires the installed unified Quick scene")
    controller.attach_quick(static_view)
    window._agent_workspace_controller = controller  # type: ignore[attr-defined]
    return controller


__all__ = ["AgentWorkspaceController", "install_agent_workspace"]
