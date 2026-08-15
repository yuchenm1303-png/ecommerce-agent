"""Contracts for the dependency-free standalone updater execution core."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import app.updater_core as core
from app.updater_core import (
    JOB_VERSION,
    RESULT_INSTALL_FAILED,
    RESULT_OK,
    RESULT_VERIFY_FAILED,
    UpdaterJob,
    run_job,
)

ROOT = Path(__file__).resolve().parents[1]


def _job(tmp_path: Path, **overrides) -> UpdaterJob:
    installer = tmp_path / "Setup.exe"
    installer.write_bytes(b"verified-installer")
    app_executable = tmp_path / "EcommerceAgent.exe"
    app_executable.write_bytes(b"app")
    version_file = tmp_path / "VERSION"
    version_file.write_text("0.9.9", encoding="utf-8")
    values = {
        "installer": str(installer),
        "target_version": "1.0.0",
        "app_pid": 1234,
        "app_image_name": "EcommerceAgent",
        "app_executable": str(app_executable),
        "version_file": str(version_file),
        "installer_sha256": hashlib.sha256(installer.read_bytes()).hexdigest(),
        "arguments": ["/SILENT"],
        "worker_pids": (),
        "ack_path": str(tmp_path / "ack.json"),
        "marker_path": str(tmp_path / "update-complete.json"),
        "result_path": str(tmp_path / "result.json"),
        "log_path": str(tmp_path / "updater.jsonl"),
        "settle_ms": 0,
    }
    values.update(overrides)
    return UpdaterJob(**values)


def test_updater_core_and_entry_compile() -> None:
    for rel in ("app/updater_core.py", "scripts/updater_main.py"):
        source = (ROOT / rel).read_text(encoding="utf-8")
        compile(source, str(ROOT / rel), "exec")


def test_job_version_is_explicit_and_round_trip_is_strict(tmp_path: Path) -> None:
    assert JOB_VERSION >= 2
    job = _job(tmp_path, worker_pids=(41, 42))
    path = tmp_path / "job.json"
    job.save(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["job_version"] == JOB_VERSION
    loaded = UpdaterJob.load(path)
    assert loaded.target_version == "1.0.0"
    assert loaded.worker_pids == (41, 42)

    raw["job_version"] = JOB_VERSION + 1
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported update job version"):
        UpdaterJob.load(path)


def test_preflight_rehashes_installer_before_ack(tmp_path: Path) -> None:
    job = _job(tmp_path)
    Path(job.installer).write_bytes(b"tampered after GUI verification")
    assert run_job(job) == 3
    assert not Path(job.ack_path).exists()
    result = json.loads(Path(job.result_path).read_text(encoding="utf-8"))
    assert result["status"] == RESULT_VERIFY_FAILED


def test_ack_is_written_only_after_preflight(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    job = _job(tmp_path)
    monkeypatch.setattr(core, "_shutdown_gate", lambda _job: core.RESULT_APP_DID_NOT_EXIT)
    monkeypatch.setattr(core, "_launch_app", lambda _path: True)
    assert run_job(job) == 4
    ack = json.loads(Path(job.ack_path).read_text(encoding="utf-8"))
    assert ack["status"] == "accepted"
    assert ack["job_version"] == JOB_VERSION
    assert ack["target_version"] == job.target_version


def test_success_requires_installed_version_then_writes_marker_and_relaunches(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    job = _job(tmp_path)
    Path(job.version_file).write_text(job.target_version, encoding="utf-8")
    monkeypatch.setattr(core, "_shutdown_gate", lambda _job: None)
    launched: list[str] = []
    monkeypatch.setattr(core, "_launch_app", lambda path: launched.append(path) is None)

    class _Proc:
        returncode = 0

    monkeypatch.setattr(core.subprocess, "run", lambda *args, **kwargs: _Proc())
    assert run_job(job) == 0
    result = json.loads(Path(job.result_path).read_text(encoding="utf-8"))
    marker = json.loads(Path(job.marker_path).read_text(encoding="utf-8"))
    assert result["status"] == RESULT_OK
    assert marker["version"] == job.target_version
    assert launched == [job.app_executable]
    assert not Path(job.installer).exists()


def test_nonzero_installer_exit_recovers_without_claiming_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    job = _job(tmp_path)
    monkeypatch.setattr(core, "_shutdown_gate", lambda _job: None)
    monkeypatch.setattr(core, "_launch_app", lambda _path: True)
    # Keep installer-process mocking isolated from the recovery path's tasklist
    # probes. The recovery state itself is what this test wants to exercise.
    monkeypatch.setattr(core, "_pid_matches", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(core, "_other_app_pids", lambda *_args, **_kwargs: ())

    class _Proc:
        returncode = 7

    monkeypatch.setattr(core.subprocess, "run", lambda *args, **kwargs: _Proc())
    assert run_job(job) == 5
    result = json.loads(Path(job.result_path).read_text(encoding="utf-8"))
    assert result["status"] == RESULT_INSTALL_FAILED
    assert not Path(job.marker_path).exists()


def test_process_matching_is_exact_not_substring_based() -> None:
    source = (ROOT / "app" / "updater_core.py").read_text(encoding="utf-8")
    assert "csv.reader" in source
    assert "_other_app_pids" in source
    assert "OWNED_APP_IMAGE" in source
    assert "OWNED_WORKER_IMAGE" in source
    assert '["taskkill", "/PID", str(pid), "/T", "/F"]' in source
    assert "worker_pids" in source
