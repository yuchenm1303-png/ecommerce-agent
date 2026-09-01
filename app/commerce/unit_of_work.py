from __future__ import annotations

from abc import ABC, abstractmethod

from .catalog.repository import ProductRepository, SourceProductRepository
from .events import CommerceEventSink
from .listings.repository import ChannelListingRepository


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
        self._committed = False
        self._entered = False

    def __enter__(self) -> "CommerceUnitOfWork":
        if self._entered:
            raise RuntimeError("commerce unit of work is already active")
        self._entered = True
        self._committed = False
        self._begin()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        try:
            if exc_type is not None or not self._committed:
                self._rollback()
        finally:
            self._entered = False
        return False

    def commit(self) -> None:
        if not self._entered:
            raise RuntimeError("commerce unit of work is not active")
        if self._committed:
            raise RuntimeError("commerce unit of work has already committed")
        self._commit()
        self._committed = True

    def rollback(self) -> None:
        if not self._entered:
            raise RuntimeError("commerce unit of work is not active")
        if not self._committed:
            self._rollback()
        self._committed = False

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
