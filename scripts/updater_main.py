"""Standalone updater executable entry point.

Built by ``packaging/Updater.spec`` into a single windowed ``updater.exe`` with
no Qt dependency. The GUI launches it detached with ``--job <path>`` after it
has closed its modal dialogs; the updater waits for the app to exit, verifies
and runs the installer, and records the result for diagnostics.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _self_check() -> int:
    # Windowed build has no usable stdout; the exit code is the contract.
    return 0


def _bootstrap_source_path() -> None:
    """Make ``app`` importable when run as a script (frozen handles itself)."""

    if getattr(sys, "frozen", False):
        return
    root = str(Path(__file__).resolve().parent.parent)
    if root not in sys.path:
        sys.path.insert(0, root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="updater")
    parser.add_argument("--job", help="path to the JSON update job file")
    parser.add_argument("--self-check", action="store_true", help="CI smoke-test probe")
    args = parser.parse_args(argv)

    if args.self_check:
        return _self_check()
    if not args.job:
        parser.error("--job is required unless --self-check is used")
    job_path = Path(args.job)
    if not job_path.is_file():
        return 2

    _bootstrap_source_path()
    from app.updater_core import UpdaterJob, run_job

    job = UpdaterJob.load(job_path)
    return run_job(job)


if __name__ == "__main__":
    raise SystemExit(main())
