"""Updater bootstrap paths and deterministic GUI-owned process shutdown."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication, QMainWindow

_COPY_CHUNK = 1024 * 1024
_TERMINATE_GRACE_S = 1.5
_KILL_GRACE_S = 1.0
_CREATE_NO_WINDOW = 0x08000000
_PYINSTALLER_RESET_ENV = "PYINSTALLER_RESET_ENVIRONMENT"


def update_state_dir() -> Path:
    base = Path(os.getenv("LOCALAPPDATA") or tempfile.gettempdir()) / "ListingStudio"
    base.mkdir(parents=True, exist_ok=True)
    return base


def stable_updater_dir() -> Path:
    path = update_state_dir() / "updater"
    path.mkdir(parents=True, exist_ok=True)
    return path


def stable_updater_exe() -> Path:
    return stable_updater_dir() / "updater.exe"


def update_download_dir() -> Path:
    path = update_state_dir() / "updates"
    path.mkdir(parents=True, exist_ok=True)
    return path


def update_marker_path() -> Path:
    return update_state_dir() / "update-complete.json"


def updater_result_path() -> Path:
    return stable_updater_dir() / "last-result.json"


def updater_log_path() -> Path:
    return stable_updater_dir() / "updater.jsonl"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while True:
                block = stream.read(_COPY_CHUNK)
                if not block:
                    break
                digest.update(block)
    except OSError:
        return ""
    return digest.hexdigest()


def _bundled_updater_exe() -> Path | None:
    if not bool(getattr(sys, "frozen", False)):
        return None

    candidates: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "updater" / "updater.exe")

    parent = Path(sys.executable).resolve().parent
    candidates.append(parent / "updater" / "updater.exe")
    candidates.append(parent / "_internal" / "updater" / "updater.exe")

    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _fresh_pyinstaller_child_environment() -> dict[str, str]:
    """Return an environment that forces an independent PyInstaller bootloader."""

    env = os.environ.copy()
    env[_PYINSTALLER_RESET_ENV] = "1"
    return env


def refresh_standalone_updater() -> Path | None:
    """Copy the packaged updater outside the install tree using SHA-256 identity."""

    bundled = _bundled_updater_exe()
    if bundled is None:
        return None

    expected = _sha256_file(bundled)
    if not expected:
        return None

    target = stable_updater_exe()
    if target.is_file() and _sha256_file(target) == expected:
        return target

    temp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        with bundled.open("rb") as source, temp.open("wb") as destination:
            while True:
                block = source.read(_COPY_CHUNK)
                if not block:
                    break
                destination.write(block)
            destination.flush()
            os.fsync(destination.fileno())

        if _sha256_file(temp) != expected:
            return None
        os.replace(temp, target)
        if _sha256_file(target) != expected:
            return None
        return target
    except OSError:
        return None
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def verify_standalone_updater(path: Path) -> bool:
    """Run the real updater binary and import its embedded core before handoff."""

    if not path.is_file():
        return False
    try:
        probe = subprocess.run(
            [str(path), "--self-check"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            creationflags=(
                getattr(subprocess, "CREATE_NO_WINDOW", _CREATE_NO_WINDOW)
            ),
            env=_fresh_pyinstaller_child_environment(),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return probe.returncode == 0


def prepare_standalone_updater() -> Path | None:
    updater = refresh_standalone_updater()
    if updater is None or not verify_standalone_updater(updater):
        return None

    # app_updater launches this executable immediately after preparation.  Arm
    # the child bootloader reset before that direct Popen so the updater never
    # reuses the GUI's _PYI runtime state. The GUI exits after successful ACK.
    os.environ[_PYINSTALLER_RESET_ENV] = "1"
    return updater


def owned_qprocess_pids(window: QMainWindow) -> tuple[int, ...]:
    pids: list[int] = []
    for process in window.findChildren(QProcess):
        try:
            if process.state() == QProcess.ProcessState.NotRunning:
                continue
            pid = int(process.processId())
        except (RuntimeError, TypeError, ValueError):
            continue
        if pid > 0:
            pids.append(pid)
    return tuple(sorted(set(pids)))


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
    """Stop only QProcess children belonging to this GUI instance."""

    processes = _live_owned_qprocesses(window)
    if not processes:
        return

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


def install_update_runtime(app: QApplication, window: QMainWindow) -> Path | None:
    """Refresh updater early and register one deterministic process teardown hook."""

    stable = refresh_standalone_updater()
    if not bool(getattr(window, "_update_runtime_shutdown_installed", False)):

        def _shutdown() -> None:
            shutdown_owned_qprocesses(window)

        app.aboutToQuit.connect(_shutdown)
        window._update_runtime_shutdown = _shutdown  # type: ignore[attr-defined]
        window._update_runtime_shutdown_installed = True  # type: ignore[attr-defined]

    window._stable_updater_path = stable  # type: ignore[attr-defined]
    return stable


__all__ = [
    "install_update_runtime",
    "owned_qprocess_pids",
    "prepare_standalone_updater",
    "refresh_standalone_updater",
    "shutdown_owned_qprocesses",
    "stable_updater_dir",
    "stable_updater_exe",
    "update_download_dir",
    "update_marker_path",
    "update_state_dir",
    "updater_log_path",
    "updater_result_path",
    "verify_standalone_updater",
]
