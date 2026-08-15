"""Dependency-free execution core for the standalone Windows updater.

The GUI verifies the release and writes an :class:`UpdaterJob`.  The tiny
``updater.exe`` performs a second checksum verification, acknowledges the job
before the GUI exits, owns the shutdown/install boundary, verifies the installed
version, and finally relaunches Listing Studio itself.

Keeping this module free of Qt/third-party dependencies is intentional: the
updater must remain runnable while the application install tree is replaced.
"""

from __future__ import annotations

import csv
import ctypes
import hashlib
import json
import os
import re
import subprocess
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

JOB_VERSION = 2

DEFAULT_INSTALLER_ARGS = [
    "/SILENT",
    "/SUPPRESSMSGBOXES",
    "/NORESTART",
    "/CLOSEAPPLICATIONS",
    "/NORESTARTAPPLICATIONS",
]
DEFAULT_APP_DEADLINE_S = 8
DEFAULT_FORCE_CLOSE_DEADLINE_S = 5
DEFAULT_WORKER_DEADLINE_S = 5
DEFAULT_FORCE_WORKER_DEADLINE_S = 3
DEFAULT_SETTLE_MS = 1_000
OWNED_APP_IMAGE = "EcommerceAgent.exe"
OWNED_WORKER_IMAGE = "EcommerceAgentWorker.exe"

RESULT_OK = "installed"
RESULT_APP_DID_NOT_EXIT = "app_did_not_exit"
RESULT_OTHER_APP_RUNNING = "other_app_running"
RESULT_WORKER_DID_NOT_EXIT = "worker_did_not_exit"
RESULT_VERIFY_FAILED = "checksum_failed"
RESULT_INSTALL_FAILED = "install_failed"
RESULT_VERSION_MISMATCH = "installed_version_mismatch"
RESULT_MARKER_FAILED = "completion_marker_failed"
RESULT_RELAUNCH_FAILED = "relaunch_failed"
RESULT_LAUNCH_FAILED = "launch_failed"

_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[.-][0-9A-Za-z.-]+)?$")
_OPEN_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_CREATE_NO_WINDOW = 0x08000000
_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic_write_json(path: str | Path, payload: dict[str, Any]) -> bool:
    target = Path(path)
    temp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temp, target)
        return True
    except OSError:
        return False
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


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


def _normalized_image_name(image_name: str) -> str:
    value = str(image_name or "").strip().lower()
    if value and not value.endswith(".exe"):
        value += ".exe"
    return value


def _pid_running(pid: int) -> bool:
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


def _tasklist_rows(*, image_name: str = "", pid: int = 0) -> list[tuple[str, int]]:
    command = ["tasklist"]
    if pid > 0:
        command.extend(["/FI", f"PID eq {pid}"])
    elif image_name:
        expected = _normalized_image_name(image_name)
        command.extend(["/FI", f"IMAGENAME eq {expected}"])
    command.extend(["/FO", "CSV", "/NH"])
    try:
        probe = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=_CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    rows: list[tuple[str, int]] = []
    try:
        for row in csv.reader((probe.stdout or "").splitlines()):
            if len(row) < 2:
                continue
            name = str(row[0] or "").strip().lower()
            try:
                process_id = int(str(row[1]).replace(",", "").strip())
            except ValueError:
                continue
            if name and process_id > 0:
                rows.append((name, process_id))
    except csv.Error:
        return []
    return rows


def _pid_image_name(pid: int) -> str:
    for image_name, process_id in _tasklist_rows(pid=pid):
        if process_id == pid:
            return image_name
    return ""


def _image_pids(image_name: str) -> tuple[int, ...]:
    expected = _normalized_image_name(image_name)
    return tuple(
        process_id
        for current_name, process_id in _tasklist_rows(image_name=expected)
        if current_name == expected
    )


def _name_running(name: str) -> bool:
    return bool(_image_pids(name))


def _pid_matches(pid: int, image_name: str) -> bool:
    expected = _normalized_image_name(image_name)
    if not expected:
        return _pid_running(pid)
    return _pid_image_name(pid) == expected


def _wait_pid_gone(pid: int, image_name: str, deadline_s: float) -> bool:
    deadline = time.monotonic() + max(0.0, float(deadline_s))
    while time.monotonic() < deadline and _pid_matches(pid, image_name):
        time.sleep(0.2)
    return not _pid_matches(pid, image_name)


def _force_kill_pid_tree(pid: int, image_name: str, *, allowed_image: str) -> bool:
    expected = _normalized_image_name(image_name)
    if expected != _normalized_image_name(allowed_image):
        return False
    if not _pid_matches(pid, expected):
        return True
    try:
        probe = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            creationflags=_CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return probe.returncode == 0 or not _pid_matches(pid, expected)


def _wait_owned_workers(worker_pids: Sequence[int], deadline_s: float) -> bool:
    pids = tuple(int(pid) for pid in worker_pids if int(pid) > 0)
    if not pids:
        return True
    deadline = time.monotonic() + max(0.0, float(deadline_s))
    while time.monotonic() < deadline:
        if not any(_pid_matches(pid, OWNED_WORKER_IMAGE) for pid in pids):
            return True
        time.sleep(0.2)
    return not any(_pid_matches(pid, OWNED_WORKER_IMAGE) for pid in pids)


def _force_owned_workers(worker_pids: Sequence[int]) -> None:
    for pid in worker_pids:
        if _pid_matches(int(pid), OWNED_WORKER_IMAGE):
            _force_kill_pid_tree(
                int(pid),
                OWNED_WORKER_IMAGE,
                allowed_image=OWNED_WORKER_IMAGE,
            )


def _other_app_pids(app_image_name: str, owning_pid: int) -> tuple[int, ...]:
    expected = _normalized_image_name(app_image_name)
    if expected != _normalized_image_name(OWNED_APP_IMAGE):
        return ()
    return tuple(pid for pid in _image_pids(expected) if pid != int(owning_pid))


def _write_result(job: "UpdaterJob", status: str, detail: str) -> None:
    if not job.result_path:
        return
    _atomic_write_json(
        job.result_path,
        {
            "status": status,
            "detail": detail,
            "target_version": job.target_version,
            "finished_at": _now(),
        },
    )


def _clear_marker(marker_path: str | Path | None) -> bool:
    if not marker_path:
        return True
    path = Path(marker_path)
    try:
        path.unlink(missing_ok=True)
        return not path.exists()
    except OSError:
        return False


def _write_completion_marker(job: "UpdaterJob") -> bool:
    if not job.marker_path:
        return True
    return _atomic_write_json(
        job.marker_path,
        {
            "version": job.target_version,
            "completed_at": _now(),
        },
    )


def _launch_app(executable: str) -> bool:
    path = Path(executable)
    if not path.is_file():
        return False
    try:
        subprocess.Popen(
            [str(path)],
            creationflags=_DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        return True
    except OSError:
        return False


def _read_installed_version(path: str | Path) -> str:
    try:
        return Path(path).read_text(encoding="utf-8").strip().lstrip("v")
    except OSError:
        return ""


@dataclass
class UpdaterJob:
    installer: str
    target_version: str
    app_pid: int
    app_image_name: str
    app_executable: str
    version_file: str
    installer_sha256: str
    arguments: list[str] = field(default_factory=list)
    worker_pids: tuple[int, ...] = ()
    app_deadline_s: int = DEFAULT_APP_DEADLINE_S
    worker_deadline_s: int = DEFAULT_WORKER_DEADLINE_S
    settle_ms: int = DEFAULT_SETTLE_MS
    ack_path: str = ""
    marker_path: str = ""
    log_path: str = ""
    result_path: str = ""

    @classmethod
    def load(cls, path: str | Path) -> "UpdaterJob":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("update job must be a JSON object")
        if int(payload.get("job_version") or 0) != JOB_VERSION:
            raise ValueError("unsupported update job version")
        known = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        values = {key: value for key, value in payload.items() if key in known}
        values["arguments"] = [str(item) for item in values.get("arguments", [])]
        values["worker_pids"] = tuple(int(item) for item in values.get("worker_pids", []))
        return cls(**values)

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["job_version"] = JOB_VERSION
        return payload

    def save(self, path: str | Path) -> None:
        if not _atomic_write_json(path, self.as_dict()):
            raise OSError(f"failed to write updater job: {path}")


def _preflight_job(job: UpdaterJob) -> tuple[str, str] | None:
    installer = Path(job.installer)
    if not installer.is_file():
        return RESULT_LAUNCH_FAILED, "installer file missing"
    if not _VERSION_RE.fullmatch(str(job.target_version or "").strip()):
        return RESULT_LAUNCH_FAILED, "target version is invalid"
    if not _SHA256_RE.fullmatch(str(job.installer_sha256 or "").strip()):
        return RESULT_VERIFY_FAILED, "installer checksum is missing or invalid"
    if not Path(job.app_executable).is_file():
        return RESULT_LAUNCH_FAILED, "application executable is missing"
    if not Path(job.version_file).is_file():
        return RESULT_LAUNCH_FAILED, "installed version file is missing"

    digest = _sha256_file(installer)
    if digest.lower() != str(job.installer_sha256).lower():
        return RESULT_VERIFY_FAILED, "installer checksum mismatch"
    return None


def _acknowledge_job(job: UpdaterJob) -> bool:
    if not job.ack_path:
        return False
    return _atomic_write_json(
        job.ack_path,
        {
            "status": "accepted",
            "job_version": JOB_VERSION,
            "target_version": job.target_version,
            "updater_pid": os.getpid(),
            "accepted_at": _now(),
        },
    )


def _shutdown_gate(job: UpdaterJob) -> str | None:
    if not _wait_pid_gone(job.app_pid, job.app_image_name, job.app_deadline_s):
        _log(
            job.log_path,
            f"app still alive after {job.app_deadline_s}s; forcing owned process tree",
        )
        if not _force_kill_pid_tree(
            job.app_pid,
            job.app_image_name,
            allowed_image=OWNED_APP_IMAGE,
        ):
            return RESULT_APP_DID_NOT_EXIT
        if not _wait_pid_gone(
            job.app_pid,
            job.app_image_name,
            DEFAULT_FORCE_CLOSE_DEADLINE_S,
        ):
            return RESULT_APP_DID_NOT_EXIT

    if _other_app_pids(job.app_image_name, job.app_pid):
        return RESULT_OTHER_APP_RUNNING

    if not _wait_owned_workers(job.worker_pids, job.worker_deadline_s):
        _log(job.log_path, "owned workflow worker remained alive; forcing worker tree")
        _force_owned_workers(job.worker_pids)
        if not _wait_owned_workers(job.worker_pids, DEFAULT_FORCE_WORKER_DEADLINE_S):
            return RESULT_WORKER_DID_NOT_EXIT

    time.sleep(max(0.0, float(job.settle_ms)) / 1000.0)
    return None


def wait_for_app_exit(
    *,
    app_pid: int,
    app_image_name: str = "",
    app_deadline_s: int = DEFAULT_APP_DEADLINE_S,
    worker_pids: Sequence[int] = (),
    worker_deadline_s: int = DEFAULT_WORKER_DEADLINE_S,
    settle_ms: int = DEFAULT_SETTLE_MS,
) -> bool:
    """Graceful-only compatibility helper used by focused tests."""

    if not _wait_pid_gone(app_pid, app_image_name, app_deadline_s):
        return False
    if not _wait_owned_workers(worker_pids, worker_deadline_s):
        return False
    time.sleep(max(0.0, float(settle_ms)) / 1000.0)
    return True


def _recover_after_failure(job: UpdaterJob, status: str, detail: str) -> int:
    _clear_marker(job.marker_path)
    _write_result(job, status, detail)
    _log(job.log_path, f"update failed status={status} detail={detail}")

    app_still_running = _pid_matches(job.app_pid, job.app_image_name)
    another_app_running = bool(_other_app_pids(job.app_image_name, job.app_pid))
    workers_still_running = any(
        _pid_matches(pid, OWNED_WORKER_IMAGE) for pid in job.worker_pids
    )
    if not app_still_running and not another_app_running and not workers_still_running:
        _launch_app(job.app_executable)

    return {
        RESULT_APP_DID_NOT_EXIT: 4,
        RESULT_INSTALL_FAILED: 5,
        RESULT_WORKER_DID_NOT_EXIT: 6,
        RESULT_OTHER_APP_RUNNING: 7,
        RESULT_VERSION_MISMATCH: 8,
    }.get(status, 2)


def run_job(job: UpdaterJob) -> int:
    """Execute one update job after a two-sided handoff acknowledgement."""

    _log(
        job.log_path,
        f"updater job start app_pid={job.app_pid} target={job.target_version} installer={job.installer}",
    )

    preflight = _preflight_job(job)
    if preflight is not None:
        status, detail = preflight
        _write_result(job, status, detail)
        _log(job.log_path, f"preflight failed status={status} detail={detail}")
        return 3 if status == RESULT_VERIFY_FAILED else 2

    if not _acknowledge_job(job):
        _write_result(job, RESULT_LAUNCH_FAILED, "failed to acknowledge updater handoff")
        return 2

    gate_failure = _shutdown_gate(job)
    if gate_failure is not None:
        detail = {
            RESULT_APP_DID_NOT_EXIT: "application process stayed alive",
            RESULT_OTHER_APP_RUNNING: "another Listing Studio instance is still running",
            RESULT_WORKER_DID_NOT_EXIT: "owned workflow worker stayed alive",
        }.get(gate_failure, "shutdown gate failed")
        return _recover_after_failure(job, gate_failure, detail)

    # The installer keeps a legacy marker-based auto-relaunch path for older
    # clients.  New updater jobs deliberately remove the marker while Inno runs
    # so only this updater owns relaunch; the marker is recreated after version
    # verification succeeds.
    if not _clear_marker(job.marker_path):
        return _recover_after_failure(
            job,
            RESULT_INSTALL_FAILED,
            "could not suspend completion marker before installation",
        )

    installer = Path(job.installer)
    arguments = list(job.arguments) or list(DEFAULT_INSTALLER_ARGS)
    _log(job.log_path, f"running installer: {installer} {' '.join(arguments)}")
    try:
        proc = subprocess.run(
            [str(installer), *arguments],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=600,
            creationflags=_CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return _recover_after_failure(
            job,
            RESULT_INSTALL_FAILED,
            f"installer launch failed: {exc}",
        )

    _log(job.log_path, f"installer exit code {proc.returncode}")
    if proc.returncode != 0:
        return _recover_after_failure(
            job,
            RESULT_INSTALL_FAILED,
            f"installer exit code {proc.returncode}",
        )

    actual_version = _read_installed_version(job.version_file)
    expected_version = str(job.target_version).strip().lstrip("v")
    if actual_version != expected_version:
        return _recover_after_failure(
            job,
            RESULT_VERSION_MISMATCH,
            f"installed version mismatch expected={expected_version} actual={actual_version or 'missing'}",
        )

    try:
        installer.unlink(missing_ok=True)
    except OSError:
        pass

    if not _write_completion_marker(job):
        _write_result(
            job,
            RESULT_MARKER_FAILED,
            "update installed but completion marker could not be written",
        )
        _log(job.log_path, "installed version verified but completion marker write failed")
        if _launch_app(job.app_executable):
            return 10
        _write_result(
            job,
            RESULT_RELAUNCH_FAILED,
            "update installed but Listing Studio could not be relaunched",
        )
        return 9

    _write_result(job, RESULT_OK, "installed version verified and relaunch requested")
    if not _launch_app(job.app_executable):
        _write_result(
            job,
            RESULT_RELAUNCH_FAILED,
            "update installed but Listing Studio could not be relaunched",
        )
        _log(job.log_path, "new application relaunch failed")
        return 9

    _log(job.log_path, "update complete; new application relaunched")
    return 0


__all__ = [
    "DEFAULT_APP_DEADLINE_S",
    "DEFAULT_FORCE_CLOSE_DEADLINE_S",
    "DEFAULT_FORCE_WORKER_DEADLINE_S",
    "DEFAULT_INSTALLER_ARGS",
    "DEFAULT_SETTLE_MS",
    "DEFAULT_WORKER_DEADLINE_S",
    "JOB_VERSION",
    "OWNED_APP_IMAGE",
    "OWNED_WORKER_IMAGE",
    "RESULT_APP_DID_NOT_EXIT",
    "RESULT_INSTALL_FAILED",
    "RESULT_LAUNCH_FAILED",
    "RESULT_MARKER_FAILED",
    "RESULT_OK",
    "RESULT_OTHER_APP_RUNNING",
    "RESULT_RELAUNCH_FAILED",
    "RESULT_VERIFY_FAILED",
    "RESULT_VERSION_MISMATCH",
    "RESULT_WORKER_DID_NOT_EXIT",
    "UpdaterJob",
    "_name_running",
    "_pid_running",
    "_sha256_file",
    "run_job",
    "wait_for_app_exit",
]
