from __future__ import annotations

from pathlib import Path

import pytest

import app.update_browser_gate as gate


class _Proc:
    def __init__(self, *, returncode: int = 0, stdout: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout


def test_listener_pid_reads_only_local_tcp_listener(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gate.os, "name", "nt")
    output = "\n".join(
        [
            "  TCP    10.0.0.5:9222        0.0.0.0:0      LISTENING       777",
            "  TCP    127.0.0.1:9222      0.0.0.0:0      LISTENING       888",
        ]
    )
    monkeypatch.setattr(gate.subprocess, "run", lambda *a, **k: _Proc(stdout=output))
    assert gate.listener_pid(9222) == 888


def test_managed_browser_gate_kills_only_exact_msedge_cdp_owner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(gate, "listener_pid", lambda _port: 4242)
    monkeypatch.setattr(gate, "_pid_image_name", lambda _pid: "msedge.exe")
    monkeypatch.setattr(gate, "_wait_listener_closed", lambda *_a, **_k: True)
    commands: list[list[str]] = []

    def _run(command, **_kwargs):
        commands.append(list(command))
        return _Proc(returncode=0)

    monkeypatch.setattr(gate.subprocess, "run", _run)
    phases: list[str] = []
    result = gate.close_managed_browser(
        port=9222,
        log_path=tmp_path / "updater.jsonl",
        progress=phases.append,
    )
    assert result.ok is True
    assert commands == [["taskkill", "/PID", "4242", "/T", "/F"]]
    assert phases == ["正在关闭 Makro Browser，释放更新文件…"]
    assert "browser gate closed managed Edge" in (tmp_path / "updater.jsonl").read_text(encoding="utf-8")


def test_browser_gate_fails_closed_for_unexpected_port_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gate, "listener_pid", lambda _port: 5151)
    monkeypatch.setattr(gate, "_pid_image_name", lambda _pid: "python.exe")
    commands: list[list[str]] = []
    monkeypatch.setattr(gate.subprocess, "run", lambda command, **k: commands.append(list(command)) or _Proc())
    result = gate.close_managed_browser(port=9222)
    assert result.ok is False
    assert "unexpected process" in result.detail
    assert commands == []


def test_gui_update_flow_quiesces_browser_lifecycle_before_handoff() -> None:
    presentation = Path("gui/resilient_app_updater.py").read_text(encoding="utf-8")
    manager = Path("gui/browser_session_manager.py").read_text(encoding="utf-8")
    assert "begin_update_quiesce" in presentation
    assert "wait_for_update_quiesce" in presentation
    assert "resume_after_update_failure" in presentation
    assert "self._update_quiesced = True" in manager
    assert "if self._update_quiesced:" in manager
    assert "self._poll_timer.stop()" in manager
    assert "thread.join" in manager
    close = presentation.index("close_managed_browser(")
    handoff = presentation.index("self._handoff_installer(")
    assert close < handoff
    assert "不会关闭其他普通 Edge 窗口" in presentation


def test_managed_edge_is_spawned_with_clean_external_runtime() -> None:
    source = Path("app/browser_session.py").read_text(encoding="utf-8")
    assert "fresh_external_child_environment" in source
    assert 'key.startswith("_PYI_")' in source
    assert 'env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"' in source
    assert "SetDllDirectoryW(None)" in source
    assert "env=env" in source
    assert "_spawn_external(command" in source


def test_updater_blocks_new_tasks_while_update_is_quiesced() -> None:
    manager = Path("gui/browser_session_manager.py").read_text(encoding="utf-8")
    presentation = Path("gui/resilient_app_updater.py").read_text(encoding="utf-8")
    assert "def is_busy" in manager
    assert "_assert_task_start_allowed" in manager
    assert "当前有上架任务正在运行，暂不开始更新" in presentation
    assert "manager.is_busy()" in presentation
