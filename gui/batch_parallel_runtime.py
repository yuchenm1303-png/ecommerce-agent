from __future__ import annotations

from pathlib import Path
from types import MethodType
from typing import Any

from app.browser_session import is_cdp_ready
from app.cdp_automation_health import poison_matches_current_generation
from .batch_account_slot_store import BatchAccountSlotStore
from .batch_browser_session import (
    BatchSharedBrowserOwner,
    bind_batch_shared_browser,
    bind_job_shared_browser,
    shared_batch_browser,
)
from .batch_model import load_batch_run, normalize_batch_concurrency, save_batch_run
from .readonly_runner import RunnerConfig


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
    """Route the canonical executor through its in-process transport owner."""

    routed = list(args)
    if not routed or Path(routed[0]).name.casefold() != "makro_execute_listing.py":
        raise RuntimeError(
            "Batch execute routing expected makro_execute_listing.py as the canonical executor"
        )
    routed[0] = "makro_execute_owned.py"
    return routed


class BatchParallelRuntime:
    """Run Batch work on an account-bound Makro browser lane.

    Every Batch is permanently owned by the Makro account that created it. The
    runtime keeps one independent Batch slot per account and persists the latest
    slot pointer across application restarts. Each Batch also records the Edge
    browser-instance token that created its owned targetIds; a restored Batch may
    reuse those tabs only if the same account/profile/port/generation is alive.

    Current UI scheduling remains one active Batch controller at a time. Separate
    per-account CDP/Profile lanes are already in place, but simultaneous execution
    across accounts is intentionally a later scheduler step rather than sharing a
    controller or transport owner unsafely.
    """

    def __init__(self, window: Any) -> None:
        self.window = window
        self.project_root = window.project_root.resolve()
        self.controller = window.batch_workspace.controller
        self.manager = getattr(window, "_managed_makro_browser", None)
        if self.manager is None:
            raise RuntimeError("Batch parallel runtime requires ManagedMakroBrowser")

        self._original_start_prepare = getattr(self.manager, "_original_batch_prepare", None)
        self._original_start_execution = getattr(self.manager, "_original_batch_execute", None)
        if self._original_start_prepare is None or self._original_start_execution is None:
            raise RuntimeError("Batch parallel runtime requires canonical BatchController starts")

        self._owner: BatchSharedBrowserOwner | None = None
        self._parallelism = 0
        self._starting_account: Any | None = None
        self._account_slots: dict[str, tuple[Any, Any]] = {}
        scope_token = str(getattr(self.manager.channel_accounts, "scope_token", "") or "")
        self._slot_store = BatchAccountSlotStore(
            self.project_root,
            scope_token=scope_token,
        )
        self._original_spawn = self.controller._spawn
        self._original_start_source = self.controller._start_source

        self._install_controller_routing()
        self.controller._batch_parallel_runtime = self
        self.manager.status_changed.connect(self._decorate_batch_status)
        self.controller.running_changed.connect(lambda _running: self._release_if_safe())
        self.controller.jobs_changed.connect(lambda _jobs: self._release_if_safe())
        self.window.destroyed.connect(lambda *_args: self._release_owner())
        self._restore_persisted_slots()

    @staticmethod
    def _empty_summary() -> dict[str, int]:
        return {
            "total": 0,
            "processing": 0,
            "ready": 0,
            "done": 0,
            "review": 0,
            "failed": 0,
        }

    def account_slot_snapshot(self, account_id: str) -> dict[str, Any]:
        """Return presentation-safe state for one account's independent Batch slot."""

        account_key = str(account_id or "").strip()
        if not account_key:
            raise ValueError("Makro account_id must not be empty")

        slot = self._account_slots.get(account_key)
        if slot is None:
            return {
                "account_id": account_key,
                "has_batch": False,
                "batch_id": "",
                "status": "IDLE",
                "running": False,
                "summary": self._empty_summary(),
            }

        batch, _config = slot
        owner_id = str(getattr(batch, "makro_account_id", "") or "").strip()
        if owner_id and owner_id != account_key:
            raise RuntimeError(
                "Makro Batch 槽位账号归属损坏；为防止串号，已拒绝展示该槽位。"
            )

        summary = batch.summary() if callable(getattr(batch, "summary", None)) else self._empty_summary()
        current = getattr(self.manager, "channel_account", None)
        running = bool(
            current is not None
            and str(getattr(current, "account_id", "") or "") == account_key
            and self.controller.is_running
        )
        return {
            "account_id": account_key,
            "has_batch": True,
            "batch_id": str(getattr(batch, "batch_id", "") or ""),
            "status": str(getattr(batch, "status", "") or "IDLE").upper(),
            "running": running,
            "summary": dict(summary),
        }

    def _controller_log(self, message: str) -> None:
        emit = getattr(self.controller, "_emit_log_now", None)
        if callable(emit):
            emit(message)

    def _normalize_recovered_batch(self, batch: Any) -> bool:
        """Turn interrupted process state into explicit STOPPED state on restart."""

        safe_batch_states = {"PREPARED", "COMPLETE", "STOPPED"}
        if str(getattr(batch, "status", "") or "").upper() in safe_batch_states:
            return False

        safe_job_states = {"READY", "DONE", "REVIEW", "FAILED", "STOPPED"}
        for job in batch.jobs:
            if str(job.status or "").upper() in safe_job_states:
                continue
            job.failure_stage = job.failure_stage or job.stage_detail or "application restart"
            job.exit_code = None
            job.status = "STOPPED"
            job.stage_detail = "应用重启，原运行任务已停止"
            job.touch()
        batch.status = "STOPPED"
        return True

    def _restore_persisted_slots(self) -> None:
        for account in self.manager.list_channel_accounts():
            try:
                path = self._slot_store.batch_path(account.account_id)
            except Exception as exc:
                self._controller_log(
                    f"[makro-account-slot] {account.label} 槽位索引无效，已跳过恢复：{exc}"
                )
                continue
            if path is None:
                continue
            try:
                batch = load_batch_run(path)
            except Exception as exc:
                self._controller_log(
                    f"[makro-account-slot] {account.label} Batch 无法读取，已跳过恢复：{exc}"
                )
                continue

            owner_id = str(getattr(batch, "makro_account_id", "") or "").strip()
            if owner_id != str(account.account_id):
                self._controller_log(
                    f"[makro-account-slot] {account.label} Batch 账号归属不匹配，已拒绝恢复。"
                )
                continue
            if self._normalize_recovered_batch(batch):
                save_batch_run(batch)
            self._account_slots[str(account.account_id)] = (batch, None)

        self.activate_account_slot(self.manager.channel_account)

    def _remember_current_slot(self) -> None:
        batch = self.controller.batch
        if batch is None:
            return
        account_id = str(getattr(batch, "makro_account_id", "") or "").strip()
        if not account_id:
            return
        self._account_slots[account_id] = (batch, self.controller.config)
        self._slot_store.remember(account_id, batch.root_dir)
        save_batch_run(batch)

    def _ensure_controller_config(self) -> RunnerConfig:
        config = self.controller.config
        if config is not None:
            return config

        batch = self.controller.batch
        if batch is None or not batch.jobs:
            raise RuntimeError("恢复的 Batch 没有商品任务，无法重建运行配置。")
        workspace = self.window.batch_workspace
        config = RunnerConfig(
            product_url=str(batch.jobs[0].product_url),
            makro_cdp_port=int(workspace.makro_port.value()),
            source_cdp_port=int(workspace.source_port.value()),
            source_use_current_page=False,
        )
        if int(config.makro_cdp_port) != int(self.manager.port):
            raise RuntimeError(
                "恢复 Batch 时当前工作区 Makro CDP 端口与账号专属 lane 不一致；已拒绝执行。"
            )
        self.controller.config = config
        account_id = str(getattr(batch, "makro_account_id", "") or "").strip()
        if account_id:
            self._account_slots[account_id] = (batch, config)
        return config

    def activate_account_slot(self, account: Any) -> None:
        """Swap the visible Batch controller state to one Makro account's slot."""

        if self.controller.is_running:
            raise RuntimeError("Batch 仍在运行，不能切换独立 Makro 任务槽位。")

        self._remember_current_slot()
        self._release_owner()

        account_id = str(getattr(account, "account_id", "") or "").strip()
        if not account_id:
            raise RuntimeError("目标 Makro 账号缺少 account_id，不能恢复任务槽位。")
        slot = self._account_slots.get(account_id)
        if slot is None:
            batch = None
            config = None
        else:
            batch, config = slot
            stored_id = str(getattr(batch, "makro_account_id", "") or "").strip()
            if stored_id != account_id:
                raise RuntimeError("Makro Batch 槽位账号归属损坏；为防止串号，已拒绝恢复。")

        self.controller.batch = batch
        self.controller.config = config
        self.controller._mode = "idle"
        self.controller._source_queue.clear()
        self.controller._prepare_queue.clear()
        self.controller._execute_queue.clear()
        self.controller._source_resume_pending.clear()
        self.controller._stopping = False
        self._parallelism = 0

        jobs = list(batch.jobs) if batch is not None else []
        summary = batch.summary() if batch is not None else self._empty_summary()
        self.controller.jobs_changed.emit(jobs)
        self.controller.summary_changed.emit(summary)
        self.controller.running_changed.emit(False)
        if batch is None:
            self.controller.state_changed.emit(f"{account.label} · 暂无 Batch")
        else:
            self.controller.state_changed.emit(
                f"{account.label} · 已恢复 Batch {batch.batch_id} · {batch.status}"
            )

    def _assert_top_level_idle(self) -> None:
        if self.manager.update_quiesced:
            raise RuntimeError("Listing Studio 正在准备更新，暂时不能启动新的 Batch。")
        if self.manager.is_busy():
            raise RuntimeError(
                "已有 Single/真实填写/Batch 浏览器工作域正在运行。"
                "Batch 不会在另一个正式工作域持有 Makro Browser 时等待或抢占 session lease。"
            )

    def _task_account(self) -> tuple[Any, Path]:
        """Return the account only after its browser runtime identity is committed."""

        task_account_gate = getattr(self.manager, "task_channel_account", None)
        if callable(task_account_gate):
            current = task_account_gate()
        else:
            current = getattr(self.manager, "channel_account", None)
            if current is None:
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
            expected_port = int(store.cdp_port(current))
            if profile_dir != expected or int(self.manager.port) != expected_port:
                raise RuntimeError(
                    "当前 Makro Browser Profile/CDP lane 与已选择平台账号不一致；"
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
        account_id = str(account.account_id)
        self._account_slots[account_id] = (batch, self.controller.config)
        self._slot_store.remember(account_id, batch.root_dir)
        save_batch_run(batch)

    def _assert_batch_account_matches_current(self) -> tuple[Any, Path]:
        account, profile_dir = self._task_account()
        batch = self.controller.batch
        if batch is None or account is None:
            return account, profile_dir

        expected_id = str(getattr(batch, "makro_account_id", "") or "").strip()
        current_id = str(account.account_id)
        if not expected_id and self._starting_account is not None:
            starting_id = str(getattr(self._starting_account, "account_id", "") or "")
            if starting_id != current_id:
                raise RuntimeError(
                    "Batch 启动期间 Makro 店铺发生变化；为防止第一条任务串号，已停止启动。"
                )
            self._stamp_batch_account(batch, account)
            expected_id = current_id

        if not expected_id:
            accounts_getter = getattr(self.manager, "list_channel_accounts", None)
            accounts = tuple(accounts_getter()) if callable(accounts_getter) else (account,)
            if len(accounts) > 1:
                raise RuntimeError(
                    "这个 Batch 创建于任务账号绑定功能启用之前，无法证明它属于哪个 Makro 店铺。"
                    "现在已经连接多个账号，为防止串号，请在目标店铺下重新执行“批量准备”。"
                )
            self._stamp_batch_account(batch, account)
            expected_id = current_id

        if expected_id != current_id:
            label = str(getattr(batch, "makro_account_label", "") or expected_id)
            raise RuntimeError(
                f"这个 Batch 属于 {label}，当前 Makro 店铺是 {account.label}。"
                "程序不会跨店铺复用已准备任务；请切换回原店铺后再执行，或在当前店铺重新准备。"
            )

        changed = False
        for job in batch.jobs:
            job_account_id = str(getattr(job, "makro_account_id", "") or "").strip()
            if not job_account_id:
                job.makro_account_id = current_id
                job.makro_account_label = str(account.label)
                job.touch()
                changed = True
                continue
            if job_account_id != current_id:
                raise RuntimeError(
                    f"{job.job_id} 的 Makro 账号归属与 Batch 不一致；"
                    "为防止商品上架到错误店铺，已停止执行，请重新准备该 Batch。"
                )
        if changed:
            save_batch_run(batch)
        self._account_slots[current_id] = (batch, self.controller.config)
        return account, profile_dir

    def _ensure_start_generation(self, reason: str, port: int) -> None:
        requested_port = int(port)
        if requested_port != int(self.manager.port):
            raise RuntimeError(
                f"Batch Makro CDP {requested_port} 与当前店铺专属端口 {self.manager.port} 不一致。"
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
            runtime._starting_account = account
            try:
                batch = runtime._original_start_prepare(
                    urls,
                    config,
                    prepare_concurrency=requested,
                )
            finally:
                runtime._starting_account = None
            runtime._stamp_batch_account(batch, account)
            assert runtime._owner is not None
            bind_batch_shared_browser(
                batch,
                runtime._owner.browser,
                instance_token=runtime._owner.instance_token,
            )
            runtime._account_slots[str(account.account_id)] = (batch, _controller.config)
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
            config = runtime._ensure_controller_config()
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
            if _controller.config is None:
                runtime._ensure_controller_config()
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
                    "请先切换任务槽位，再接管对应店铺的 Browser lane。"
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
        current_token = self._owner.instance_token

        target = str(getattr(job, "makro_target_id", "") or "")
        stored_token = str(getattr(job, "makro_browser_instance_token", "") or "")
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
        if target and (not stored_token or stored_token != current_token):
            raise RuntimeError(
                f"{job.job_id} 的 Makro Browser generation 已变化，旧 targetId 已失效；"
                "请重新批量准备该店铺任务。"
            )
        bind_job_shared_browser(job, browser, instance_token=current_token)

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
        current_token = self._owner.instance_token
        expected_profile = browser.profile_dir.resolve()
        owned_jobs = [job for job in batch.jobs if str(job.makro_target_id or "")]
        batch_token = str(getattr(batch, "makro_browser_instance_token", "") or "")
        if owned_jobs and (not batch_token or batch_token != current_token):
            raise RuntimeError(
                "这个 Batch 的 Makro Browser generation 已变化，旧 owned targetId 全部失效；"
                "请在该店铺重新批量准备。"
            )

        incompatible: list[str] = []
        for job in owned_jobs:
            port = int(job.makro_cdp_port or 0)
            profile = str(job.makro_profile_dir or "").strip()
            job_token = str(getattr(job, "makro_browser_instance_token", "") or "")
            if (
                port != int(browser.cdp_port)
                or not profile
                or Path(profile).resolve() != expected_profile
                or int(job.browser_lane or 0) != 0
                or not job_token
                or job_token != current_token
            ):
                incompatible.append(str(job.job_id))
        if incompatible:
            raise RuntimeError(
                "这些任务的 Makro Browser ownership/generation 与当前账号不一致，不能安全迁移 targetId；"
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
            f"独立 Edge {port} · {parallelism} Jobs / 1 account transport lane"
        )
        label.setToolTip(
            "当前 Batch 永久绑定创建时的 Makro 店铺、Browser Profile、CDP lane 与 Edge generation。"
            "切换店铺不会覆盖其他账号的 Batch；重启后也会恢复各账号最后一个 Batch。"
            "只有原 Edge generation 仍存活时才会复用 owned targetId。"
        )


def install_batch_parallel_runtime(window: Any) -> BatchParallelRuntime:
    existing = getattr(window, "_batch_parallel_runtime", None)
    if isinstance(existing, BatchParallelRuntime):
        return existing
    runtime = BatchParallelRuntime(window)
    window._batch_parallel_runtime = runtime
    return runtime


__all__ = ["BatchParallelRuntime", "install_batch_parallel_runtime"]
