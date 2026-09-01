from __future__ import annotations

from typing import Protocol

from ..scope import CommerceScope
from .domain import ChannelListing


class ChannelListingRepository(Protocol):
    def get(self, scope: CommerceScope, listing_id: str) -> ChannelListing | None:
        ...

    def find_for_variant_account(
        self,
        scope: CommerceScope,
        *,
        variant_id: str,
        channel: str,
        channel_account_id: str,
    ) -> tuple[ChannelListing, ...]:
        ...

    def add(self, listing: ChannelListing) -> None:
        ...

    def save(self, listing: ChannelListing) -> None:
        ...


__all__ = ["ChannelListingRepository"]
