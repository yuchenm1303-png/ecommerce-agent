from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any

from app.cdp_automation_health import clear_cdp_poison
from app.channel_accounts import ChannelAccount, ChannelAccountStore
from app.update_browser_gate import close_managed_browser

from .browser_session_manager import ManagedMakroBrowser


class AccountBoundMakroBrowser(ManagedMakroBrowser):
    """Bind one managed Makro Edge lane to one selected marketplace account.

    Every Makro account owns both a dedicated Browser Profile and a persisted CDP
    port. Switching the UI account changes which lane this lifecycle manager is
    observing; it no longer destroys another account's healthy Edge merely to open
    the selected one. Batch presentation/runtime state follows the same account
    switch through BatchParallelRuntime's independent account slots.
    """

    _CHANNEL = "makro"

    def __init__(self, window: Any) -> None:
        project_root = Path(window.project_root).resolve()
        self.project_root = project_root
        self._account_scope_id = self._current_scope_id(window)
        self.channel_accounts = ChannelAccountStore(
            project_root,
            scope_id=self._account_scope_id,
        )
        self.channel_account = self.channel_accounts.active_account(self._CHANNEL)
        managed_port = self.channel_accounts.cdp_port(self.channel_account)
        self._desired_profile_dir = self.channel_accounts.profile_dir(self.channel_account)
        self._runtime_identity = self.channel_accounts.runtime_identity(self.channel_account)
        self._runtime_marker = self._runtime_marker_for(self.channel_account)
        self._account_reconcile_lock = threading.Lock()
        self._account_selection_lock = threading.Lock()
        self._pending_channel_account: ChannelAccount | None = None
        self._single_prepared_account_id: str | None = None
        self._single_prepared_invalidated_by_account_switch = False

        self._sync_managed_port_controls(window, managed_port)
        super().__init__(window, port=managed_port)
        self.profile_dir = self._desired_profile_dir

    @staticmethod
    def _sync_managed_port_controls(window: Any, port: int) -> None:
        """Make every GUI task producer use the currently selected account lane."""

        single_port = getattr(window, "makro_port", None)
        if single_port is not None and callable(getattr(single_port, "setValue", None)):
            single_port.setValue(int(port))

        batch_workspace = getattr(window, "batch_workspace", None)
        batch_port = getattr(batch_workspace, "makro_port", None)
        if batch_port is not None and callable(getattr(batch_port, "setValue", None)):
            batch_port.setValue(int(port))

    @staticmethod
    def _current_scope_id(window: Any) -> str:
        access = getattr(window, "_application_access", None)
        session = getattr(access, "session", None)
        if session is None or not bool(getattr(session, "enforced", False)):
            return "development"
        user_id = str(getattr(session, "user_id", "") or "").strip()
        if not user_id:
            raise RuntimeError("当前应用账号缺少 user_id，不能安全绑定 Makro 平台账号。")
        return user_id

    def _runtime_marker_for(self, account: ChannelAccount) -> Path:
        return (
            self.project_root
            / "channel_accounts"
            / "managed_makro_browsers"
            / self.channel_accounts.scope_token
            / f"{account.account_id}.json"
        )

    def channel_browser_port(self, account: ChannelAccount | None = None) -> int:
        target = account or self.channel_account
        return self.channel_accounts.cdp_port(target)

    def _assert_account_scope_stable(self) -> None:
        current = self._current_scope_id(self.window)
        if current != self._account_scope_id:
            raise RuntimeError(
                "Listing Studio 登录账号已在本次运行中切换。为防止 Makro 店铺串号，"
                "当前平台会话已停止使用；请重新启动程序，让新账号加载自己的 Makro 会话。"
            )

    def list_channel_accounts(self) -> tuple[ChannelAccount, ...]:
        self._assert_account_scope_stable()
        return self.channel_accounts.list_accounts(self._CHANNEL)

    def selected_channel_account(self) -> ChannelAccount:
        with self._account_selection_lock:
            return self._pending_channel_account or self.channel_account

    def channel_browser_status(self) -> tuple[str, str]:
        return self._state, self._detail

    def _single_domain_busy(self) -> bool:
        return bool(
            self.window.runner.is_running
            or self.window.execution_runner.is_running
        )

    def channel_account_change_blocked(self) -> bool:
        return bool(self._update_quiesced or self._single_domain_busy())

    def _is_busy(self) -> bool:
        if self._single_domain_busy():
            return True
        workspace = getattr(self.window, "batch_workspace", None)
        controller = getattr(workspace, "controller", None)
        lane_running = getattr(controller, "account_lane_running", None)
        if callable(lane_running):
            target = self.selected_channel_account()
            return bool(lane_running(target.account_id))
        return bool(workspace is not None and workspace.is_running)

    def begin_update_quiesce(self) -> tuple[bool, str]:
        workspace = getattr(self.window, "batch_workspace", None)
        controller = getattr(workspace, "controller", None)
        any_running = getattr(controller, "any_account_lane_running", None)
        if callable(any_running) and any_running():
            return False, "仍有 Makro 账号的独立 Batch 在运行。请等待所有账号任务结束后再更新。"
        return super().begin_update_quiesce()

    def task_channel_account(self) -> ChannelAccount:
        """Return the account only after the selected browser lane is committed."""

        self._assert_account_scope_stable()
        if self._has_pending_channel_account():
            selected = self.selected_channel_account()
            self.ensure_async()
            raise RuntimeError(
                f"Makro 店铺正在切换到 {selected.label}。状态变为 READY 后再启动任务。"
            )

        expected_profile = self.channel_accounts.profile_dir(self.channel_account).resolve()
        expected_port = self.channel_accounts.cdp_port(self.channel_account)
        if Path(self.profile_dir).resolve() != expected_profile or int(self.port) != expected_port:
            self.ensure_async()
            raise RuntimeError(
                "当前 Makro Browser lane 尚未切换到已选择店铺；为防止串号，已拒绝启动任务。"
            )

        expected_identity = self.channel_accounts.runtime_identity(self.channel_account)
        if self._read_runtime_identity() != expected_identity:
            self.ensure_async()
            raise RuntimeError(
                f"{self.channel_account.label} 的 Makro Browser 会话仍在切换或验证中。"
                "运行时身份确认完成后再启动任务。"
            )
        return self.channel_account

    def _assert_account_change_allowed(self) -> None:
        self._assert_account_scope_stable()
        if self._update_quiesced:
            raise RuntimeError("Listing Studio 正在准备更新，暂时不能切换 Makro 店铺。")
        if self._single_domain_busy():
            raise RuntimeError(
                "当前 Single / 真实填写任务仍在运行。该现场仍属于当前 Makro 店铺，"
                "任务结束前不能切换账号；独立 Batch 不受此限制。"
            )

    def request_activate_channel_account(self, account_id: str) -> ChannelAccount:
        """Validate one account lane, then persist selection and hand it to lifecycle."""

        self._assert_account_change_allowed()
        wanted = str(account_id or "").strip()
        account = next(
            (item for item in self.channel_accounts.list_accounts(self._CHANNEL) if item.account_id == wanted),
            None,
        )
        if account is None:
            raise KeyError(f"unknown {self._CHANNEL} channel account: {wanted}")

        self.channel_accounts.cdp_port(account)
        account = self.channel_accounts.set_active(self._CHANNEL, account.account_id)
        with self._account_selection_lock:
            if (
                account.account_id == self.channel_account.account_id
                and self._pending_channel_account is None
            ):
                self.ensure_async()
                return account
            self._pending_channel_account = account

        self._single_prepared_generation = None
        self._batch_prepare_generation = None
        if self._single_prepared_account_id is not None:
            self._single_prepared_invalidated_by_account_switch = True
        self._emit_status(
            "CHECKING",
            f"已选择 {account.label} · 正在后台连接独立 Makro Browser lane",
        )
        self.ensure_async()
        return account

    def create_and_activate_channel_account(self, *, label: str = "") -> ChannelAccount:
        self._assert_account_change_allowed()
        account = self.channel_accounts.create_account(self._CHANNEL, label=label)
        return self.request_activate_channel_account(account.account_id)

    def _apply_pending_channel_account(self) -> bool:
        with self._account_selection_lock:
            account = self._pending_channel_account
            self._pending_channel_account = None
        if account is None or account.account_id == self.channel_account.account_id:
            return False

        self.channel_account = account
        self._desired_profile_dir = self.channel_accounts.profile_dir(account)
        self.profile_dir = self._desired_profile_dir
        self.port = self.channel_accounts.cdp_port(account)
        self._sync_managed_port_controls(self.window, self.port)
        self._runtime_identity = self.channel_accounts.runtime_identity(account)
        self._runtime_marker = self._runtime_marker_for(account)
        self._instance_token = ""
        self._generation += 1
        self._single_prepared_generation = None
        self._batch_prepare_generation = None

        batch_runtime = getattr(self.window, "_batch_parallel_runtime", None)
        activate_slot = getattr(batch_runtime, "activate_account_slot", None)
        if callable(activate_slot):
            activate_slot(account)
        return True

    def _has_pending_channel_account(self) -> bool:
        with self._account_selection_lock:
            return self._pending_channel_account is not None

    def _read_runtime_identity(self) -> str:
        try:
            payload = json.loads(self._runtime_marker.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return ""
        if not isinstance(payload, dict):
            return ""
        if int(payload.get("cdp_port") or 0) != int(self.port):
            return ""
        return str(payload.get("identity") or "").strip()

    def _write_runtime_identity(self) -> None:
        payload = {
            "version": 3,
            "identity": self._runtime_identity,
            "channel": self.channel_account.channel,
            "account_id": self.channel_account.account_id,
            "cdp_port": self.port,
            "profile_dir": str(Path(self.profile_dir).resolve()),
        }
        self._runtime_marker.parent.mkdir(parents=True, exist_ok=True)
        temp = self._runtime_marker.with_name(
            f".{self._runtime_marker.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        )
        try:
            temp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            os.replace(temp, self._runtime_marker)
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass

    def _clear_runtime_identity(self) -> None:
        try:
            self._runtime_marker.unlink(missing_ok=True)
        except OSError:
            pass

    def _legacy_profile_is_desired(self) -> bool:
        legacy = (self.project_root / "browser_profiles" / "makro-edge").resolve()
        return self.profile_dir.resolve() == legacy

    def _reconcile_running_browser_account(self, reason: str) -> None:
        """Verify the selected account lane without touching other account lanes."""

        with self._account_reconcile_lock:
            token = self._cdp_instance_token()
            if not token:
                return

            observed_identity = self._read_runtime_identity()
            if observed_identity == self._runtime_identity:
                return

            if not observed_identity and self._legacy_profile_is_desired():
                self._write_runtime_identity()
                return

            if self._is_busy():
                raise RuntimeError(
                    "检测到当前账号专属 Makro Browser 端口属于未知会话，但任务仍在运行；"
                    "为防止串号，程序不会中途替换浏览器。"
                )

            self._emit_status(
                "RECOVERING",
                f"{reason} · 正在恢复 {self.channel_account.label} 的专属 Browser lane",
            )
            closed = close_managed_browser(port=self.port, deadline_s=6.0)
            if not closed.ok:
                self._emit_status(
                    "ERROR",
                    f"平台账号隔离失败：无法安全关闭冲突 Makro Browser · {closed.detail}",
                )
                raise RuntimeError(
                    "无法证明并安全关闭当前账号端口上的 Browser；为防止串号，已停止继续。"
                )

            self._instance_token = ""
            self._generation += 1
            self._single_prepared_generation = None
            self._batch_prepare_generation = None
            clear_cdp_poison(self.port)
            self._clear_runtime_identity()

    def ensure_ready(self, reason: str = "task") -> bool:
        """Resolve pending account lane changes on the lifecycle worker."""

        launched_any = False
        while True:
            self._assert_account_scope_stable()
            self._apply_pending_channel_account()
            self._reconcile_running_browser_account(reason)

            if self._is_busy() and not self._single_domain_busy():
                token = self._cdp_instance_token()
                if not token or self._read_runtime_identity() != self._runtime_identity:
                    raise RuntimeError(
                        f"{self.channel_account.label} 的独立 Browser lane 正在运行任务，"
                        "但无法验证其运行时身份；为防止串号，已拒绝切入。"
                    )
                self._observe_instance(token)
                self._emit_status(
                    "READY",
                    f"{self.channel_account.label} 专属 Browser lane 正在执行独立 Batch",
                )
            else:
                launched_any = bool(super().ensure_ready(reason) or launched_any)

            self._write_runtime_identity()
            if not self._has_pending_channel_account():
                return launched_any

    def ensure_async(self) -> None:
        if not self._has_pending_channel_account():
            super().ensure_async()
            return
        if self._update_quiesced or self._single_domain_busy():
            return
        if self._launch_thread is not None and self._launch_thread.is_alive():
            return

        def worker() -> None:
            try:
                self.ensure_ready("Makro account lane switch")
            except Exception:
                pass

        self._launch_thread = threading.Thread(
            target=worker,
            name="managed-makro-account-lane-switch",
            daemon=True,
        )
        self._launch_thread.start()

    def _apply_endpoint_observation(self, token: str) -> None:
        if self._has_pending_channel_account() and not self._update_quiesced:
            selected = self.selected_channel_account()
            if self._state in self._HOT_STATES:
                self._emit_status(
                    "CHECKING",
                    f"已选择 {selected.label} · 正在后台连接专属 Makro Browser lane",
                )
            self.ensure_async()
            return
        super()._apply_endpoint_observation(token)

    def _assert_task_start_allowed(self) -> None:
        self._assert_account_scope_stable()
        if self._has_pending_channel_account():
            selected = self.selected_channel_account()
            self.ensure_async()
            raise RuntimeError(
                f"Makro 店铺正在切换到 {selected.label}。状态变为 READY 后请重新开始任务。"
            )
        super()._assert_task_start_allowed()

    def _start_single(self, config: Any, *, mode: str = "full") -> Any:
        self._single_prepared_account_id = None
        self._single_prepared_invalidated_by_account_switch = False
        return super()._start_single(config, mode=mode)

    def _single_prepared(self, result: Any) -> None:
        super()._single_prepared(result)
        if getattr(result, "plan_summary", None):
            self._single_prepared_account_id = self.channel_account.account_id
            self._single_prepared_invalidated_by_account_switch = False

    def _start_real(self, config: Any) -> Any:
        if self._single_prepared_invalidated_by_account_switch:
            raise RuntimeError(
                "这个 Single 任务是在另一个 Makro 店铺下准备的，且之后发生过账号切换。"
                "旧 Step 3 页面归属已经失效；请在当前店铺重新执行“完整流程准备”后再真实填写。"
            )
        prepared_account_id = str(self._single_prepared_account_id or "")
        if prepared_account_id and prepared_account_id != self.channel_account.account_id:
            raise RuntimeError(
                "当前 Single 准备结果属于另一个 Makro 店铺。"
                "程序不会把旧店铺的准备结果用于当前账号；请重新执行“完整流程准备”。"
            )
        return super()._start_real(config)

    def _apply_status(self, state: str, detail: str) -> None:
        account: ChannelAccount | None = getattr(self, "channel_account", None)
        if account is not None:
            detail = f"{account.label} · CDP {self.port} · {detail}"
        super()._apply_status(state, detail)


def _install_channel_account_center_if_available(
    window: Any,
    manager: AccountBoundMakroBrowser,
) -> None:
    if getattr(window, "_card_details", None) is None:
        return
    from .channel_account_surface import install_channel_account_center

    install_channel_account_center(window, manager)


def install_managed_makro_browser(window: Any) -> AccountBoundMakroBrowser:
    existing = getattr(window, "_managed_makro_browser", None)
    if isinstance(existing, AccountBoundMakroBrowser):
        _install_channel_account_center_if_available(window, existing)
        return existing
    if isinstance(existing, ManagedMakroBrowser):
        raise RuntimeError("Makro Browser 已由非账号绑定的 manager 初始化，拒绝混用两套会话 owner。")

    manager = AccountBoundMakroBrowser(window)
    window._managed_makro_browser = manager
    window._channel_accounts = manager.channel_accounts
    _install_channel_account_center_if_available(window, manager)
    return manager


__all__ = ["AccountBoundMakroBrowser", "install_managed_makro_browser"]
