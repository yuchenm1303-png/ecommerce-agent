from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence


Record = dict[str, Any]
RecordFilters = Mapping[str, Any]


class CommerceRecordGateway(Protocol):
    """Minimal trusted persistence gateway used by Commerce repositories.

    The domain intentionally does not depend on ``supabase-py`` or PostgREST.
    Implementations may use Supabase, direct PostgreSQL or an in-memory test
    store, but they must execute in a trusted server-side/transactional context.
    A packaged desktop client must never satisfy this protocol with a Supabase
    service-role credential.
    """

    def get_one(self, table: str, filters: RecordFilters) -> Record | None:
        ...

    def get_many(self, table: str, filters: RecordFilters) -> tuple[Record, ...]:
        ...

    def insert(self, table: str, row: Mapping[str, Any]) -> None:
        ...

    def upsert(
        self,
        table: str,
        row: Mapping[str, Any],
        *,
        conflict_columns: Sequence[str],
    ) -> None:
        ...


__all__ = ["CommerceRecordGateway", "Record", "RecordFilters"]
