from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum

from ..scope import CommerceScope


_CHANNEL_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _required(value: str, *, field_name: str, max_length: int) -> str:
    normalized = " ".join(str(value or "").strip().split())
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    if len(normalized) > max_length:
        raise ValueError(f"{field_name} is too long")
    return normalized


def _normalize_channel(value: str) -> str:
    normalized = str(value or "").strip().casefold()
    if not _CHANNEL_RE.fullmatch(normalized):
        raise ValueError(f"invalid channel name: {value!r}")
    return normalized


class ChannelAccountStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


@dataclass(frozen=True, slots=True)
class ChannelAccountIdentity:
    """Server-side marketplace account identity.

    This is deliberately not a browser-session model.  Passwords, cookies,
    access tokens, API keys and local browser profile keys never belong here.
    Those runtime credentials stay behind their dedicated secure/session layer.
    """

    channel_account_id: str
    scope: CommerceScope
    channel: str
    label: str
    status: ChannelAccountStatus = ChannelAccountStatus.ACTIVE
    created_at: datetime = field(default_factory=_now_utc)
    updated_at: datetime = field(default_factory=_now_utc)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "channel_account_id",
            _required(self.channel_account_id, field_name="channel_account_id", max_length=200),
        )
        object.__setattr__(self, "channel", _normalize_channel(self.channel))
        object.__setattr__(
            self,
            "label",
            _required(self.label, field_name="label", max_length=120),
        )
        object.__setattr__(self, "status", ChannelAccountStatus(self.status))

    @classmethod
    def register(
        cls,
        *,
        scope: CommerceScope,
        channel: str,
        label: str,
        channel_account_id: str = "",
    ) -> "ChannelAccountIdentity":
        return cls(
            channel_account_id=(channel_account_id.strip() or uuid.uuid4().hex),
            scope=scope,
            channel=channel,
            label=label,
        )

    def archive(self) -> "ChannelAccountIdentity":
        if self.status is ChannelAccountStatus.ARCHIVED:
            return self
        return replace(
            self,
            status=ChannelAccountStatus.ARCHIVED,
            updated_at=_now_utc(),
        )


__all__ = ["ChannelAccountIdentity", "ChannelAccountStatus"]
