"""Update-runtime bootstrap and deterministic GUI shutdown.

The updater executable is copied to a stable directory outside the app install
tree before any update check can run. The copy is content-addressed with
SHA-256 and replaced atomically, so an updater binary can never remain stale
just because a new build happens to have the same byte length.

The same bootstrap owns shutdown of QProcess children. When Qt begins quitting,
all GUI-owned workflow workers are first asked to terminate, then killed only if
needed. The external updater remains the final safety net for non-Qt/background
threads that can otherwise keep the Python process alive after app.exec()
returns.
"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication, QMainWindow

_COPY_CHUNK = 1024 * 1024
_TERMINATE_WAIT_MS = 1_500
_KILL_WAIT_MS = 1_000


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


def _stable_updater_exe() -> Path:
    base = Path(os.getenv("LOCALAPPDATA") or tempfile.gettempdir()) / "ListingStudio"
    return base / "updater" / "updater.exe"


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


def refresh_standalone_updater() -> Path | None:
    """Install the bundled updater by content hash into its stable location."""

    bundled = _bundled_updater_exe()
    if bundled is None:
        return None

    expected = _sha256_file(bundled)
    if not expected:
        return None

    target = _stable_updater_exe()
    if target.is_file() and _sha256_file(target) == expected:
        return target

    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with bundled.open("rb") as source, tmp.open("wb") as destination:
            while True:
                block = source.read(_COPY_CHUNK)
                if not block:
                    break
                destination.write(block)
            destination.flush()
            os.fsync(destination.fileno())

        if _sha256_file(tmp) != expected:
            return None

        os.replace(tmp, target)
        return target if _sha256_file(target) == expected else None
    except OSError:
        return None
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _shutdown_owned_qprocesses(window: QMainWindow) -> None:
    """Stop only child processes owned by this GUI instance."""

    processes = [
        process
        for process in window.findChildren(QProcess)
        if process.state() != QProcess.ProcessState.NotRunning
    ]
    if not processes:
        return

    for process in processes:
        try:
            process.terminate()
        except RuntimeError:
            pass

    for process in processes:
        try:
            if process.state() != QProcess.ProcessState.NotRunning:
                process.waitForFinished(_TERMINATE_WAIT_MS)
        except RuntimeError:
            pass

    remaining = []
    for process in processes:
        try:
            if process.state() != QProcess.ProcessState.NotRunning:
                remaining.append(process)
        except RuntimeError:
            pass

    for process in remaining:
        try:
            process.kill()
        except RuntimeError:
            pass

    for process in remaining:
        try:
            if process.state() != QProcess.ProcessState.NotRunning:
                process.waitForFinished(_KILL_WAIT_MS)
        except RuntimeError:
            pass


def install_update_runtime(app: QApplication, window: QMainWindow) -> Path | None:
    """Install updater bootstrap before checks and register deterministic teardown."""

    stable_updater = refresh_standalone_updater()

    if not bool(getattr(window, "_update_runtime_shutdown_installed", False)):
        def _shutdown() -> None:
            _shutdown_owned_qprocesses(window)

        app.aboutToQuit.connect(_shutdown)
        window._update_runtime_shutdown = _shutdown  # type: ignore[attr-defined]
        window._update_runtime_shutdown_installed = True  # type: ignore[attr-defined]

    window._stable_updater_path = stable_updater  # type: ignore[attr-defined]
    return stable_updater


__all__ = [
    "install_update_runtime",
    "refresh_standalone_updater",
]
