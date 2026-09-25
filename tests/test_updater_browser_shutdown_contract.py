from __future__ import annotations

import http.server
import json
import socket
import threading
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


def _spy_run(commands: list[list[str]]):
    def _run(command, **_kwargs):
        commands.append(list(command))
        return _Proc(returncode=0)

    return _run


def test_managed_browser_gate_closes_exact_msedge_cdp_owner_gracefully(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(gate, "listener_pid", lambda _port: 4242)
    monkeypatch.setattr(gate, "_pid_image_name", lambda _pid: "msedge.exe")
    graceful_ports: list[int] = []
    monkeypatch.setattr(
        gate,
        "_request_graceful_exit",
        lambda port, _log=None: graceful_ports.append(port) or True,
    )
    monkeypatch.setattr(gate, "_wait_listener_closed", lambda *_a, **_k: True)
    commands: list[list[str]] = []
    monkeypatch.setattr(gate.subprocess, "run", _spy_run(commands))
    log_path = tmp_path / "browser-close.log"

    result = gate.close_managed_browser(port=9222, log_path=log_path)

    assert result.ok is True
    assert graceful_ports == [9222]
    # Graceful shutdown only: no taskkill at all, so Chromium persists session cookies.
    assert commands == []
    text = log_path.read_text(encoding="utf-8")
    assert "browser gate closed managed Edge pid=4242 gracefully" in text


def test_managed_browser_gate_forces_shutdown_after_graceful_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(gate, "listener_pid", lambda _port: 4242)
    monkeypatch.setattr(gate, "_pid_image_name", lambda _pid: "msedge.exe")
    monkeypatch.setattr(gate, "_request_graceful_exit", lambda *_a, **_k: True)
    # Graceful wait expires first, then the forced kill is confirmed closed.
    waits = [False, True]
    monkeypatch.setattr(gate, "_wait_listener_closed", lambda *_a, **_k: waits.pop(0))
    commands: list[list[str]] = []
    monkeypatch.setattr(gate.subprocess, "run", _spy_run(commands))
    log_path = tmp_path / "browser-close.log"

    result = gate.close_managed_browser(port=9222, log_path=log_path)

    assert result.ok is True
    assert commands == [["taskkill", "/PID", "4242", "/T", "/F"]]
    assert "browser gate forcing managed Edge pid=4242" in log_path.read_text(encoding="utf-8")


def test_managed_browser_gate_skips_graceful_wait_when_cdp_is_unreachable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(gate, "listener_pid", lambda _port: 4242)
    monkeypatch.setattr(gate, "_pid_image_name", lambda _pid: "msedge.exe")
    monkeypatch.setattr(gate, "_request_graceful_exit", lambda *_a, **_k: False)
    waited: list[float] = []
    monkeypatch.setattr(
        gate,
        "_wait_listener_closed",
        lambda _port, budget: waited.append(budget) or True,
    )
    commands: list[list[str]] = []
    monkeypatch.setattr(gate.subprocess, "run", _spy_run(commands))

    result = gate.close_managed_browser(
        port=9222,
        deadline_s=6.0,
        graceful_deadline_s=8.0,
        log_path=tmp_path / "browser-close.log",
    )

    assert result.ok is True
    # Only the post-kill confirmation wait ran; the 8s graceful budget was skipped.
    assert waited == [6.0]
    assert commands == [["taskkill", "/PID", "4242", "/T", "/F"]]


def test_cdp_browser_close_sends_a_masked_close_frame_on_the_wire() -> None:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    received: dict[str, bytes] = {}

    def _serve() -> None:
        connection, _ = server.accept()
        with connection:
            handshake = b""
            while b"\r\n\r\n" not in handshake:
                chunk = connection.recv(4096)
                if not chunk:
                    break
                handshake += chunk
            received["handshake"] = handshake
            connection.sendall(
                b"HTTP/1.1 101 Switching Protocols\r\n"
                b"Upgrade: websocket\r\n"
                b"Connection: Upgrade\r\n\r\n"
            )
            received["frame"] = connection.recv(4096)
        server.close()

    worker = threading.Thread(target=_serve, daemon=True)
    worker.start()
    try:
        assert gate._send_cdp_browser_close(f"ws://127.0.0.1:{port}/devtools/browser/abc") is True
        worker.join(timeout=5)
        assert not worker.is_alive()
    finally:
        server.close()

    handshake = received["handshake"].decode("ascii")
    assert handshake.startswith("GET /devtools/browser/abc HTTP/1.1\r\n")
    assert f"Host: 127.0.0.1:{port}\r\n" in handshake
    assert "Upgrade: websocket\r\n" in handshake
    assert "Sec-WebSocket-Version: 13\r\n" in handshake

    frame = received["frame"]
    assert frame[0] == 0x81  # FIN + text opcode
    assert frame[1] & 0x80  # client frames must be masked
    length = frame[1] & 0x7F
    assert length < 126
    mask = frame[2 : 2 + 4]
    payload = frame[2 + 4 : 2 + 4 + length]
    unmasked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    assert json.loads(unmasked.decode("utf-8")) == {"id": 1, "method": "Browser.close"}


def test_cdp_browser_close_refuses_a_non_upgrade_answer() -> None:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]

    def _serve() -> None:
        connection, _ = server.accept()
        with connection:
            connection.recv(4096)
            connection.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
        server.close()

    worker = threading.Thread(target=_serve, daemon=True)
    worker.start()
    try:
        assert gate._send_cdp_browser_close(f"ws://127.0.0.1:{port}/devtools/browser/abc") is False
        worker.join(timeout=5)
    finally:
        server.close()


def test_browser_websocket_url_reads_the_cdp_version_endpoint() -> None:
    class _VersionHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
            if self.path != "/json/version":
                self.send_error(404)
                return
            body = json.dumps(
                {"webSocketDebuggerUrl": "ws://127.0.0.1:9/devtools/browser/fake"}
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args) -> None:
            return

    server = http.server.HTTPServer(("127.0.0.1", 0), _VersionHandler)
    port = server.server_address[1]
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        assert (
            gate._browser_websocket_url(port)
            == "ws://127.0.0.1:9/devtools/browser/fake"
        )
    finally:
        server.shutdown()
        server.server_close()

    assert gate._browser_websocket_url(port) == ""
    assert gate._browser_websocket_url(0) == ""


def test_browser_gate_fails_closed_for_unexpected_port_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gate, "listener_pid", lambda _port: 5151)
    monkeypatch.setattr(gate, "_pid_image_name", lambda _pid: "python.exe")
    commands: list[list[str]] = []
    monkeypatch.setattr(gate.subprocess, "run", lambda command, **k: commands.append(list(command)) or _Proc())
    result = gate.close_managed_browser(port=9222)
    assert result.ok is False
    assert "unexpected process" in result.detail
    assert commands == []


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
