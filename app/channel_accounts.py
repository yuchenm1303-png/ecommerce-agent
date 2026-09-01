from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_STATE_VERSION = 1
_CHANNEL_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_DEFAULT_MAKRO_LABEL = "Makro 默认账号"
_LEGACY_MAKRO_PROFILE_KEY = "legacy-makro-edge"


@dataclass(frozen=True, slots=True)
class ChannelAccount:
    """A platform account identity owned by one application account scope.

    Credentials are deliberately not part of this model. Browser-based channels
    keep their authenticated session in the browser profile selected by
    ``profile_key``; API/OAuth credentials can be added later through a dedicated
    secure credential store without changing Product/Listing ownership semantics.
    """

    account_id: str
    channel: str
    label: str
    profile_key: str
    created_at: float


class ChannelAccountStore:
    """Local channel-account metadata scoped to the signed-in application account.

    This store contains identity and selection metadata only. It never stores a
    marketplace password, cookie, access token, API key, or OAuth secret.
    """

    def __init__(self, runtime_root: Path, *, scope_id: str) -> None:
        self.runtime_root = Path(runtime_root).resolve()
        self.scope_id = str(scope_id or "").strip()
        if not self.scope_id:
            raise ValueError("channel account scope_id must not be empty")

        self.scope_token = hashlib.sha256(self.scope_id.encode("utf-8")).hexdigest()[:24]
        self.state_root = self.runtime_root / "channel_accounts"
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.state_path = self.state_root / f"{self.scope_token}.json"
        self._state = self._load_state()

    @staticmethod
    def _normalize_channel(channel: str) -> str:
        value = str(channel or "").strip().casefold()
        if not _CHANNEL_RE.fullmatch(value):
            raise ValueError(f"invalid channel name: {channel!r}")
        return value

    @staticmethod
    def _normalize_label(label: str, *, fallback: str) -> str:
        value = " ".join(str(label or "").strip().split())
        return (value or fallback)[:80]

    def _empty_state(self) -> dict[str, Any]:
        return {"version": _STATE_VERSION, "accounts": [], "active": {}}

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return self._empty_state()
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"平台账号配置无法读取，已停止使用以避免串号：{self.state_path}"
            ) from exc
        if not isinstance(payload, dict) or int(payload.get("version") or 0) != _STATE_VERSION:
            raise RuntimeError("平台账号配置版本无效，已停止使用以避免串号。")
        if not isinstance(payload.get("accounts"), list) or not isinstance(payload.get("active"), dict):
            raise RuntimeError("平台账号配置结构无效，已停止使用以避免串号。")
        return payload

    @staticmethod
    def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        try:
            temp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            os.replace(temp, path)
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass

    def _save(self) -> None:
        self._atomic_write_json(self.state_path, self._state)

    def _rows(self) -> list[dict[str, Any]]:
        return [row for row in self._state["accounts"] if isinstance(row, dict)]

    @staticmethod
    def _from_row(row: dict[str, Any]) -> ChannelAccount:
        return ChannelAccount(
            account_id=str(row.get("account_id") or ""),
            channel=str(row.get("channel") or ""),
            label=str(row.get("label") or ""),
            profile_key=str(row.get("profile_key") or ""),
            created_at=float(row.get("created_at") or 0.0),
        )

    def list_accounts(self, channel: str) -> tuple[ChannelAccount, ...]:
        normalized = self._normalize_channel(channel)
        return tuple(
            self._from_row(row)
            for row in self._rows()
            if str(row.get("channel") or "") == normalized
        )

    def _default_account_id(self, channel: str) -> str:
        return uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"listing-studio:{self.scope_token}:{channel}:default",
        ).hex

    def _legacy_makro_claim_path(self) -> Path:
        return self.state_root / "legacy_makro_profile_owner.json"

    def _claim_legacy_makro_profile(self, account_id: str) -> bool:
        legacy_dir = self.runtime_root / "browser_profiles" / "makro-edge"
        if not legacy_dir.exists():
            return False

        claim_path = self._legacy_makro_claim_path()
        claim = {
            "version": 1,
            "scope_token": self.scope_token,
            "account_id": account_id,
        }
        try:
            with claim_path.open("x", encoding="utf-8") as handle:
                json.dump(claim, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.flush()
                try:
                    os.fsync(handle.fileno())
                except OSError:
                    pass
            return True
        except FileExistsError:
            pass
        except OSError:
            return False

        try:
            existing = json.loads(claim_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return False
        return bool(
            isinstance(existing, dict)
            and existing.get("scope_token") == self.scope_token
            and existing.get("account_id") == account_id
        )

    def _bootstrap_default(self, channel: str) -> ChannelAccount:
        account_id = self._default_account_id(channel)
        profile_key = account_id
        label = f"{channel.title()} 默认账号"
        if channel == "makro":
            label = _DEFAULT_MAKRO_LABEL
            if self._claim_legacy_makro_profile(account_id):
                profile_key = _LEGACY_MAKRO_PROFILE_KEY

        row = {
            "account_id": account_id,
            "channel": channel,
            "label": label,
            "profile_key": profile_key,
            "created_at": time.time(),
        }
        self._state["accounts"].append(row)
        self._state["active"][channel] = account_id
        self._save()
        return self._from_row(row)

    def active_account(self, channel: str) -> ChannelAccount:
        normalized = self._normalize_channel(channel)
        accounts = self.list_accounts(normalized)
        if not accounts:
            return self._bootstrap_default(normalized)

        active_id = str(self._state["active"].get(normalized) or "")
        for account in accounts:
            if account.account_id == active_id:
                return account

        account = accounts[0]
        self._state["active"][normalized] = account.account_id
        self._save()
        return account

    def create_account(self, channel: str, *, label: str = "") -> ChannelAccount:
        normalized = self._normalize_channel(channel)
        account_id = uuid.uuid4().hex
        fallback = f"{normalized.title()} 账号 {len(self.list_accounts(normalized)) + 1}"
        row = {
            "account_id": account_id,
            "channel": normalized,
            "label": self._normalize_label(label, fallback=fallback),
            "profile_key": account_id,
            "created_at": time.time(),
        }
        self._state["accounts"].append(row)
        self._save()
        return self._from_row(row)

    def set_active(self, channel: str, account_id: str) -> ChannelAccount:
        normalized = self._normalize_channel(channel)
        wanted = str(account_id or "").strip()
        for account in self.list_accounts(normalized):
            if account.account_id == wanted:
                self._state["active"][normalized] = wanted
                self._save()
                return account
        raise KeyError(f"unknown {normalized} channel account: {wanted}")

    def profile_dir(self, account: ChannelAccount) -> Path:
        if account.channel == "makro" and account.profile_key == _LEGACY_MAKRO_PROFILE_KEY:
            return (self.runtime_root / "browser_profiles" / "makro-edge").resolve()
        return (
            self.runtime_root
            / "browser_profiles"
            / "channels"
            / account.channel
            / account.profile_key
        ).resolve()

    def runtime_identity(self, account: ChannelAccount) -> str:
        return f"{self.scope_token}:{account.channel}:{account.account_id}"


__all__ = ["ChannelAccount", "ChannelAccountStore"]
