"""Standalone update execution core (dependency-free).

This module is imported by two sides:

- The GUI (``gui.app_updater``) builds an :class:`UpdaterJob` describing the
  verified installer and hands it to the standalone updater executable.
- The standalone ``updater.exe`` (a single-file PyInstaller build from
  ``scripts/updater_main.py``) loads that job and executes it.

The core deliberately avoids Qt and any third-party packages so ``updater.exe``
stays tiny and can never be broken by the app it is meant to replace. It runs
from a stable location outside the install directory, so every app update can
overwrite the app while the updater keeps working — the bootstrap trap of an
updater living inside the app it updates is gone.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import subprocess
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

JOB_VERSION = 1

DEFAULT_INSTALLER_ARGS = [
    "/SILENT",
    "/SUPPRESSMSGBOXES",
    "/NORESTART",
    "/CLOSEAPPLICATIONS",
    "/NORESTARTAPPLICATIONS",
]
DEFAULT_APP_DEADLINE_S = 30
DEFAULT_WORKER_DEADLINE_S = 15
DEFAULT_SETTLE_MS = 1_500
DEFAULT_WORKER_NAMES = ("EcommerceAgentWorker",)

RESULT_OK = "installed"
RESULT_APP_DID_NOT_EXIT = "app_did_not_exit"
RESULT_VERIFY_FAILED = "checksum_failed"
RESULT_INSTALL_FAILED = "install_failed"
RESULT_LAUNCH_FAILED = "launch_failed"

_OPEN_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_CREATE_NO_WINDOW = 0x08000000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _log(log_path: str | Path | None, message: str) -> None:
    if not log_path:
        return
    path = Path(log_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{_now()}\t{message}\n")
    except OSError:
        pass


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while True:
                block = stream.read(1024 * 1024)
                if not block:
                    break
                digest.update(block)
    except OSError:
        return ""
    return digest.hexdigest()


def _pid_running(pid: int) -> bool:
    """Best-effort process-existence check without psutil (works on Windows)."""

    if pid <= 0:
        return False
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(_OPEN_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        kernel32.CloseHandle(handle)
        return True
    except (AttributeError, OSError):
        return False


def _name_running(name: str) -> bool:
    """Whether any process with the given image name (no .exe) is running."""

    if not name:
        return False
    try:
        probe = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {name}.exe", "/NH"],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=_CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return name.lower() in (probe.stdout or "").lower()


def _pid_image_name(pid: int) -> str:
    """Resolve the image name of the process owning ``pid`` ("" if gone)."""

    try:
        probe = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=_CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    for line in (probe.stdout or "").splitlines():
        if f'"{pid}"' in line:
            try:
                return line.split('","')[0].strip('"').lower()
            except (IndexError, ValueError):
                return ""
    return ""


def _pid_matches(pid: int, image_name: str) -> bool:
    """Whether ``pid`` still belongs to the owning app.

    A bare PID existence check is not enough: Windows can reuse a PID right
    after a process exits, which would make a live process appear to be our
    app. When an image name is known, require it to match as well.
    """

    if not image_name:
        return _pid_running(pid)
    expected = image_name.lower()
    if not expected.endswith(".exe"):
        expected += ".exe"
    return _pid_image_name(pid) == expected


def wait_for_app_exit(
    *,
    app_pid: int,
    app_image_name: str = "",
    app_deadline_s: int = DEFAULT_APP_DEADLINE_S,
    worker_names: Sequence[str] = DEFAULT_WORKER_NAMES,
    worker_deadline_s: int = DEFAULT_WORKER_DEADLINE_S,
    settle_ms: int = DEFAULT_SETTLE_MS,
) -> bool:
    """Wait for the owning app process (and its workflow workers) to exit.

    Returns True only when the app actually exited within the deadline. False
    means the app stayed alive, in which case no installer should run — the
    upgrade would hit Inno Setup's silent RestartManager abort again and roll
    back. ``app_image_name`` guards against PID reuse: the wait only counts a
    PID as "still our app" while its image name matches. After the app exits,
    workers get a bounded grace window and a short settle allows file handles
    to release before the installer starts.
    """

    deadline = time.monotonic() + float(app_deadline_s)
    while time.monotonic() < deadline and _pid_matches(app_pid, app_image_name):
        time.sleep(0.25)
    if _pid_matches(app_pid, app_image_name):
        return False

    worker_deadline = time.monotonic() + float(worker_deadline_s)
    while time.monotonic() < worker_deadline:
        if not any(_name_running(name) for name in worker_names):
            break
        time.sleep(0.25)

    time.sleep(max(0.0, settle_ms) / 1000.0)
    return True


def _write_result(result_path: str | Path | None, status: str, detail: str) -> None:
    if not result_path:
        return
    try:
        path = Path(result_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"status": status, "detail": detail, "finished_at": _now()},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass


@dataclass
class UpdaterJob:
    """One update-install job executed by the standalone updater."""

    installer: str
    arguments: list[str] = field(default_factory=list)
    installer_sha256: str = ""
    app_pid: int = 0
    app_image_name: str = ""
    app_deadline_s: int = DEFAULT_APP_DEADLINE_S
    worker_names: tuple[str, ...] = DEFAULT_WORKER_NAMES
    worker_deadline_s: int = DEFAULT_WORKER_DEADLINE_S
    settle_ms: int = DEFAULT_SETTLE_MS
    log_path: str = ""
    result_path: str = ""

    @classmethod
    def load(cls, path: str | Path) -> "UpdaterJob":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("update job must be a JSON object")
        known = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        return cls(**{key: value for key, value in payload.items() if key in known})

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.as_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["version"] = JOB_VERSION
        return payload


def run_job(job: UpdaterJob) -> int:
    """Execute one verified update job; returns a process exit code."""

    _log(job.log_path, f"updater job start pid={job.app_pid} installer={job.installer}")

    installer = Path(job.installer)
    if not installer.is_file():
        _log(job.log_path, "installer missing")
        _write_result(job.result_path, RESULT_LAUNCH_FAILED, "installer file missing")
        return 2

    if job.installer_sha256:
        digest = _sha256_file(installer)
        if digest.lower() != str(job.installer_sha256).lower():
            _log(
                job.log_path,
                f"checksum mismatch got={digest[:12]} expected={str(job.installer_sha256)[:12]}",
            )
            _write_result(job.result_path, RESULT_VERIFY_FAILED, "installer checksum mismatch")
            return 3

    if not wait_for_app_exit(
        app_pid=job.app_pid,
        app_image_name=job.app_image_name,
        app_deadline_s=job.app_deadline_s,
        worker_names=job.worker_names,
        worker_deadline_s=job.worker_deadline_s,
        settle_ms=job.settle_ms,
    ):
        _log(job.log_path, "app did not exit within deadline; not installing")
        _write_result(job.result_path, RESULT_APP_DID_NOT_EXIT, "app process stayed alive")
        return 4

    arguments = list(job.arguments) or list(DEFAULT_INSTALLER_ARGS)
    _log(job.log_path, f"running installer: {str(installer)} {' '.join(arguments)}")
    try:
        proc = subprocess.run(
            [str(installer), *arguments],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=600,
            creationflags=_CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _log(job.log_path, f"installer launch failed: {exc}")
        _write_result(job.result_path, RESULT_INSTALL_FAILED, f"installer launch failed: {exc}")
        return 5

    _log(job.log_path, f"installer exit code {proc.returncode}")
    if proc.returncode != 0:
        _write_result(
            job.result_path,
            RESULT_INSTALL_FAILED,
            f"installer exit code {proc.returncode}",
        )
        return 5

    _write_result(job.result_path, RESULT_OK, "installed with exit code 0")
    return 0


__all__ = [
    "DEFAULT_INSTALLER_ARGS",
    "DEFAULT_APP_DEADLINE_S",
    "DEFAULT_WORKER_DEADLINE_S",
    "DEFAULT_SETTLE_MS",
    "DEFAULT_WORKER_NAMES",
    "JOB_VERSION",
    "RESULT_OK",
    "RESULT_APP_DID_NOT_EXIT",
    "RESULT_VERIFY_FAILED",
    "RESULT_INSTALL_FAILED",
    "RESULT_LAUNCH_FAILED",
    "UpdaterJob",
    "run_job",
    "wait_for_app_exit",
    "_pid_running",
    "_name_running",
]
