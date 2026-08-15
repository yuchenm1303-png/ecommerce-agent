"""Entry point for the standalone, windowed updater executable."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import traceback
from datetime import datetime, timezone
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
    """Detach future children from this updater's PyInstaller runtime."""

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


def _installer_log_path(job: object) -> Path:
    arguments = list(getattr(job, "arguments", []) or [])
    for argument in arguments:
        value = str(argument or "")
        if value.upper().startswith("/LOG="):
            raw = value.split("=", 1)[1].strip().strip('"')
            if raw:
                return Path(raw)
    updater_log = str(getattr(job, "log_path", "") or "")
    if updater_log:
        return Path(updater_log).with_name("installer.log")
    return Path(os.getenv("LOCALAPPDATA") or Path.home()) / "ListingStudio" / "updater" / "installer.log"


def _ensure_installer_log(job: object) -> Path:
    path = _installer_log_path(job)
    arguments = list(getattr(job, "arguments", []) or [])
    if not any(str(value or "").upper().startswith("/LOG=") for value in arguments):
        arguments.append(f"/LOG={path}")
        setattr(job, "arguments", arguments)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)
    except OSError:
        pass
    return path


def _tail_text(path: Path, *, max_lines: int = 80, max_chars: int = 9000) -> str:
    if not path.is_file():
        return "(log file not created)"
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return f"(failed to read log: {exc})"
    text = "\n".join(lines[-max_lines:]).strip()
    if len(text) > max_chars:
        text = "…\n" + text[-max_chars:]
    return text or "(log file is empty)"


def _result_payload(job: object) -> dict[str, object]:
    path = Path(str(getattr(job, "result_path", "") or ""))
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_crash_result(job: object, detail: str) -> None:
    result_path = str(getattr(job, "result_path", "") or "")
    if not result_path:
        return
    path = Path(result_path)
    payload = {
        "status": "updater_crashed",
        "detail": detail,
        "target_version": str(getattr(job, "target_version", "") or ""),
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, path)
    except OSError:
        pass


def _append_crash_log(job: object, text: str) -> None:
    path_text = str(getattr(job, "log_path", "") or "")
    if not path_text:
        return
    path = Path(path_text)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(text.rstrip() + "\n")
    except OSError:
        pass


def _best_effort_relaunch(job: object) -> None:
    executable = Path(str(getattr(job, "app_executable", "") or ""))
    if not executable.is_file():
        return
    try:
        subprocess.Popen(
            [str(executable)],
            creationflags=0x00000008 | 0x00000200,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            env=os.environ.copy(),
        )
    except OSError:
        pass


def _show_failure_dialog(job: object, *, fallback_detail: str = "") -> None:
    if not bool(getattr(sys, "frozen", False)) or sys.platform != "win32":
        return

    result = _result_payload(job)
    status = str(result.get("status") or "updater_failed")
    detail = str(result.get("detail") or fallback_detail or "unknown updater failure")
    updater_log = Path(str(getattr(job, "log_path", "") or ""))
    installer_log = _installer_log_path(job)
    target = str(getattr(job, "target_version", "") or "")

    message = (
        f"Listing Studio 自动更新失败\n\n"
        f"目标版本：v{target or '?'}\n"
        f"状态：{status}\n"
        f"原因：{detail}\n\n"
        f"Updater 日志：{updater_log}\n"
        f"{_tail_text(updater_log)}\n\n"
        f"Inno Setup 日志：{installer_log}\n"
        f"{_tail_text(installer_log)}"
    )
    if len(message) > 18000:
        message = message[:18000] + "\n…"
    try:
        ctypes.windll.user32.MessageBoxW(
            None,
            message,
            "Listing Studio 更新失败",
            0x00000010 | 0x00040000,
        )
    except (AttributeError, OSError):
        pass


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

    _sanitize_external_child_runtime()
    _ensure_installer_log(job)
    try:
        try:
            exit_code = run_job(job)
        except Exception as exc:
            crash = "unhandled updater exception: " + repr(exc)
            _append_crash_log(job, crash + "\n" + traceback.format_exc())
            _write_crash_result(job, crash)
            _best_effort_relaunch(job)
            _show_failure_dialog(job, fallback_detail=crash)
            return 99

        if exit_code != 0:
            _show_failure_dialog(job)
        return exit_code
    finally:
        try:
            job_path.unlink(missing_ok=True)
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
