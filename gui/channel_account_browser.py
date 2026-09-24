from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any

from app.browser_instance import managed_makro_cdp_port
from app.cdp_automation_health import clear_cdp_poison
from app.channel_accounts import ChannelAccount, ChannelAccountStore
from app.update_browser_gate import close_managed_browser

from .browser_session_manager import ManagedMakroBrowser


class AccountBoundMakroBrowser(ManagedMakroBrowser):
    """Bind one managed Makro Edge generation to one runtime/account namespace.

    The existing Single/Batch CDP architecture remains the execution owner. This
    class selects both the runtime-root-specific CDP transport and the persisted
    account profile, preventing two Listing Studio copies from recovering/closing
    the same browser generation and preventing marketplace-session cross-account
    reuse inside one copy.
    """

    _CHANNEL = "makro"

    def __init__(self, window: Any) -> None:
        project_root = Path(window.project_root).resolve()
        managed_port = managed_makro_cdp_port(project_root)
        self._account_scope_id = self._current_scope_id(window)
        self.channel_accounts = ChannelAccountStore(
            project_root,
            scope_id=self._account_scope_id,
        )
        self.channel_account = self.channel_accounts.active_account(self._CHANNEL)
        self._desired_profile_dir = self.channel_accounts.profile_dir(self.channel_account)
        self._runtime_identity = self.channel_accounts.runtime_identity(self.channel_account)
        self._runtime_marker = (
            project_root
            / "channel_accounts"
            / "managed_makro_browser.json"
        )
        self._account_reconcile_lock = threading.Lock()
        self._account_selection_lock = threading.Lock()
        self._pending_channel_account: ChannelAccount | None = None
        self._single_prepared_account_id: str | None = None
        self._single_prepared_invalidated_by_account_switch = False

        self._sync_managed_port_controls(window, managed_port)
        super().__init__(window, port=managed_port)
        # ManagedMakroBrowser schedules its first warm-up with QTimer.singleShot(0),
        # so the account-bound profile is committed before lifecycle I/O can run.
        self.profile_dir = self._desired_profile_dir

    @staticmethod
    def _sync_managed_port_controls(window: Any, port: int) -> None:
        """Make every GUI task producer use this manager's isolated CDP port."""

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

    def task_channel_account(self) -> ChannelAccount:
        """Return the account only after the live browser owner is committed to it.

        Account selection is asynchronous: the lifecycle thread first applies the
        requested metadata, then rotates Edge/Profile, verifies automation and only
        finally writes the runtime identity marker. Task producers must use this
        gate instead of reading ``channel_account`` directly, otherwise they can
        observe the brief middle state where account B is selected while account
        A's Edge is still the live CDP endpoint.
        """

        self._assert_account_scope_stable()
        if self._has_pending_channel_account():
            selected = self.selected_channel_account()
            self.ensure_async()
            raise RuntimeError(
                f"Makro 店铺正在切换到 {selected.label}。状态变为 READY 后再启动任务。"
            )

        expected_profile = self.channel_accounts.profile_dir(self.channel_account).resolve()
        if Path(self.profile_dir).resolve() != expected_profile:
            self.ensure_async()
            raise RuntimeError(
                "当前 Makro Browser Profile 尚未切换到已选择店铺；为防止串号，已拒绝启动任务。"
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
        if self._is_busy():
            raise RuntimeError(
                "当前仍有 Single / Batch / 真实填写任务运行。为防止上架到错误店铺，"
                "任务结束前不能切换 Makro 账号。"
            )

    def request_activate_channel_account(self, account_id: str) -> ChannelAccount:
        """Persist an account selection and let the lifecycle worker rotate Edge.

        This method is safe for the Qt presentation thread: it performs local
        metadata I/O only and never probes CDP, closes Edge, or waits on a worker.
        """

        self._assert_account_change_allowed()
        account = self.channel_accounts.set_active(self._CHANNEL, account_id)
        with self._account_selection_lock:
            if (
                account.account_id == self.channel_account.account_id
                and self._pending_channel_account is None
            ):
                self.ensure_async()
                return account
            self._pending_channel_account = account

        # Any prepared browser-owned state belongs to the previous marketplace
        # account and must never survive a store switch.
        self._single_prepared_generation = None
        self._batch_prepare_generation = None
        if self._single_prepared_account_id is not None:
            self._single_prepared_invalidated_by_account_switch = True
        self._emit_status(
            "CHECKING",
            f"已选择 {account.label} · 正在后台切换独立 Makro 登录会话",
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
        self._runtime_identity = self.channel_accounts.runtime_identity(account)
        self._single_prepared_generation = None
        self._batch_prepare_generation = None
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
        return str(payload.get("identity") or "").strip()

    def _write_runtime_identity(self) -> None:
        payload = {
            "version": 2,
            "identity": self._runtime_identity,
            "channel": self.channel_account.channel,
            "account_id": self.channel_account.account_id,
            "cdp_port": self.port,
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
        """Rotate a live managed Edge only when its recorded account is different.

        This function performs endpoint/process I/O and is called only from the
        inherited lifecycle worker path, never from a Start-click UI path.
        """

        with self._account_reconcile_lock:
            token = self._cdp_instance_token()
            if not token:
                return

            observed_identity = self._read_runtime_identity()
            if observed_identity == self._runtime_identity:
                return

            # Upgrade compatibility: the first account that legitimately claimed
            # the old global profile may reuse the already-running legacy Edge.
            if not observed_identity and self._legacy_profile_is_desired():
                self._write_runtime_identity()
                return

            if self._is_busy():
                raise RuntimeError(
                    "检测到当前 Makro Browser 属于另一个平台账号，但任务仍在运行；"
                    "为防止串号，程序不会中途替换浏览器。"
                )

            self._emit_status(
                "RECOVERING",
                f"{reason} · 正在切换到 {self.channel_account.label} 的独立登录会话",
            )
            closed = close_managed_browser(port=self.port, deadline_s=6.0)
            if not closed.ok:
                self._emit_status(
                    "ERROR",
                    f"平台账号隔离失败：无法安全关闭其他账号的 Makro Browser · {closed.detail}",
                )
                raise RuntimeError(
                    "无法证明并安全关闭当前 Makro Browser；为防止上架到错误店铺，已停止继续。"
                )

            self._instance_token = ""
            self._generation += 1
            self._single_prepared_generation = None
            self._batch_prepare_generation = None
            clear_cdp_poison(self.port)
            self._clear_runtime_identity()

    def ensure_ready(self, reason: str = "task") -> bool:
        """Resolve pending account changes on the lifecycle worker, never the UI."""

        launched_any = False
        while True:
            self._assert_account_scope_stable()
            self._apply_pending_channel_account()
            self._reconcile_running_browser_account(reason)
            launched_any = bool(super().ensure_ready(reason) or launched_any)
            self._write_runtime_identity()
            if not self._has_pending_channel_account():
                return launched_any

    def _apply_endpoint_observation(self, token: str) -> None:
        # A selection can arrive during the tail of a lifecycle worker. Keep the
        # poll path aware of it so a READY emitted by the old generation cannot
        # strand the pending switch indefinitely.
        if self._has_pending_channel_account() and not self._is_busy() and not self._update_quiesced:
            selected = self.selected_channel_account()
            if self._state in self._HOT_STATES:
                self._emit_status(
                    "CHECKING",
                    f"已选择 {selected.label} · 正在后台切换独立 Makro 登录会话",
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
            detail = f"{account.label} · {detail}"
        super()._apply_status(state, detail)


def _install_channel_account_center_if_available(
    window: Any,
    manager: AccountBoundMakroBrowser,
) -> None:
    # CardDetails is installed before the managed browser in the formal GUI. Keep
    # the browser subsystem usable in isolated tests/tools that do not construct
    # presentation components.
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
