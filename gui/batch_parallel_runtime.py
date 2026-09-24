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

    One BatchController now carries independent account lanes. The selected lane
    owns the visible workspace while QProcess callbacks re-enter their originating
    account lane, so shop A can keep preparing/executing after the user switches to
    shop B. Each account also keeps its own CDP lease/transport owner; only the
    shared supplier Source Edge remains globally serialized.
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

        self._owners: dict[str, BatchSharedBrowserOwner] = {}
        self._parallelism_by_account: dict[str, int] = {}
        self._starting_accounts: dict[str, Any] = {}
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
        lane_running = getattr(self.controller, "lane_running_changed", None)
        lane_state = getattr(self.controller, "lane_state_changed", None)
        if lane_running is not None:
            lane_running.connect(
                lambda account_id, _running: self._release_if_safe(str(account_id))
            )
        else:
            self.controller.running_changed.connect(lambda _running: self._release_if_safe())
        if lane_state is not None:
            lane_state.connect(
                lambda account_id, _snapshot: self._release_if_safe(str(account_id))
            )
        else:
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
        """Return presentation-safe state for one account's independent Batch lane."""

        account_key = str(account_id or "").strip()
        if not account_key:
            raise ValueError("Makro account_id must not be empty")
        snapshot_getter = getattr(self.controller, "account_lane_snapshot", None)
        if callable(snapshot_getter):
            return dict(snapshot_getter(account_key))

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
        return {
            "account_id": account_key,
            "has_batch": True,
            "batch_id": str(getattr(batch, "batch_id", "") or ""),
            "status": str(getattr(batch, "status", "") or "IDLE").upper(),
            "running": False,
            "summary": batch.summary(),
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
            account_id = str(account.account_id)
            self._account_slots[account_id] = (batch, None)
            install_lane = getattr(self.controller, "install_account_lane", None)
            if callable(install_lane):
                install_lane(account_id, batch=batch, config=None)

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
        if not self.controller.is_running:
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
        """Select one account lane without stopping work owned by other accounts."""

        self._remember_current_slot()
        account_id = str(getattr(account, "account_id", "") or "").strip()
        if not account_id:
            raise RuntimeError("目标 Makro 账号缺少 account_id，不能恢复任务槽位。")

        slot = self._account_slots.get(account_id)
        install_lane = getattr(self.controller, "install_account_lane", None)
        snapshot_getter = getattr(self.controller, "account_lane_snapshot", None)
        if slot is not None and callable(install_lane) and callable(snapshot_getter):
            snapshot = snapshot_getter(account_id)
            if not bool(snapshot.get("has_batch")):
                batch, config = slot
                stored_id = str(getattr(batch, "makro_account_id", "") or "").strip()
                if stored_id != account_id:
                    raise RuntimeError("Makro Batch 槽位账号归属损坏；为防止串号，已拒绝恢复。")
                install_lane(account_id, batch=batch, config=config)

        activate_lane = getattr(self.controller, "activate_account_lane", None)
        if callable(activate_lane):
            activate_lane(account_id)
            self._parallelism_by_account.setdefault(account_id, 0)
            return

        if self.controller.is_running:
            raise RuntimeError("Batch 仍在运行，旧控制器不能切换 Makro 任务槽位。")
        batch, config = slot if slot is not None else (None, None)
        self.controller.batch = batch
        self.controller.config = config

    def _assert_top_level_idle(self) -> None:
        if self.manager.update_quiesced:
            raise RuntimeError("Listing Studio 正在准备更新，暂时不能启动新的 Batch。")
        if self.manager.is_busy():
            raise RuntimeError(
                "已有 Single/真实填写/Batch 浏览器工作域正在运行。"
                "Batch 不会在另一个正式工作域持有 Makro Browser 时等待或抢占 session lease。"
            )

    def _current_lane_id(self) -> str:
        getter = getattr(self.controller, "current_account_lane", None)
        if callable(getter):
            return str(getter() or "").strip()
        current = getattr(self.manager, "channel_account", None)
        return str(getattr(current, "account_id", "") or "").strip()

    def _account_for_lane(self, account_id: str) -> Any:
        wanted = str(account_id or "").strip()
        for account in self.manager.list_channel_accounts():
            if str(account.account_id) == wanted:
                return account
        raise RuntimeError(f"Makro account lane 不存在：{wanted}")

    def _task_account(self) -> tuple[Any, Path]:
        """Resolve the account owned by the current scheduler callback lane."""

        lane_id = self._current_lane_id()
        current_manager = getattr(self.manager, "channel_account", None)
        if not lane_id and current_manager is not None:
            lane_id = str(current_manager.account_id)

        account = self._account_for_lane(lane_id)
        manager_id = str(getattr(current_manager, "account_id", "") or "")
        selected_getter = getattr(self.manager, "selected_channel_account", None)
        selected = selected_getter() if callable(selected_getter) else current_manager
        selected_id = str(getattr(selected, "account_id", "") or "")

        if lane_id == manager_id == selected_id:
            gate = getattr(self.manager, "task_channel_account", None)
            if callable(gate):
                account = gate()

        store = self.manager.channel_accounts
        profile_dir = Path(store.profile_dir(account)).resolve()
        expected_port = int(store.cdp_port(account))
        config = self.controller.config
        if config is not None and int(config.makro_cdp_port) != expected_port:
            raise RuntimeError(
                f"{account.label} 的 Batch 配置 CDP {config.makro_cdp_port} 与账号专属端口 "
                f"{expected_port} 不一致；为防止串号，已拒绝继续。"
            )
        if lane_id == manager_id:
            if Path(self.manager.profile_dir).resolve() != profile_dir or int(self.manager.port) != expected_port:
                raise RuntimeError(
                    "当前 Makro Browser Profile/CDP lane 与已选择平台账号不一致；"
                    "为防止串号，已拒绝创建 Batch。"
                )
        return account, profile_dir

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
        starting_account = self._starting_accounts.get(self._current_lane_id())
        if not expected_id and starting_account is not None:
            starting_id = str(getattr(starting_account, "account_id", "") or "")
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
        account, _profile_dir = self._task_account()
        expected_port = int(self.manager.channel_accounts.cdp_port(account))
        if requested_port != expected_port:
            raise RuntimeError(
                f"Batch Makro CDP {requested_port} 与 {account.label} 专属端口 "
                f"{expected_port} 不一致。"
            )
        if poison_matches_current_generation(requested_port) or not is_cdp_ready(
            requested_port,
            timeout_s=0.25,
        ):
            if str(account.account_id) != str(self.manager.channel_account.account_id):
                raise RuntimeError(
                    f"{account.label} 的后台 Browser lane 已离线；为保护其他账号任务，不会跨账号重启。"
                )
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
            runtime._parallelism_by_account[str(account.account_id)] = min(
                requested,
                max(1, len(urls)),
            )
            port = int(config.makro_cdp_port)
            runtime._ensure_start_generation("Batch preparation", port)
            runtime._ensure_owner(port, profile_dir=profile_dir)
            runtime._starting_accounts[runtime._current_lane_id()] = account
            try:
                batch = runtime._original_start_prepare(
                    urls,
                    config,
                    prepare_concurrency=requested,
                )
            finally:
                runtime._starting_accounts.pop(runtime._current_lane_id(), None)
            runtime._stamp_batch_account(batch, account)
            owner = runtime._owners.get(str(account.account_id))
            if owner is None:
                raise RuntimeError("Makro account transport owner was not acquired")
            bind_batch_shared_browser(
                batch,
                owner.browser,
                instance_token=owner.instance_token,
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
            runtime._parallelism_by_account[str(_account.account_id)] = normalize_batch_concurrency(
                execute_concurrency
            )
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
        account, account_profile = self._task_account()
        account_id = str(account.account_id)
        requested_port = int(port)
        requested_profile = Path(
            profile_dir if profile_dir is not None else account_profile
        ).resolve()
        owner = self._owners.get(account_id)
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
                    f"{account.label} 的 Batch 已绑定 Makro targetId，不能中途更换自己的 "
                    "Profile/CDP lane；请重新批量准备。"
                )
            owner.release()
            self._owners.pop(account_id, None)

        browser = shared_batch_browser(
            self.project_root,
            cdp_port=requested_port,
            profile_dir=requested_profile,
        )
        owner = BatchSharedBrowserOwner(browser)
        owner.acquire()
        self._owners[account_id] = owner

    def _ensure_job_binding(self, job: Any) -> None:
        config = self.controller.config
        if config is None:
            raise RuntimeError("Batch browser binding requires runtime config")
        _account, profile_dir = self._assert_batch_account_matches_current()
        self._ensure_owner(int(config.makro_cdp_port), profile_dir=profile_dir)
        account, _ = self._assert_batch_account_matches_current()
        owner = self._owners.get(str(account.account_id))
        if owner is None:
            raise RuntimeError("Batch browser binding requires account transport owner")
        browser = owner.browser
        current_token = owner.instance_token

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
        account, _profile_dir = self._assert_batch_account_matches_current()
        owner = self._owners.get(str(account.account_id))
        if owner is None or not owner.acquired:
            if any(str(job.makro_target_id or "") for job in batch.jobs):
                raise RuntimeError(
                    "这个 Batch 没有自己账号的 browser session owner；请重新批量准备。"
                )
            return

        owner.assert_alive()
        browser = owner.browser
        current_token = owner.instance_token
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

    def _release_if_safe(self, account_id: str | None = None) -> None:
        lane = str(account_id or self._current_lane_id()).strip()
        if not lane:
            return
        lane_context = getattr(self.controller, "account_lane", None)
        if callable(lane_context):
            with lane_context(lane):
                if self.controller.is_running:
                    return
                batch = self.controller.batch
                if batch is None or not batch.jobs:
                    self._release_owner(lane)
                    return
                if str(batch.status) in {"COMPLETE", "STOPPED"}:
                    self._release_owner(lane)
            return

        if self.controller.is_running:
            return
        batch = self.controller.batch
        if batch is None or not batch.jobs or str(batch.status) in {"COMPLETE", "STOPPED"}:
            self._release_owner(lane)

    def _release_owner(self, account_id: str | None = None) -> None:
        if account_id is None:
            owners = tuple(self._owners.values())
            self._owners.clear()
            for owner in owners:
                owner.release()
            return
        owner = self._owners.pop(str(account_id), None)
        if owner is not None:
            owner.release()

    def _decorate_batch_status(self, state: str, detail: str) -> None:
        label = getattr(self.manager, "_batch_label", None)
        account = getattr(self.manager, "channel_account", None)
        account_id = str(getattr(account, "account_id", "") or "")
        owner = self._owners.get(account_id)
        if label is None or owner is None:
            return
        port = int(owner.browser.cdp_port)
        parallelism = max(1, int(self._parallelism_by_account.get(account_id) or 1))
        account_label = str(getattr(account, "label", "") or "当前店铺")
        label.setText(
            f"Makro Browser · {state} · {account_label} · {detail} · "
            f"独立 Edge {port} · {parallelism} Jobs / 1 account transport lane"
        )
        label.setToolTip(
            "当前 Batch 永久绑定创建时的 Makro 店铺、Browser Profile、CDP lane 与 Edge generation。"
            "不同 Makro 账号拥有独立 scheduler lane / Browser owner，可以同时运行；"
            "共享的供应商 Source Edge 仍按顺序串行采集。重启后会恢复各账号最后一个 Batch。"
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
