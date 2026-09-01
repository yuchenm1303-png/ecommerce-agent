from __future__ import annotations

from typing import Protocol

from ..scope import CommerceScope
from .domain import ChannelAccountIdentity


class ChannelAccountRepository(Protocol):
    """Persistence boundary for server-side marketplace account identities."""

    def get(
        self,
        scope: CommerceScope,
        channel_account_id: str,
    ) -> ChannelAccountIdentity | None:
        ...

    def list_for_channel(
        self,
        scope: CommerceScope,
        channel: str,
    ) -> tuple[ChannelAccountIdentity, ...]:
        ...

    def add(self, account: ChannelAccountIdentity) -> None:
        ...

    def save(self, account: ChannelAccountIdentity) -> None:
        ...


__all__ = ["ChannelAccountRepository"]
