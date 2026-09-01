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


def _required(value: str, *, field_name: str, max_length: int = 500) -> str:
    normalized = " ".join(str(value or "").strip().split())
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    if len(normalized) > max_length:
        raise ValueError(f"{field_name} is too long")
    return normalized


def _optional(value: str, *, max_length: int = 500) -> str:
    normalized = " ".join(str(value or "").strip().split())
    if len(normalized) > max_length:
        raise ValueError("value is too long")
    return normalized


class ListingStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    INACTIVE = "inactive"
    REMOVED = "removed"


@dataclass(frozen=True, slots=True)
class ChannelListing:
    """One marketplace listing for one internal variant and channel account.

    Runtime task success/failure is intentionally absent.  A failed automation
    task does not make a product or listing a failed business entity.
    """

    listing_id: str
    scope: CommerceScope
    product_id: str
    variant_id: str
    channel: str
    channel_account_id: str
    status: ListingStatus = ListingStatus.DRAFT
    external_listing_id: str = ""
    created_at: datetime = field(default_factory=_now_utc)
    updated_at: datetime = field(default_factory=_now_utc)

    def __post_init__(self) -> None:
        object.__setattr__(self, "listing_id", _required(self.listing_id, field_name="listing_id", max_length=200))
        object.__setattr__(self, "product_id", _required(self.product_id, field_name="product_id", max_length=200))
        object.__setattr__(self, "variant_id", _required(self.variant_id, field_name="variant_id", max_length=200))
        channel = str(self.channel or "").strip().casefold()
        if not _CHANNEL_RE.fullmatch(channel):
            raise ValueError(f"invalid channel name: {self.channel!r}")
        object.__setattr__(self, "channel", channel)
        object.__setattr__(
            self,
            "channel_account_id",
            _required(self.channel_account_id, field_name="channel_account_id", max_length=200),
        )
        status = ListingStatus(self.status)
        object.__setattr__(self, "status", status)
        external_id = _optional(self.external_listing_id, max_length=300)
        object.__setattr__(self, "external_listing_id", external_id)
        if status is ListingStatus.ACTIVE and not external_id:
            raise ValueError("active listing requires external_listing_id")

    @classmethod
    def draft(
        cls,
        *,
        scope: CommerceScope,
        product_id: str,
        variant_id: str,
        channel: str,
        channel_account_id: str,
    ) -> "ChannelListing":
        return cls(
            listing_id=uuid.uuid4().hex,
            scope=scope,
            product_id=product_id,
            variant_id=variant_id,
            channel=channel,
            channel_account_id=channel_account_id,
        )

    def mark_active(self, *, external_listing_id: str) -> "ChannelListing":
        return replace(
            self,
            status=ListingStatus.ACTIVE,
            external_listing_id=external_listing_id,
            updated_at=_now_utc(),
        )

    def mark_inactive(self) -> "ChannelListing":
        return replace(self, status=ListingStatus.INACTIVE, updated_at=_now_utc())

    def mark_removed(self) -> "ChannelListing":
        return replace(self, status=ListingStatus.REMOVED, updated_at=_now_utc())


__all__ = ["ChannelListing", "ListingStatus"]
