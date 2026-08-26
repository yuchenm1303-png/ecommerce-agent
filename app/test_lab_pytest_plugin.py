"""Pytest safety boundary for the local zero-cost Test Lab.

The plugin is loaded explicitly by ``tools/test_lab.py``.  It is intentionally
not registered in ``pytest.ini`` so ordinary developer pytest runs keep their
existing behaviour.  Test Lab runs may use loopback sockets (Playwright/CDP
fakes and local subprocess tests) but any attempt to open an external socket is
failed before bytes leave the machine.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from typing import Any

_ORIGINAL_CONNECT = socket.socket.connect
_ORIGINAL_CONNECT_EX = socket.socket.connect_ex
_ORIGINAL_CREATE_CONNECTION = socket.create_connection
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


def _socket_address_allowed(sock: socket.socket, address: Any) -> bool:
    if getattr(sock, "family", None) == getattr(socket, "AF_UNIX", object()):
        return True
    if isinstance(address, tuple) and address:
        return _host_is_loopback(address[0])
    return False


def _blocked(address: Any) -> RuntimeError:
    return RuntimeError(
        "TEST_LAB_EXTERNAL_NETWORK_BLOCKED: zero-cost replay attempted an "
        f"external socket connection to {address!r}"
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
    if not _host_is_loopback(host):
        raise _blocked(address)
    return _ORIGINAL_CREATE_CONNECTION(address, *args, **kwargs)


def pytest_sessionstart(session: Any) -> None:
    global _INSTALLED
    if os.environ.get("ECOMMERCE_TEST_LAB_NO_EXTERNAL") != "1":
        raise RuntimeError(
            "app.test_lab_pytest_plugin may only run inside tools/test_lab.py "
            "with ECOMMERCE_TEST_LAB_NO_EXTERNAL=1"
        )
    if _INSTALLED:
        return
    socket.socket.connect = _guarded_connect  # type: ignore[method-assign]
    socket.socket.connect_ex = _guarded_connect_ex  # type: ignore[method-assign]
    socket.create_connection = _guarded_create_connection
    _INSTALLED = True


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    global _INSTALLED
    if not _INSTALLED:
        return
    socket.socket.connect = _ORIGINAL_CONNECT  # type: ignore[method-assign]
    socket.socket.connect_ex = _ORIGINAL_CONNECT_EX  # type: ignore[method-assign]
    socket.create_connection = _ORIGINAL_CREATE_CONNECTION
    _INSTALLED = False


def pytest_report_header(config: Any) -> str:
    return "Test Lab safety: external sockets blocked; loopback only; paid AI credentials stripped"
