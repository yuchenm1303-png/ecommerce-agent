"""Frozen parent used by the Windows updater end-to-end packaging smoke test.

This executable intentionally runs as ``EcommerceAgent.exe`` from a disposable
old install tree.  It launches the real packaged updater, waits for the updater
ACK, then exits exactly like the production GUI does after handoff.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

JOB_VERSION = 2
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--updater", required=True)
    parser.add_argument("--installer", required=True)
    parser.add_argument("--target-version", required=True)
    parser.add_argument("--install-dir", required=True)
    parser.add_argument("--relaunch-probe", required=True)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--setup-log", required=True)
    args = parser.parse_args()

    updater = Path(args.updater).resolve()
    installer = Path(args.installer).resolve()
    install_dir = Path(args.install_dir).resolve()
    relaunch_probe = Path(args.relaunch_probe).resolve()
    state_dir = Path(args.state_dir).resolve()
    setup_log = Path(args.setup_log).resolve()
    version_file = install_dir / "_internal" / "packaging" / "VERSION"

    for required in (updater, installer, relaunch_probe, version_file):
        if not required.is_file():
            return 11

    state_dir.mkdir(parents=True, exist_ok=True)
    ack_path = state_dir / "handoff.json"
    job_path = state_dir / "pending-update.json"
    result_path = state_dir / "last-result.json"
    marker_path = state_dir / "update-complete.json"
    updater_log = state_dir / "updater.jsonl"
    relaunch_marker = state_dir / "relaunch-probe.json"
    for path in (ack_path, job_path, result_path, marker_path, updater_log, relaunch_marker, setup_log):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    job = {
        "job_version": JOB_VERSION,
        "installer": str(installer),
        "target_version": str(args.target_version).strip().lstrip("v"),
        "app_pid": os.getpid(),
        "app_image_name": Path(sys.executable).stem,
        "app_executable": str(relaunch_probe),
        "version_file": str(version_file),
        "installer_sha256": _sha256(installer),
        "arguments": [
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            "/CLOSEAPPLICATIONS",
            "/NORESTARTAPPLICATIONS",
            f"/DIR={install_dir}",
            f"/LOG={setup_log}",
        ],
        "worker_pids": [],
        "app_deadline_s": 15,
        "worker_deadline_s": 5,
        "settle_ms": 250,
        "ack_path": str(ack_path),
        "marker_path": str(marker_path),
        "log_path": str(updater_log),
        "result_path": str(result_path),
    }
    _write_json(job_path, job)

    env = os.environ.copy()
    env["ECOMMERCE_AGENT_E2E_RELAUNCH_MARKER"] = str(relaunch_marker)
    try:
        proc = subprocess.Popen(
            [str(updater), "--job", str(job_path)],
            env=env,
            creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return 12

    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        if ack_path.is_file():
            try:
                ack = json.loads(ack_path.read_text(encoding="utf-8"))
            except Exception:
                ack = {}
            if (
                isinstance(ack, dict)
                and ack.get("status") == "accepted"
                and int(ack.get("job_version") or 0) == JOB_VERSION
                and str(ack.get("target_version") or "") == str(args.target_version).strip().lstrip("v")
            ):
                return 0
        if proc.poll() is not None:
            return 13
        time.sleep(0.05)
    return 14


if __name__ == "__main__":
    raise SystemExit(main())
