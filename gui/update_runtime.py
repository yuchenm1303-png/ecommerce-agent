"""Qt-owned worker shutdown used when Listing Studio exits for Velopack."""
from __future__ import annotations

import time

from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication, QMainWindow

_TERMINATE_GRACE_S = 1.5
_KILL_GRACE_S = 1.0


def _live_owned_qprocesses(window: QMainWindow) -> list[QProcess]:
    result: list[QProcess] = []
    for process in window.findChildren(QProcess):
        try:
            if process.state() != QProcess.ProcessState.NotRunning:
                result.append(process)
        except RuntimeError:
            continue
    return result


def _wait_processes(processes: list[QProcess], deadline_s: float) -> list[QProcess]:
    deadline = time.monotonic() + max(0.0, deadline_s)
    remaining = list(processes)
    while remaining and time.monotonic() < deadline:
        next_remaining: list[QProcess] = []
        for process in remaining:
            try:
                if process.state() != QProcess.ProcessState.NotRunning:
                    process.waitForFinished(50)
                if process.state() != QProcess.ProcessState.NotRunning:
                    next_remaining.append(process)
            except RuntimeError:
                continue
        remaining = next_remaining
    return remaining


def shutdown_owned_qprocesses(window: QMainWindow) -> None:
    """Stop only child worker processes created by this GUI instance."""

    processes = _live_owned_qprocesses(window)
    for process in processes:
        try:
            process.terminate()
        except RuntimeError:
            pass
    remaining = _wait_processes(processes, _TERMINATE_GRACE_S)
    for process in remaining:
        try:
            process.kill()
        except RuntimeError:
            pass
    _wait_processes(remaining, _KILL_GRACE_S)


def install_update_runtime(app: QApplication, window: QMainWindow) -> None:
    """Register deterministic app-owned worker shutdown; Velopack owns updating."""

    if bool(getattr(window, "_update_runtime_shutdown_installed", False)):
        return

    def _shutdown() -> None:
        shutdown_owned_qprocesses(window)

    app.aboutToQuit.connect(_shutdown)
    window._update_runtime_shutdown = _shutdown  # type: ignore[attr-defined]
    window._update_runtime_shutdown_installed = True  # type: ignore[attr-defined]


__all__ = ["install_update_runtime", "shutdown_owned_qprocesses"]
