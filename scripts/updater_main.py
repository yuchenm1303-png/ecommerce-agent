"""Entry point for the standalone, windowed updater executable."""

from __future__ import annotations

import argparse
import ctypes
import os
import sys
from pathlib import Path


def _bootstrap_source_path() -> None:
    if getattr(sys, "frozen", False):
        return
    root = str(Path(__file__).resolve().parent.parent)
    if root not in sys.path:
        sys.path.insert(0, root)


def _path_is_inside(candidate: str, root: str) -> bool:
    if not candidate or not root:
        return False
    try:
        candidate_abs = os.path.normcase(os.path.abspath(candidate))
        root_abs = os.path.normcase(os.path.abspath(root))
        return os.path.commonpath([candidate_abs, root_abs]) == root_abs
    except (OSError, ValueError):
        return False


def _sanitize_external_child_runtime() -> None:
    """Detach future children from this updater's PyInstaller runtime.

    PyInstaller intentionally mutates the frozen process environment.  On
    Windows the DLL directory and private ``_PYI_*`` variables are inherited by
    subprocesses.  That is correct for bundled helpers, but wrong for the Inno
    installer and for a restarted, independent Listing Studio instance.

    This function is called only after the updater core and job have been fully
    imported/decoded, so the updater no longer needs its bundle DLL search path.
    From this point onward every external child gets the normal Windows runtime
    and a fresh PyInstaller instance.
    """

    bundle_root = str(getattr(sys, "_MEIPASS", "") or "")

    for key in tuple(os.environ):
        if key.startswith("_PYI_"):
            os.environ.pop(key, None)
    os.environ["PYINSTALLER_RESET_ENVIRONMENT"] = "1"

    if bundle_root:
        entries = [entry for entry in os.environ.get("PATH", "").split(os.pathsep) if entry]
        os.environ["PATH"] = os.pathsep.join(
            entry for entry in entries if not _path_is_inside(entry, bundle_root)
        )

    if sys.platform == "win32":
        try:
            # PyInstaller uses SetDllDirectoryW(bundle_root).  Child processes
            # inherit that DLL search path unless it is reset to the Windows
            # default before they are created.
            ctypes.windll.kernel32.SetDllDirectoryW(None)
        except (AttributeError, OSError):
            pass


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
        # Import/load everything while the updater still owns its frozen bundle
        # runtime.  Only then detach the environment used by external children.
        from app.updater_core import UpdaterJob, run_job

        job = UpdaterJob.load(job_path)
    except Exception:
        return 2

    _sanitize_external_child_runtime()
    try:
        return run_job(job)
    finally:
        try:
            job_path.unlink(missing_ok=True)
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
