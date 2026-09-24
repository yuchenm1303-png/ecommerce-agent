from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from .scope import CommerceScope


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


class CommerceEvent(Protocol):
    event_id: str
    scope: CommerceScope
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class SourceProductBound:
    scope: CommerceScope
    source_product_id: str
    product_id: str
    variant_id: str
    request_identity: str
    binding_method: str
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    occurred_at: datetime = field(default_factory=_now_utc)


@dataclass(frozen=True, slots=True)
class ChannelListingRecorded:
    scope: CommerceScope
    listing_id: str
    product_id: str
    variant_id: str
    channel: str
    channel_account_id: str
    external_listing_id: str
    status: str
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    occurred_at: datetime = field(default_factory=_now_utc)


class CommerceEventSink(Protocol):
    def append(self, event: CommerceEvent) -> None:
        ...


__all__ = [
    "ChannelListingRecorded",
    "CommerceEvent",
    "CommerceEventSink",
    "SourceProductBound",
]
