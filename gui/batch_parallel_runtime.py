from __future__ import annotations

from pathlib import Path
from types import MethodType
from typing import Any

from app.cdp_transport_lane import python_args_under_transport_lane
from .batch_browser_session import (
    BatchSharedBrowserOwner,
    bind_batch_shared_browser,
    bind_job_shared_browser,
    shared_batch_browser,
)
from .batch_model import normalize_batch_concurrency


def _set_cli_option(args: list[str], name: str, value: str) -> None:
    try:
        index = args.index(name)
    except ValueError:
        args.extend([name, value])
        return
    if index + 1 >= len(args):
        args.append(value)
    else:
        args[index + 1] = value


class BatchParallelRuntime:
    """Run concurrent Batch jobs on one Edge with one real CDP transport lane.

    The GUI process owns the long-lived logical browser session. Source/Product
    Identity and Resolver work may run concurrently across jobs, but browser
    control is serialized at the transport boundary: each prepare worker owns the
    lane only through Step1/2 + Step3 schema capture, then releases it before AI.
    Execute workers are launched through the same lane wrapper. targetId remains
    the per-product tab ownership boundary; no worker Edge profiles or secondary
    Makro CDP ports exist.
    """

    def __init__(self, window: Any) -> None:
        self.window = window
        self.project_root = window.project_root.resolve()
        self.controller = window.batch_workspace.controller
        self.manager = getattr(window, "_managed_makro_browser", None)
        if self.manager is None:
            raise RuntimeError("Batch parallel runtime requires ManagedMakroBrowser")

        self._owner: BatchSharedBrowserOwner | None = None
        self._parallelism = 0
        self._original_start_prepare = self.controller.start_prepare
        self._original_start_execution = self.controller.start_execution
        self._original_spawn = self.controller._spawn
        self._original_start_source = self.controller._start_source

        self._install_controller_routing()
        self.controller._batch_parallel_runtime = self
        self.manager.status_changed.connect(self._decorate_batch_status)
        self.controller.running_changed.connect(lambda _running: self._release_if_safe())
        self.controller.jobs_changed.connect(lambda _jobs: self._release_if_safe())
        self.window.destroyed.connect(lambda *_args: self._release_owner())

    def _assert_top_level_idle(self) -> None:
        if self.manager.is_busy():
            raise RuntimeError(
                "已有 Single/真实填写/Batch 浏览器工作域正在运行。"
                "Batch 不会在另一个正式工作域持有 Makro Browser 时等待或抢占 session lease。"
            )

    def _install_controller_routing(self) -> None:
        runtime = self

        def start_prepare(
            _controller: Any,
            urls: list[str],
            config: Any,
            *,
            prepare_concurrency: int = 6,
        ):
            runtime._assert_top_level_idle()
            requested = normalize_batch_concurrency(prepare_concurrency)
            runtime._parallelism = min(requested, max(1, len(urls)))
            runtime.manager.ensure_ready("Batch single-browser preparation")
            runtime._ensure_owner(int(config.makro_cdp_port))
            batch = runtime._original_start_prepare(
                urls,
                config,
                prepare_concurrency=requested,
            )
            assert runtime._owner is not None
            bind_batch_shared_browser(batch, runtime._owner.browser)
            _controller._persist_emit(immediate=True)
            runtime._decorate_batch_status("READY", "单浏览器 transport lane 已接管")
            return batch

        def start_execution(
            _controller: Any,
            *,
            allow_save: bool,
            upload_images: bool,
            execute_concurrency: int = 6,
        ) -> None:
            runtime._assert_top_level_idle()
            runtime._assert_prepared_browser_alive()
            runtime._parallelism = normalize_batch_concurrency(execute_concurrency)
            runtime._original_start_execution(
                allow_save=allow_save,
                upload_images=upload_images,
                execute_concurrency=execute_concurrency,
            )

        def spawn(_controller: Any, job_id: str, stage: str, args: list[str]) -> None:
            routed = list(args)
            if stage in {"prepare", "execute"}:
                job = _controller._job(job_id)
                runtime._ensure_job_binding(job)
                _set_cli_option(routed, "--cdp-port", str(job.makro_cdp_port))
                _set_cli_option(routed, "--profile-dir", str(job.makro_profile_dir))
                target = str(getattr(job, "makro_target_id", "") or "")
                _controller._emit_log_now(
                    f"[{job_id}] BROWSER_TAB port={job.makro_cdp_port} "
                    f"target={target or 'new-owned-tab'}"
                )
                if stage == "execute":
                    routed = python_args_under_transport_lane(job.makro_cdp_port, routed)
                    _controller._emit_log_now(
                        f"[{job_id}] BROWSER_TRANSPORT_LANE port={job.makro_cdp_port} mode=exclusive-write"
                    )
            runtime._original_spawn(job_id, stage, routed)

        def start_source(_controller: Any, job_id: str) -> None:
            if _controller.config is not None:
                runtime._ensure_owner(int(_controller.config.makro_cdp_port))
            runtime._ensure_job_binding(_controller._job(job_id))
            runtime._original_start_source(job_id)

        self.controller.start_prepare = MethodType(start_prepare, self.controller)
        self.controller.start_execution = MethodType(start_execution, self.controller)
        self.controller._spawn = MethodType(spawn, self.controller)
        self.controller._start_source = MethodType(start_source, self.controller)

    def _ensure_owner(self, port: int) -> None:
        requested_port = int(port)
        owner = self._owner
        if owner is not None and int(owner.browser.cdp_port) == requested_port:
            owner.assert_alive()
            return

        batch = self.controller.batch
        if owner is not None:
            has_owned_tabs = bool(
                batch is not None
                and any(str(job.makro_target_id or "") for job in batch.jobs)
            )
            if has_owned_tabs:
                raise RuntimeError(
                    "当前 Batch 已绑定 Makro targetId，不能中途切换 CDP 端口；请重新批量准备。"
                )
            owner.release()

        browser = shared_batch_browser(self.project_root, cdp_port=requested_port)
        owner = BatchSharedBrowserOwner(browser)
        owner.acquire()
        self._owner = owner

    def _ensure_job_binding(self, job: Any) -> None:
        config = self.controller.config
        if config is None:
            raise RuntimeError("Batch browser binding requires runtime config")
        self._ensure_owner(int(config.makro_cdp_port))
        assert self._owner is not None
        browser = self._owner.browser

        target = str(getattr(job, "makro_target_id", "") or "")
        current_port = int(getattr(job, "makro_cdp_port", 0) or 0)
        current_profile = str(getattr(job, "makro_profile_dir", "") or "").strip()
        if target and (
            current_port not in {0, int(browser.cdp_port)}
            or (
                current_profile
                and Path(current_profile).resolve() != browser.profile_dir.resolve()
            )
        ):
            raise RuntimeError(
                f"{job.job_id} 属于旧多浏览器 Batch，targetId 不能迁移到单浏览器；请重新批量准备。"
            )
        bind_job_shared_browser(job, browser)

    def _assert_prepared_browser_alive(self) -> None:
        batch = self.controller.batch
        if batch is None:
            return
        if self._owner is None or not self._owner.acquired:
            if any(str(job.makro_target_id or "") for job in batch.jobs):
                raise RuntimeError(
                    "这个 Batch 没有当前 GUI 持有的单浏览器 session owner；请重新批量准备。"
                )
            return

        self._owner.assert_alive()
        browser = self._owner.browser
        expected_profile = browser.profile_dir.resolve()
        incompatible: list[str] = []
        for job in batch.jobs:
            if not str(job.makro_target_id or ""):
                continue
            port = int(job.makro_cdp_port or 0)
            profile = str(job.makro_profile_dir or "").strip()
            if (
                port != int(browser.cdp_port)
                or not profile
                or Path(profile).resolve() != expected_profile
                or int(job.browser_lane or 0) != 0
            ):
                incompatible.append(str(job.job_id))
        if incompatible:
            raise RuntimeError(
                "这些任务来自旧多浏览器 ownership，不能安全迁移 targetId；"
                f"请重新批量准备。jobs={incompatible}"
            )

    def _release_if_safe(self) -> None:
        if self.controller.is_running:
            return
        batch = self.controller.batch
        if batch is None or not batch.jobs:
            self._release_owner()
            return
        if str(batch.status) in {"COMPLETE", "STOPPED"}:
            self._release_owner()

    def _release_owner(self) -> None:
        owner = self._owner
        self._owner = None
        if owner is not None:
            owner.release()

    def _decorate_batch_status(self, state: str, detail: str) -> None:
        label = getattr(self.manager, "_batch_label", None)
        if label is None or self._owner is None:
            return
        port = int(self._owner.browser.cdp_port)
        parallelism = max(1, int(self._parallelism or 1))
        label.setText(
            f"Makro Browser · {state} · {detail} · 单 Edge {port} · {parallelism} Jobs / 1 transport lane"
        )
        label.setToolTip(
            "Batch 只使用一个 Makro Edge 和一条真实 Playwright/CDP transport。"
            "多个任务仍可并行做 Source/AI；浏览器阶段按 owned targetId 进入唯一 transport lane，"
            "避免多个 Playwright transport 同时控制同一 Chromium generation。"
        )


def install_batch_parallel_runtime(window: Any) -> BatchParallelRuntime:
    existing = getattr(window, "_batch_parallel_runtime", None)
    if isinstance(existing, BatchParallelRuntime):
        return existing
    runtime = BatchParallelRuntime(window)
    window._batch_parallel_runtime = runtime
    return runtime


__all__ = ["BatchParallelRuntime", "install_batch_parallel_runtime"]
