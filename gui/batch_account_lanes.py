from __future__ import annotations

from collections.abc import Iterator, MutableMapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Callable


DEFAULT_BATCH_ACCOUNT_LANE = "__default__"


def _normalize_lane_key(value: object) -> str:
    key = str(value or "").strip()
    return key or DEFAULT_BATCH_ACCOUNT_LANE


@dataclass(slots=True)
class BatchAccountLaneState:
    """Mutable Batch scheduler state owned by exactly one Makro account."""

    batch: Any = None
    config: Any = None
    mode: str = "idle"
    source_queue: list[str] = field(default_factory=list)
    prepare_queue: list[str] = field(default_factory=list)
    execute_queue: list[str] = field(default_factory=list)
    source_resume_pending: set[str] = field(default_factory=set)
    stopping: bool = False
    execution_images: bool = False
    last_state: str = "Idle · 等待批量链接"
    extras: dict[str, Any] = field(default_factory=dict)


class BatchAccountLaneRegistry:
    """Select and temporarily enter independent scheduler lanes.

    The selected lane is the one rendered by the GUI. A ContextVar lets async
    QProcess callbacks temporarily re-enter the account that owns that process,
    so a background shop can keep progressing after the user switches the UI to
    another Makro account.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._states: dict[str, BatchAccountLaneState] = {
            DEFAULT_BATCH_ACCOUNT_LANE: BatchAccountLaneState()
        }
        self._selected = DEFAULT_BATCH_ACCOUNT_LANE
        self._context: ContextVar[str | None] = ContextVar(
            "makro_batch_account_lane",
            default=None,
        )

    def ensure(self, account_id: object) -> BatchAccountLaneState:
        key = _normalize_lane_key(account_id)
        with self._lock:
            state = self._states.get(key)
            if state is None:
                state = BatchAccountLaneState()
                self._states[key] = state
            return state

    def keys(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._states)

    def selected_key(self) -> str:
        with self._lock:
            return self._selected

    def current_key(self) -> str:
        contextual = self._context.get()
        return _normalize_lane_key(contextual if contextual is not None else self.selected_key())

    def state(self, account_id: object | None = None) -> BatchAccountLaneState:
        key = self.current_key() if account_id is None else _normalize_lane_key(account_id)
        return self.ensure(key)

    def select(self, account_id: object) -> BatchAccountLaneState:
        key = _normalize_lane_key(account_id)
        state = self.ensure(key)
        with self._lock:
            self._selected = key
        return state

    @contextmanager
    def use(self, account_id: object):
        key = _normalize_lane_key(account_id)
        self.ensure(key)
        token = self._context.set(key)
        try:
            yield self._states[key]
        finally:
            self._context.reset(token)


class LaneProcessMap(MutableMapping[Any, tuple[str, str]]):
    """A mapping that exposes only processes owned by the current account lane."""

    def __init__(self, lane_key: Callable[[], str]) -> None:
        self._lane_key = lane_key
        self._items: dict[Any, tuple[str, str]] = {}
        self._owners: dict[Any, str] = {}

    def owner_for(self, process: Any) -> str | None:
        return self._owners.get(process)

    def all_items(self) -> tuple[tuple[Any, tuple[str, str]], ...]:
        return tuple(self._items.items())

    def __getitem__(self, process: Any) -> tuple[str, str]:
        if self._owners.get(process) != self._lane_key():
            raise KeyError(process)
        return self._items[process]

    def __setitem__(self, process: Any, value: tuple[str, str]) -> None:
        lane = self._lane_key()
        existing = self._owners.get(process)
        if existing is not None and existing != lane:
            raise RuntimeError("QProcess cannot migrate between Makro account lanes")
        self._owners[process] = lane
        self._items[process] = value

    def __delitem__(self, process: Any) -> None:
        if self._owners.get(process) != self._lane_key():
            raise KeyError(process)
        del self._items[process]
        self._owners.pop(process, None)

    def __iter__(self) -> Iterator[Any]:
        lane = self._lane_key()
        return iter(tuple(process for process, owner in self._owners.items() if owner == lane))

    def __len__(self) -> int:
        lane = self._lane_key()
        return sum(owner == lane for owner in self._owners.values())

    def pop_owned(self, process: Any, default: Any = None) -> Any:
        owner = self._owners.get(process)
        if owner != self._lane_key():
            return default
        value = self._items.pop(process, default)
        self._owners.pop(process, None)
        return value


class LaneProcessDataMap(MutableMapping[Any, Any]):
    """Process-keyed data whose clear()/iteration is lane-local."""

    def __init__(
        self,
        processes: LaneProcessMap,
        lane_key: Callable[[], str],
    ) -> None:
        self._processes = processes
        self._lane_key = lane_key
        self._items: dict[Any, Any] = {}

    def _belongs(self, process: Any) -> bool:
        owner = self._processes.owner_for(process)
        return owner is None or owner == self._lane_key()

    def __getitem__(self, process: Any) -> Any:
        if not self._belongs(process):
            raise KeyError(process)
        return self._items[process]

    def __setitem__(self, process: Any, value: Any) -> None:
        owner = self._processes.owner_for(process)
        if owner is not None and owner != self._lane_key():
            raise RuntimeError("process data cannot migrate between Makro account lanes")
        self._items[process] = value

    def __delitem__(self, process: Any) -> None:
        if not self._belongs(process):
            raise KeyError(process)
        del self._items[process]

    def __iter__(self) -> Iterator[Any]:
        lane = self._lane_key()
        return iter(
            tuple(
                process
                for process in self._items
                if self._processes.owner_for(process) in {None, lane}
            )
        )

    def __len__(self) -> int:
        return sum(1 for _ in self.__iter__())

    def discard(self, process: Any) -> Any:
        return self._items.pop(process, None)


__all__ = [
    "BatchAccountLaneRegistry",
    "BatchAccountLaneState",
    "DEFAULT_BATCH_ACCOUNT_LANE",
    "LaneProcessDataMap",
    "LaneProcessMap",
]
