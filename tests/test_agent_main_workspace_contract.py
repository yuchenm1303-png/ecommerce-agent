from __future__ import annotations

from pathlib import Path


def test_main_agent_workspace_is_deferred_from_listing_startup() -> None:
    root = Path(__file__).resolve().parents[1]
    bootstrap = (root / "gui" / "__init__.py").read_text(encoding="utf-8")
    entry = (root / "run_local_gui.py").read_text(encoding="utf-8")

    assert "QTimer.singleShot(0, install_agent_workspace_extension)" in bootstrap
    assert "install_agent_workspace(window)" in bootstrap
    assert "agent_workspace" not in entry.casefold()


def test_main_agent_workspace_uses_existing_quick_window() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "gui" / "agent_workspace.py").read_text(encoding="utf-8")

    assert 'getattr(window, "_static_qml_view_controller", None)' in source
    assert "controller.attach_quick(static_view)" in source
    assert "static_view.quick.contentItem()" in source
    assert 'engine.rootContext().setContextProperty("agentWorkspace", self)' in source
    assert "QQuickWindow" in source


def test_main_agent_workspace_keeps_listing_provider_detached() -> None:
    root = Path(__file__).resolve().parents[1]
    registry = (root / "app" / "providers" / "registry.py").read_text(encoding="utf-8")
    source = (root / "gui" / "agent_workspace.py").read_text(encoding="utf-8")

    assert "AgentRuntime" not in registry
    assert "agent_runtime" not in registry
    assert "app.providers.registry" not in source
    assert "builtin_read_only_tools()" in source
    assert 'name="write_workspace_note"' in source
    assert "effect=ToolEffect.MUTATING" in source


def test_main_agent_workspace_reuses_product_visual_language() -> None:
    root = Path(__file__).resolve().parents[1]
    qml = (root / "gui" / "qml" / "AgentWorkspace.qml").read_text(encoding="utf-8")

    assert "Microsoft YaHei UI" in qml
    assert "agentWorkspace.contentTop" in qml
    assert "agentWorkspace.conversationItems" in qml
    assert "agentWorkspace.activityItems" in qml
    assert "agentWorkspace.workspaceFiles" in qml
    assert "agentWorkspace.resolveApproval(true)" in qml
    assert "agentWorkspace.cancelCurrent()" in qml
    assert "不保存模型私有思维链" in qml
    assert "duration: 300" in qml


def test_main_agent_credentials_are_lazy_and_memory_only() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "gui" / "agent_workspace.py").read_text(encoding="utf-8")

    assert "DASHSCOPE_API_KEY" in source
    assert "AI_API_KEY" in source
    assert "CredentialRef.runtime(_RUNTIME_KEY_ALIAS)" in source
    assert "CredentialResolver(runtime_lookup=" in source
    assert "self._api_key" not in source
