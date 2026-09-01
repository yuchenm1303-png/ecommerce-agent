from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum

from .catalog.repository import ProductRepository, SourceProductRepository
from .events import CommerceEventSink
from .listings.repository import ChannelListingRepository


class _UnitOfWorkState(str, Enum):
    IDLE = "idle"
    ACTIVE = "active"
    COMMITTED = "committed"
    ROLLED_BACK = "rolled_back"


class CommerceUnitOfWork(ABC):
    """Atomic boundary for future commerce persistence implementations.

    A unit of work never auto-commits on a clean context-manager exit.  Callers
    must explicitly commit; otherwise rollback is guaranteed.  That makes future
    shadow writers fail closed without changing the existing listing runtime.
    """

    products: ProductRepository
    source_products: SourceProductRepository
    listings: ChannelListingRepository
    events: CommerceEventSink

    def __init__(self) -> None:
        self._state = _UnitOfWorkState.IDLE

    def __enter__(self) -> "CommerceUnitOfWork":
        if self._state is not _UnitOfWorkState.IDLE:
            raise RuntimeError("commerce unit of work is already active")
        self._begin()
        self._state = _UnitOfWorkState.ACTIVE
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        try:
            if self._state is _UnitOfWorkState.ACTIVE:
                self._rollback()
                self._state = _UnitOfWorkState.ROLLED_BACK
        finally:
            self._state = _UnitOfWorkState.IDLE
        return False

    def commit(self) -> None:
        self._require_active()
        self._commit()
        self._state = _UnitOfWorkState.COMMITTED

    def rollback(self) -> None:
        self._require_active()
        self._rollback()
        self._state = _UnitOfWorkState.ROLLED_BACK

    def _require_active(self) -> None:
        if self._state is not _UnitOfWorkState.ACTIVE:
            raise RuntimeError("commerce unit of work is not active")

    @abstractmethod
    def _begin(self) -> None:
        ...

    @abstractmethod
    def _commit(self) -> None:
        ...

    @abstractmethod
    def _rollback(self) -> None:
        ...


__all__ = ["CommerceUnitOfWork"]
