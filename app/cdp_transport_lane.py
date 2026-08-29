from __future__ import annotations

import os
import secrets
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .browser_session import _cdp_lock_root, _try_lock_handle, _unlock_handle


_TRANSPORT_LANE_ENV_PREFIX = "ECOMMERCE_CDP_TRANSPORT_LANE_"
_TRANSPORT_LANE_POLL_S = 0.10
_TRANSPORT_LANE_WAIT_LOG_S = 5.0


def _transport_lane_env_key(port: int) -> str:
    return f"{_TRANSPORT_LANE_ENV_PREFIX}{int(port)}"


def _transport_lane_lock_path(port: int) -> Path:
    return _cdp_lock_root() / f"transport-lane-{int(port)}.lock"


@contextmanager
def exclusive_cdp_transport_lane(port: int) -> Iterator[None]:
    """Own the only active Playwright transport lane for one local CDP port.

    This module is deliberately an ownership primitive, not a process launcher.
    Browser workers acquire the lane inside their own process before creating a
    Playwright transport. That keeps source-Python and installed frozen runtimes
    identical and prevents accidental ``sys.executable`` recursion into the GUI.

    The OS file lock spans the caller's browser-control phase and is released by
    the kernel if the worker crashes. A process-tree marker makes accidental
    nested ownership fail fast instead of deadlocking behind its own parent.
    """

    port = int(port)
    env_key = _transport_lane_env_key(port)
    inherited = str(os.environ.get(env_key) or "").strip()
    if inherited:
        raise RuntimeError(
            f"CDP transport lane {port} 已由当前进程树中的父级浏览器阶段持有；"
            "禁止嵌套启动第二个浏览器控制阶段。"
        )

    lock_path = _transport_lane_lock_path(port)
    handle = lock_path.open("a+b")
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()

    token = f"{os.getpid()}:{secrets.token_hex(16)}"
    previous_env = os.environ.get(env_key)
    next_wait_log = time.monotonic() + _TRANSPORT_LANE_WAIT_LOG_S
    acquired = False
    try:
        while not _try_lock_handle(handle):
            now = time.monotonic()
            if now >= next_wait_log:
                print(
                    f"CDP_TRANSPORT_LANE WAIT port={port} pid={os.getpid()}",
                    flush=True,
                )
                next_wait_log = now + _TRANSPORT_LANE_WAIT_LOG_S
            time.sleep(_TRANSPORT_LANE_POLL_S)

        acquired = True
        os.environ[env_key] = token
        print(
            f"CDP_TRANSPORT_LANE ACQUIRED port={port} pid={os.getpid()}",
            flush=True,
        )
        yield
    finally:
        if os.environ.get(env_key) == token:
            if previous_env is None:
                os.environ.pop(env_key, None)
            else:
                os.environ[env_key] = previous_env
        if acquired:
            _unlock_handle(handle)
            print(
                f"CDP_TRANSPORT_LANE RELEASED port={port} pid={os.getpid()}",
                flush=True,
            )
        try:
            handle.close()
        except Exception:
            pass


__all__ = [
    "exclusive_cdp_transport_lane",
    "_transport_lane_env_key",
    "_transport_lane_lock_path",
]
