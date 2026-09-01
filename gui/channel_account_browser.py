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
    """Bind the one managed Makro Edge generation to an explicit channel account.

    The existing Single/Batch CDP architecture remains the execution owner. This
    class only selects which persisted Edge profile that architecture is allowed
    to use and prevents a signed-in Listing Studio account from silently reusing
    another account's marketplace session.
    """

    _CHANNEL = "makro"

    def __init__(self, window: Any) -> None:
        self._account_scope_id = self._current_scope_id(window)
        self.channel_accounts = ChannelAccountStore(
            Path(window.project_root),
            scope_id=self._account_scope_id,
        )
        self.channel_account = self.channel_accounts.active_account(self._CHANNEL)
        self._desired_profile_dir = self.channel_accounts.profile_dir(self.channel_account)
        self._runtime_identity = self.channel_accounts.runtime_identity(self.channel_account)
        self._runtime_marker = (
            Path(window.project_root).resolve()
            / "channel_accounts"
            / "managed_makro_browser.json"
        )
        self._account_reconcile_lock = threading.Lock()

        super().__init__(window)
        # ManagedMakroBrowser schedules its first warm-up with QTimer.singleShot(0),
        # so the account-bound profile is committed before lifecycle I/O can run.
        self.profile_dir = self._desired_profile_dir

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
            "version": 1,
            "identity": self._runtime_identity,
            "channel": self.channel_account.channel,
            "account_id": self.channel_account.account_id,
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
        self._assert_account_scope_stable()
        self._reconcile_running_browser_account(reason)
        launched = super().ensure_ready(reason)
        self._write_runtime_identity()
        return launched

    def _assert_task_start_allowed(self) -> None:
        self._assert_account_scope_stable()
        super()._assert_task_start_allowed()

    def _apply_status(self, state: str, detail: str) -> None:
        account: ChannelAccount | None = getattr(self, "channel_account", None)
        if account is not None:
            detail = f"{account.label} · {detail}"
        super()._apply_status(state, detail)


def install_managed_makro_browser(window: Any) -> AccountBoundMakroBrowser:
    existing = getattr(window, "_managed_makro_browser", None)
    if isinstance(existing, AccountBoundMakroBrowser):
        return existing
    if isinstance(existing, ManagedMakroBrowser):
        raise RuntimeError("Makro Browser 已由非账号绑定的 manager 初始化，拒绝混用两套会话 owner。")

    manager = AccountBoundMakroBrowser(window)
    window._managed_makro_browser = manager
    window._channel_accounts = manager.channel_accounts
    return manager


__all__ = ["AccountBoundMakroBrowser", "install_managed_makro_browser"]
