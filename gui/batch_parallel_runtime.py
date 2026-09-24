from __future__ import annotations

from pathlib import Path
from types import MethodType
from typing import Any

from app.browser_session import is_cdp_ready
from app.cdp_automation_health import poison_matches_current_generation
from .batch_browser_session import (
    BatchSharedBrowserOwner,
    bind_batch_shared_browser,
    bind_job_shared_browser,
    shared_batch_browser,
)
from .batch_model import normalize_batch_concurrency, save_batch_run


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


def _route_execute_to_owned_worker(args: list[str]) -> list[str]:
    """Route the canonical executor through its in-process transport owner.

    Keep the first argv item as a normal helper script name. The frozen process
    router can therefore send the installed build to EcommerceAgentWorker.exe,
    while source-Python runs continue to use python.exe. No launcher assumes that
    ``sys.executable`` is a Python interpreter.
    """

    routed = list(args)
    if not routed or Path(routed[0]).name.casefold() != "makro_execute_listing.py":
        raise RuntimeError(
            "Batch execute routing expected makro_execute_listing.py as the canonical executor"
        )
    routed[0] = "makro_execute_owned.py"
    return routed


class BatchParallelRuntime:
    """Run concurrent Batch jobs on one account-bound Edge transport lane.

    Source/Product Identity and Resolver work may run concurrently across jobs,
    but every Makro browser action in one Batch belongs to the marketplace account
    captured when that Batch starts. Jobs persist that account identity together
    with their owned targetId/profile so a later account switch cannot silently
    execute an old prepared Batch in another store.

    Browser control remains serialized at the transport boundary for one account:
    each prepare worker owns the lane only through Step1/2 + Step3 schema capture,
    then releases it before AI. Execute workers enter the lane inside the packaged/
    source-neutral execution host itself. Cross-account parallel browser lanes are
    intentionally not synthesized from the same CDP generation.
    """

    def __init__(self, window: Any) -> None:
        self.window = window
        self.project_root = window.project_root.resolve()
        self.controller = window.batch_workspace.controller
        self.manager = getattr(window, "_managed_makro_browser", None)
        if self.manager is None:
            raise RuntimeError("Batch parallel runtime requires ManagedMakroBrowser")

        # ManagedMakroBrowser is installed first and keeps the canonical raw
        # BatchController methods here. BatchParallelRuntime becomes the sole Batch
        # start owner afterwards, so the same click is not routed through a second
        # synchronous ensure_ready() gate.
        self._original_start_prepare = getattr(self.manager, "_original_batch_prepare", None)
        self._original_start_execution = getattr(self.manager, "_original_batch_execute", None)
        if self._original_start_prepare is None or self._original_start_execution is None:
            raise RuntimeError("Batch parallel runtime requires canonical BatchController starts")

        self._owner: BatchSharedBrowserOwner | None = None
        self._parallelism = 0
        self._original_spawn = self.controller._spawn
        self._original_start_source = self.controller._start_source

        self._install_controller_routing()
        self.controller._batch_parallel_runtime = self
        self.manager.status_changed.connect(self._decorate_batch_status)
        self.controller.running_changed.connect(lambda _running: self._release_if_safe())
        self.controller.jobs_changed.connect(lambda _jobs: self._release_if_safe())
        self.window.destroyed.connect(lambda *_args: self._release_owner())

    def _assert_top_level_idle(self) -> None:
        if self.manager.update_quiesced:
            raise RuntimeError("Listing Studio 正在准备更新，暂时不能启动新的 Batch。")
        if self.manager.is_busy():
            raise RuntimeError(
                "已有 Single/真实填写/Batch 浏览器工作域正在运行。"
                "Batch 不会在另一个正式工作域持有 Makro Browser 时等待或抢占 session lease。"
            )

    def _task_account(self) -> tuple[Any, Path]:
        """Return the committed Makro account/profile for a new browser task."""

        current = getattr(self.manager, "channel_account", None)
        if current is None:
            # Compatibility for tests/tools that still construct the base manager.
            return None, Path(self.manager.profile_dir).resolve()

        selected_getter = getattr(self.manager, "selected_channel_account", None)
        selected = selected_getter() if callable(selected_getter) else current
        if selected.account_id != current.account_id:
            ensure_async = getattr(self.manager, "ensure_async", None)
            if callable(ensure_async):
                ensure_async()
            raise RuntimeError(
                f"Makro 店铺正在切换到 {selected.label}。状态变为 READY 后再启动 Batch，"
                "避免任务绑定到旧账号。"
            )

        profile_dir = Path(self.manager.profile_dir).resolve()
        store = getattr(self.manager, "channel_accounts", None)
        if store is not None:
            expected = Path(store.profile_dir(current)).resolve()
            if profile_dir != expected:
                raise RuntimeError(
                    "当前 Makro Browser Profile 与已选择平台账号不一致；"
                    "为防止串号，已拒绝创建 Batch。"
                )
        return current, profile_dir

    def _stamp_batch_account(self, batch: Any, account: Any) -> None:
        if account is None:
            return
        batch.makro_account_id = str(account.account_id)
        batch.makro_account_label = str(account.label)
        for job in batch.jobs:
            job.makro_account_id = str(account.account_id)
            job.makro_account_label = str(account.label)
            job.touch()
        save_batch_run(batch)

    def _assert_batch_account_matches_current(self) -> tuple[Any, Path]:
        account, profile_dir = self._task_account()
        batch = self.controller.batch
        if batch is None or account is None:
            return account, profile_dir

        expected_id = str(getattr(batch, "makro_account_id", "") or "").strip()
        current_id = str(account.account_id)
        if not expected_id:
            accounts_getter = getattr(self.manager, "list_channel_accounts", None)
            accounts = tuple(accounts_getter()) if callable(accounts_getter) else (account,)
            if len(accounts) > 1:
                raise RuntimeError(
                    "这个 Batch 创建于任务账号绑定功能启用之前，无法证明它属于哪个 Makro 店铺。"
                    "现在已经连接多个账号，为防止串号，请在目标店铺下重新执行“批量准备”。"
                )
            # Safe legacy migration: there is only one possible marketplace account.
            self._stamp_batch_account(batch, account)
            expected_id = current_id

        if expected_id != current_id:
            label = str(getattr(batch, "makro_account_label", "") or expected_id)
            raise RuntimeError(
                f"这个 Batch 属于 {label}，当前 Makro 店铺是 {account.label}。"
                "程序不会跨店铺复用已准备任务；请切换回原店铺后再执行，或在当前店铺重新准备。"
            )

        for job in batch.jobs:
            job_account_id = str(getattr(job, "makro_account_id", "") or "").strip()
            if not job_account_id:
                job.makro_account_id = current_id
                job.makro_account_label = str(account.label)
                job.touch()
                continue
            if job_account_id != current_id:
                raise RuntimeError(
                    f"{job.job_id} 的 Makro 账号归属与 Batch 不一致；"
                    "为防止商品上架到错误店铺，已停止执行，请重新准备该 Batch。"
                )
        save_batch_run(batch)
        return account, profile_dir

    def _ensure_start_generation(self, reason: str, port: int) -> None:
        """Recover only an actually unavailable/poisoned generation.

        Healthy Batch starts deliberately do not call ``probe_cdp_automation``.
        The manager already maintains that proof in the background, while each
        real prepare/execute worker performs the authoritative Playwright attach
        inside the exclusive transport lane. This keeps the GUI click path free
        from a 6-second Playwright startup/attach wait without weakening failure
        detection or recovery.
        """

        requested_port = int(port)
        if requested_port != int(self.manager.port):
            raise RuntimeError(
                f"Batch Makro CDP {requested_port} 与 GUI 托管端口 {self.manager.port} 不一致。"
            )
        if poison_matches_current_generation(requested_port) or not is_cdp_ready(
            requested_port,
            timeout_s=0.25,
        ):
            self.manager.ensure_ready(reason)

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
            account, profile_dir = runtime._task_account()
            requested = normalize_batch_concurrency(prepare_concurrency)
            runtime._parallelism = min(requested, max(1, len(urls)))
            port = int(config.makro_cdp_port)
            runtime._ensure_start_generation("Batch preparation", port)
            runtime._ensure_owner(port, profile_dir=profile_dir)
            batch = runtime._original_start_prepare(
                urls,
                config,
                prepare_concurrency=requested,
            )
            runtime._stamp_batch_account(batch, account)
            assert runtime._owner is not None
            bind_batch_shared_browser(batch, runtime._owner.browser)
            _controller._persist_emit(immediate=True)
            runtime._decorate_batch_status("READY", "账号绑定的单浏览器 transport lane 已接管")
            return batch

        def start_execution(
            _controller: Any,
            *,
            allow_save: bool,
            upload_images: bool,
            execute_concurrency: int = 6,
        ) -> None:
            runtime._assert_top_level_idle()
            _account, profile_dir = runtime._assert_batch_account_matches_current()
            config = _controller.config
            if config is None:
                raise RuntimeError("Batch execution requires runtime config")
            runtime._ensure_start_generation("Batch execution", int(config.makro_cdp_port))
            runtime._ensure_owner(int(config.makro_cdp_port), profile_dir=profile_dir)
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
                account_label = str(getattr(job, "makro_account_label", "") or "")
                _controller._emit_log_now(
                    f"[{job_id}] BROWSER_TAB account={account_label or 'legacy'} "
                    f"port={job.makro_cdp_port} target={target or 'new-owned-tab'}"
                )
                if stage == "execute":
                    routed = _route_execute_to_owned_worker(routed)
                    _controller._emit_log_now(
                        f"[{job_id}] BROWSER_TRANSPORT_LANE port={job.makro_cdp_port} "
                        "mode=in-process-owned-worker"
                    )
            runtime._original_spawn(job_id, stage, routed)

        def start_source(_controller: Any, job_id: str) -> None:
            if _controller.config is not None:
                _account, profile_dir = runtime._assert_batch_account_matches_current()
                runtime._ensure_owner(
                    int(_controller.config.makro_cdp_port),
                    profile_dir=profile_dir,
                )
            runtime._ensure_job_binding(_controller._job(job_id))
            runtime._original_start_source(job_id)

        self.controller.start_prepare = MethodType(start_prepare, self.controller)
        self.controller.start_execution = MethodType(start_execution, self.controller)
        self.controller._spawn = MethodType(spawn, self.controller)
        self.controller._start_source = MethodType(start_source, self.controller)

    def _ensure_owner(self, port: int, *, profile_dir: str | Path | None = None) -> None:
        requested_port = int(port)
        requested_profile = Path(
            profile_dir if profile_dir is not None else self.manager.profile_dir
        ).resolve()
        owner = self._owner
        if (
            owner is not None
            and int(owner.browser.cdp_port) == requested_port
            and owner.browser.profile_dir.resolve() == requested_profile
        ):
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
                    "当前 Batch 已绑定 Makro targetId，不能中途切换账号 Profile/CDP 端口；"
                    "请切换回该 Batch 的原店铺，或重新批量准备。"
                )
            owner.release()

        browser = shared_batch_browser(
            self.project_root,
            cdp_port=requested_port,
            profile_dir=requested_profile,
        )
        owner = BatchSharedBrowserOwner(browser)
        owner.acquire()
        self._owner = owner

    def _ensure_job_binding(self, job: Any) -> None:
        config = self.controller.config
        if config is None:
            raise RuntimeError("Batch browser binding requires runtime config")
        _account, profile_dir = self._assert_batch_account_matches_current()
        self._ensure_owner(int(config.makro_cdp_port), profile_dir=profile_dir)
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
                f"{job.job_id} 属于其他 Makro Browser/Profile，targetId 不能迁移；请重新批量准备。"
            )
        bind_job_shared_browser(job, browser)

    def _assert_prepared_browser_alive(self) -> None:
        batch = self.controller.batch
        if batch is None:
            return
        self._assert_batch_account_matches_current()
        if self._owner is None or not self._owner.acquired:
            if any(str(job.makro_target_id or "") for job in batch.jobs):
                raise RuntimeError(
                    "这个 Batch 没有当前 GUI 持有的账号绑定 browser session owner；请重新批量准备。"
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
                "这些任务的 Makro Browser ownership 与当前账号不一致，不能安全迁移 targetId；"
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
        account = getattr(self.manager, "channel_account", None)
        account_label = str(getattr(account, "label", "") or "当前店铺")
        label.setText(
            f"Makro Browser · {state} · {account_label} · {detail} · "
            f"单 Edge {port} · {parallelism} Jobs / 1 transport lane"
        )
        label.setToolTip(
            "当前 Batch 永久绑定创建时的 Makro 店铺与独立 Browser Profile。"
            "多个商品仍可并行做 Source/AI；Makro 浏览器阶段按 owned targetId 进入该账号唯一 transport lane，"
            "账号切换后不会跨店铺复用旧任务。"
        )


def install_batch_parallel_runtime(window: Any) -> BatchParallelRuntime:
    existing = getattr(window, "_batch_parallel_runtime", None)
    if isinstance(existing, BatchParallelRuntime):
        return existing
    runtime = BatchParallelRuntime(window)
    window._batch_parallel_runtime = runtime
    return runtime


__all__ = ["BatchParallelRuntime", "install_batch_parallel_runtime"]
