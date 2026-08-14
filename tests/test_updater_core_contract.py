"""Contract + functional tests for the standalone updater core.

The updater core is dependency-free and runs as the tiny updater.exe, so it is
tested here directly (job round-trip, checksum gating, wait-for-app-exit
timing, and the "never install while the app is alive" invariant).
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from app.updater_core import (
    RESULT_APP_DID_NOT_EXIT,
    RESULT_INSTALL_FAILED,
    RESULT_LAUNCH_FAILED,
    RESULT_OK,
    RESULT_VERIFY_FAILED,
    UpdaterJob,
    _pid_running,
    run_job,
    wait_for_app_exit,
)

ROOT = Path(__file__).resolve().parents[1]
CREATE_NO_WINDOW = 0x08000000


def test_updater_core_and_entry_compile() -> None:
    for rel in ("app/updater_core.py", "scripts/updater_main.py"):
        source = (ROOT / rel).read_text(encoding="utf-8")
        compile(source, str(ROOT / rel), "exec")


def test_pid_running_distinguishes_live_and_dead_processes() -> None:
    assert _pid_running(os.getpid())
    assert not _pid_running(-1)
    assert not _pid_running(999_999_999)


def test_job_round_trip_preserves_fields(tmp_path: Path) -> None:
    job = UpdaterJob(
        installer=r"C:\WINDOWS\TEMP\Setup.exe",
        arguments=["/SILENT"],
        installer_sha256="ab" * 32,
        app_pid=1234,
        log_path=str(tmp_path / "updater.jsonl"),
        result_path=str(tmp_path / "result.json"),
    )
    job_path = tmp_path / "job.json"
    job.save(job_path)
    loaded = UpdaterJob.load(job_path)
    assert loaded.installer == job.installer
    assert loaded.arguments == ["/SILENT"]
    assert loaded.installer_sha256 == "ab" * 32
    assert loaded.app_pid == 1234
    assert loaded.log_path == job.log_path
    assert loaded.result_path == job.result_path


def test_run_job_fails_when_installer_is_missing(tmp_path: Path) -> None:
    job = UpdaterJob(
        installer=str(tmp_path / "missing.exe"),
        result_path=str(tmp_path / "result.json"),
        log_path=str(tmp_path / "updater.jsonl"),
    )
    assert run_job(job) == 2
    result = (tmp_path / "result.json").read_text(encoding="utf-8")
    assert RESULT_LAUNCH_FAILED in result


def test_run_job_aborts_on_checksum_mismatch_before_installing(tmp_path: Path) -> None:
    installer = tmp_path / "Setup.exe"
    installer.write_bytes(b"not really an installer")
    job = UpdaterJob(
        installer=str(installer),
        installer_sha256="0" * 64,  # wrong
        result_path=str(tmp_path / "result.json"),
        log_path=str(tmp_path / "updater.jsonl"),
    )
    assert run_job(job) == 3
    assert RESULT_VERIFY_FAILED in (tmp_path / "result.json").read_text(encoding="utf-8")


def test_run_job_never_installs_while_the_app_is_still_alive(tmp_path: Path) -> None:
    marker = tmp_path / "installed.txt"
    if " " in str(marker):
        return  # Start-Process array cannot pass paths with spaces

    # Fake "app" that stays alive past the 1s deadline.
    app = subprocess.Popen(
        ["ping", "127.0.0.1", "-n", "13"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW,
    )
    try:
        installer = Path(r"C:\Windows\System32\cmd.exe")
        job = UpdaterJob(
            installer=str(installer),
            arguments=["/c", f"echo INSTALLED>{marker}"],
            app_pid=app.pid,
            app_image_name="ping",
            app_deadline_s=1,
            settle_ms=0,
            result_path=str(tmp_path / "result.json"),
            log_path=str(tmp_path / "updater.jsonl"),
        )
        assert run_job(job) == 4
        assert not marker.exists()
        assert RESULT_APP_DID_NOT_EXIT in (tmp_path / "result.json").read_text(encoding="utf-8")
    finally:
        app.kill()
        app.wait()


def test_run_job_installs_only_after_the_app_has_exited(tmp_path: Path) -> None:
    marker = tmp_path / "installed.txt"
    if " " in str(marker):
        return

    # Fake "app" that exits quickly; the job is executed afterwards.
    app = subprocess.Popen(
        ["ping", "127.0.0.1", "-n", "2"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW,
    )
    pid = app.pid
    app.wait(timeout=15)

    installer = Path(r"C:\Windows\System32\cmd.exe")
    job = UpdaterJob(
        installer=str(installer),
        arguments=["/c", f"echo INSTALLED>{marker}"],
        app_pid=pid,
        app_image_name="ping",
        worker_names=(),
        app_deadline_s=10,
        settle_ms=0,
        result_path=str(tmp_path / "result.json"),
        log_path=str(tmp_path / "updater.jsonl"),
    )
    assert run_job(job) == 0
    assert marker.exists()
    assert RESULT_OK in (tmp_path / "result.json").read_text(encoding="utf-8")


def test_wait_for_app_exit_returns_false_for_an_alive_process() -> None:
    app = subprocess.Popen(
        ["ping", "127.0.0.1", "-n", "13"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW,
    )
    try:
        assert not wait_for_app_exit(
            app_pid=app.pid,
            app_image_name="ping",
            app_deadline_s=1,
            worker_names=(),
            settle_ms=0,
        )
    finally:
        app.kill()
        app.wait()


def test_run_job_reports_nonzero_installer_exit(tmp_path: Path) -> None:
    # cmd /c exit 7 -> installer exit code 7 -> RESULT_INSTALL_FAILED.
    installer = Path(r"C:\Windows\System32\cmd.exe")
    job = UpdaterJob(
        installer=str(installer),
        arguments=["/c", "exit 7"],
        app_pid=-1,
        app_deadline_s=10,
        worker_names=(),
        settle_ms=0,
        result_path=str(tmp_path / "result.json"),
        log_path=str(tmp_path / "updater.jsonl"),
    )
    assert run_job(job) == 5
    assert RESULT_INSTALL_FAILED in (tmp_path / "result.json").read_text(encoding="utf-8")
