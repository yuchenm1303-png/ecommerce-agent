"""Standalone updater entry with continuous topmost progress UI."""
from __future__ import annotations

import argparse
import traceback
from pathlib import Path

from scripts import updater_main as legacy


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="updater")
    parser.add_argument("--job")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)
    if args.self_check:
        return legacy._self_check()
    if not args.job:
        parser.error("--job is required unless --self-check is used")

    job_path = Path(args.job)
    if not job_path.is_file():
        return 2
    legacy._bootstrap_source_path()
    try:
        from app.updater_core import UpdaterJob, run_job
        from app.updater_panel import NativeUpdatePanel
        job = UpdaterJob.load(job_path)
    except Exception:
        return 2

    legacy._sanitize_external_child_runtime()
    legacy._ensure_installer_log(job)
    panel = NativeUpdatePanel(job.target_version, getattr(job, "log_path", ""))
    panel.set_phase("步骤 4/6 · 正在关闭 Listing Studio", "更新执行器已经接管，正在释放程序文件。")
    try:
        try:
            code = run_job(job)
        except Exception as exc:
            detail = "unhandled updater exception: " + repr(exc)
            legacy._append_crash_log(job, detail + "\n" + traceback.format_exc())
            legacy._write_crash_result(job, detail)
            legacy._best_effort_relaunch(job)
            panel.set_phase("更新未完成", "正在恢复原程序并准备错误详情…")
            panel.finish(False)
            legacy._show_failure_dialog(job, fallback_detail=detail)
            return 99
        panel.finish(code == 0)
        if code != 0:
            legacy._show_failure_dialog(job)
        return code
    finally:
        panel.finish(False)
        try:
            job_path.unlink(missing_ok=True)
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
