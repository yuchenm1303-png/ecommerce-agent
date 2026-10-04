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
    output = "\n".join([
        "  TCP    10.0.0.5:9222        0.0.0.0:0      LISTENING       777",
        "  TCP    127.0.0.1:9222      0.0.0.0:0      LISTENING       888",
    ])
    monkeypatch.setattr(gate.subprocess, "run", lambda *a, **k: _Proc(stdout=output))
    assert gate.listener_pid(9222) == 888


def test_managed_browser_gate_kills_only_exact_msedge_cdp_owner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(gate, "listener_pid", lambda _port, **_k: 4242)
    monkeypatch.setattr(gate, "_pid_image_name", lambda _pid, **_k: "msedge.exe")
    monkeypatch.setattr(gate, "_wait_listener_closed", lambda *_a, **_k: True)
    commands: list[list[str]] = []

    def _run(command, **_kwargs):
        commands.append(list(command))
        return _Proc(returncode=0)

    monkeypatch.setattr(gate.subprocess, "run", _run)
    result = gate.close_managed_browser(port=9222, log_path=tmp_path / "browser-close.log")
    assert result.ok is True
    assert commands == [["taskkill", "/PID", "4242", "/T", "/F"]]
    assert "browser gate closed managed Edge" in (tmp_path / "browser-close.log").read_text(encoding="utf-8")


def test_browser_gate_fails_closed_for_unexpected_port_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gate, "listener_pid", lambda _port, **_k: 5151)
    monkeypatch.setattr(gate, "_pid_image_name", lambda _pid, **_k: "python.exe")
    commands: list[list[str]] = []
    monkeypatch.setattr(gate.subprocess, "run", lambda command, **k: commands.append(list(command)) or _Proc())
    result = gate.close_managed_browser(port=9222)
    assert result.ok is False
    assert "unexpected process" in result.detail
    assert commands == []


def test_velopack_transition_uses_bounded_headless_browser_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[dict[str, object]] = []

    def _close(**kwargs):
        calls.append(dict(kwargs))
        return gate.BrowserCloseResult(True, pid=4242)

    monkeypatch.setattr(gate, "close_managed_browser", _close)
    log_path = tmp_path / "velopack-transition.log"
    result = gate.prepare_for_velopack_transition(port=9222, log_path=log_path)
    assert result.ok is True
    assert calls == [
        {
            "port": 9222,
            "deadline_s": gate._VELOPACK_HOOK_CLOSE_DEADLINE_S,
            "command_timeout_s": gate._VELOPACK_HOOK_COMMAND_TIMEOUT_S,
            "log_path": log_path,
        }
    ]
    assert gate._VELOPACK_HOOK_CLOSE_DEADLINE_S < 15
    assert gate._VELOPACK_HOOK_COMMAND_TIMEOUT_S < 15


def test_manual_setup_and_uninstall_register_fast_cleanup_before_velopack_run() -> None:
    hook = Path("packaging/velopack_runtime_hook.py").read_text(encoding="utf-8")
    assert "prepare_for_velopack_transition" in hook
    assert "on_before_update_fast_callback(_prepare_for_transition)" in hook
    assert "on_before_uninstall_fast_callback(_prepare_for_transition)" in hook
    assert hook.index("on_before_update_fast_callback") < hook.index("_velopack_app.run()")
    assert hook.index("on_before_uninstall_fast_callback") < hook.index("_velopack_app.run()")


def test_velopack_update_quiesces_business_browser_before_framework_handoff() -> None:
    updater = Path("gui/app_updater.py").read_text(encoding="utf-8")
    manager = Path("gui/browser_session_manager.py").read_text(encoding="utf-8")
    assert "begin_update_quiesce" in updater
    assert "wait_for_update_quiesce" in updater
    assert "resume_after_update_failure" in updater
    assert "close_managed_browser" in updater
    assert "shutdown_owned_qprocesses" in updater
    assert "apply_updates_and_restart" in updater
    assert updater.index("close_managed_browser") < updater.rindex("shutdown_owned_qprocesses")
    assert updater.rindex("shutdown_owned_qprocesses") < updater.rindex("apply_updates_and_restart")
    assert "self._update_quiesced = True" in manager
    assert "self._poll_timer.stop()" in manager


def test_managed_edge_is_spawned_with_clean_external_runtime() -> None:
    source = Path("app/browser_session.py").read_text(encoding="utf-8")
    assert "fresh_external_child_environment" in source
    assert 'key.startswith("_PYI_")' in source
    assert 'env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"' in source
    assert "SetDllDirectoryW(None)" in source
    assert "env=env" in source
