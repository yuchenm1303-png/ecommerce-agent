"""Entry point for the standalone, windowed updater executable."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _bootstrap_source_path() -> None:
    if getattr(sys, "frozen", False):
        return
    root = str(Path(__file__).resolve().parent.parent)
    if root not in sys.path:
        sys.path.insert(0, root)


def _self_check() -> int:
    """Exercise the embedded Python runtime and updater core, not just bootloader."""

    _bootstrap_source_path()
    try:
        from app.updater_core import JOB_VERSION, UpdaterJob, run_job

        if JOB_VERSION < 2 or not callable(run_job) or UpdaterJob is None:
            return 3
    except Exception:
        return 3
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="updater")
    parser.add_argument("--job", help="path to the JSON update job file")
    parser.add_argument("--self-check", action="store_true", help="runtime/core smoke test")
    args = parser.parse_args(argv)

    if args.self_check:
        return _self_check()
    if not args.job:
        parser.error("--job is required unless --self-check is used")

    job_path = Path(args.job)
    if not job_path.is_file():
        return 2

    _bootstrap_source_path()
    try:
        from app.updater_core import UpdaterJob, run_job

        job = UpdaterJob.load(job_path)
    except Exception:
        return 2

    try:
        return run_job(job)
    finally:
        try:
            job_path.unlink(missing_ok=True)
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
