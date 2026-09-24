"""Pytest safety boundary for the local zero-cost Test Lab.

Loaded explicitly by ``tools/test_lab.py`` rather than globally through pytest.ini.
External sockets are rejected before bytes leave the machine. Loopback stays
available for local fixture servers and process tests, but the production Makro
and Source Edge CDP ports are explicitly forbidden so an offline regression can
never attach to the user's live browsers by accident.

On Windows Python 3.12.4+ treats ``mkdir(..., 0o700)`` as a private ACL. Some
restricted desktop-agent sandboxes then cannot reopen directories they created
with that ACL. Pytest hard-codes ``0o700`` for ``tmp_path``/``--basetemp`` and
``tempfile`` uses the same mode. Test Lab therefore normalizes only those private
mkdir calls that land inside its own per-run temp root so the directory inherits
the already-writable workspace ACL. Paths outside Test Lab keep normal Python
permission semantics.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from typing import Any

_ORIGINAL_CONNECT = socket.socket.connect
_ORIGINAL_CONNECT_EX = socket.socket.connect_ex
_ORIGINAL_CREATE_CONNECTION = socket.create_connection
_ORIGINAL_MKDIR = os.mkdir
_BLOCKED_LIVE_CDP_PORTS = {9222, 9333}
_TEST_LAB_TEMP_ROOT_ENV = "ECOMMERCE_TEST_LAB_TEMP_ROOT"
_INSTALLED = False


def _host_is_loopback(host: object) -> bool:
    if isinstance(host, bytes):
        try:
            host = host.decode("ascii")
        except UnicodeDecodeError:
            return False
    text = str(host or "").strip().strip("[]")
    if text.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(text).is_loopback
    except ValueError:
        return False


def _loopback_port_blocked(address: Any) -> bool:
    if not isinstance(address, tuple) or len(address) < 2:
        return False
    try:
        return _host_is_loopback(address[0]) and int(address[1]) in _BLOCKED_LIVE_CDP_PORTS
    except (TypeError, ValueError):
        return False


def _socket_address_allowed(sock: socket.socket, address: Any) -> bool:
    if getattr(sock, "family", None) == getattr(socket, "AF_UNIX", object()):
        return True
    if isinstance(address, tuple) and address:
        return _host_is_loopback(address[0]) and not _loopback_port_blocked(address)
    return False


def _blocked(address: Any) -> RuntimeError:
    return RuntimeError(
        "TEST_LAB_NETWORK_BLOCKED: zero-cost replay attempted a forbidden "
        f"socket connection to {address!r}"
    )


def _guarded_connect(sock: socket.socket, address: Any) -> Any:
    if not _socket_address_allowed(sock, address):
        raise _blocked(address)
    return _ORIGINAL_CONNECT(sock, address)


def _guarded_connect_ex(sock: socket.socket, address: Any) -> Any:
    if not _socket_address_allowed(sock, address):
        raise _blocked(address)
    return _ORIGINAL_CONNECT_EX(sock, address)


def _guarded_create_connection(address: Any, *args: Any, **kwargs: Any) -> socket.socket:
    host = address[0] if isinstance(address, tuple) and address else ""
    if not _host_is_loopback(host) or _loopback_port_blocked(address):
        raise _blocked(address)
    return _ORIGINAL_CREATE_CONNECTION(address, *args, **kwargs)


def _inside_test_lab_temp(path: Any) -> bool:
    root = os.environ.get(_TEST_LAB_TEMP_ROOT_ENV, "").strip()
    if not root:
        return False
    try:
        root_path = os.path.normcase(os.path.abspath(root))
        candidate = os.path.normcase(os.path.abspath(os.fsdecode(path)))
        return os.path.commonpath((root_path, candidate)) == root_path
    except (OSError, TypeError, ValueError):
        return False


def _guarded_mkdir(path: Any, mode: int = 0o777, *, dir_fd: int | None = None) -> None:
    effective_mode = mode
    if os.name == "nt" and dir_fd is None and mode == 0o700 and _inside_test_lab_temp(path):
        # Windows Python 3.12.4+ maps 0o700 to a private ACL. Under a restricted
        # desktop-agent token that ACL can lock the creator out on the next open.
        # 0o777 is ignored by Windows, so the directory inherits the writable ACL
        # from the Test Lab run root instead of creating an owner-only descriptor.
        effective_mode = 0o777
    if dir_fd is None:
        _ORIGINAL_MKDIR(path, effective_mode)
    else:
        _ORIGINAL_MKDIR(path, effective_mode, dir_fd=dir_fd)


def pytest_sessionstart(session: Any) -> None:
    global _INSTALLED
    if os.environ.get("ECOMMERCE_TEST_LAB_NO_EXTERNAL") != "1":
        raise RuntimeError(
            "app.test_lab_pytest_plugin may only run inside tools/test_lab.py "
            "with ECOMMERCE_TEST_LAB_NO_EXTERNAL=1"
        )
    if _INSTALLED:
        return
    if os.name == "nt":
        os.mkdir = _guarded_mkdir  # type: ignore[assignment]
    socket.socket.connect = _guarded_connect  # type: ignore[method-assign]
    socket.socket.connect_ex = _guarded_connect_ex  # type: ignore[method-assign]
    socket.create_connection = _guarded_create_connection
    _INSTALLED = True


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    global _INSTALLED
    if not _INSTALLED:
        return
    if os.name == "nt":
        os.mkdir = _ORIGINAL_MKDIR  # type: ignore[assignment]
    socket.socket.connect = _ORIGINAL_CONNECT  # type: ignore[method-assign]
    socket.socket.connect_ex = _ORIGINAL_CONNECT_EX  # type: ignore[method-assign]
    socket.create_connection = _ORIGINAL_CREATE_CONNECTION
    _INSTALLED = False


def pytest_report_header(config: Any) -> str:
    return (
        "Test Lab safety: external sockets blocked; live CDP 9222/9333 blocked; "
        "paid AI credentials stripped; Windows 0o700 temp ACLs normalized in Test Lab root"
    )
