from __future__ import annotations

from pathlib import Path


def test_agent_lab_is_a_separate_development_entry() -> None:
    root = Path(__file__).resolve().parents[1]
    formal_entry = (root / "run_local_gui.py").read_text(encoding="utf-8")
    lab_entry = (root / "run_agent_lab.py").read_text(encoding="utf-8")

    assert "agent_lab" not in formal_entry.casefold()
    assert "AgentLabController" in lab_entry
    assert "QQmlApplicationEngine" in lab_entry
    assert 'gui" / "qml" / "AgentLab.qml"' in lab_entry


def test_agent_lab_uses_runtime_only_credentials_and_safe_tools() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "gui" / "agent_lab_controller.py").read_text(encoding="utf-8")

    assert "CredentialRef.runtime(_RUNTIME_KEY_ALIAS)" in source
    assert "CredentialResolver(" in source
    assert "runtime_lookup=" in source
    assert "self._api_key" not in source
    assert "builtin_read_only_tools()" in source
    assert 'name="write_workspace_note"' in source
    assert "effect=ToolEffect.MUTATING" in source
    assert "context.resolve_workspace_path" in source
    assert "context.raise_if_cancelled()" in source


def test_agent_lab_qml_exposes_harness_controls() -> None:
    root = Path(__file__).resolve().parents[1]
    qml = (root / "gui" / "qml" / "AgentLab.qml").read_text(encoding="utf-8")

    for contract in (
        "agentLab.configureProvider",
        "agentLab.sendMessage",
        "agentLab.cancelCurrent",
        "agentLab.resolveApproval(true)",
        "agentLab.resolveApproval(false)",
        "agentLab.openWorkspace",
        "Harness Timeline",
        "不保存模型私有思维链",
    ):
        assert contract in qml


def test_agent_lab_does_not_attach_agent_runtime_to_listing_provider_registry() -> None:
    root = Path(__file__).resolve().parents[1]
    registry = (root / "app" / "providers" / "registry.py").read_text(encoding="utf-8")

    assert "AgentRuntime" not in registry
    assert "agent_runtime" not in registry
    assert "AgentLabController" not in registry
