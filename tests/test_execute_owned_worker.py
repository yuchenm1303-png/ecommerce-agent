from __future__ import annotations

from contextlib import contextmanager

import pytest

import makro_execute_owned as owned


@contextmanager
def _recording_lane(events: list[tuple[str, int]], port: int):
    events.append(("enter", int(port)))
    try:
        yield
    finally:
        events.append(("exit", int(port)))


def test_owned_execute_calls_business_executor_inside_same_transport_lane(monkeypatch) -> None:
    events: list[tuple[str, int]] = []
    monkeypatch.setattr(
        owned,
        "exclusive_cdp_transport_lane",
        lambda port: _recording_lane(events, port),
    )
    monkeypatch.setattr(owned, "poison_matches_current_generation", lambda _port: False)

    def execute() -> int:
        events.append(("execute", 9222))
        return 0

    monkeypatch.setattr(owned, "execute_main", execute)
    monkeypatch.setattr(owned.sys, "argv", ["makro_execute_owned.py", "--cdp-port", "9222"])

    assert owned.main() == 0
    assert events == [("enter", 9222), ("execute", 9222), ("exit", 9222)]


def test_owned_execute_never_calls_executor_for_poisoned_generation(monkeypatch) -> None:
    events: list[tuple[str, int]] = []
    monkeypatch.setattr(
        owned,
        "exclusive_cdp_transport_lane",
        lambda port: _recording_lane(events, port),
    )
    monkeypatch.setattr(owned, "poison_matches_current_generation", lambda _port: True)
    monkeypatch.setattr(
        owned,
        "execute_main",
        lambda: (_ for _ in ()).throw(AssertionError("executor must not run")),
    )
    monkeypatch.setattr(owned.sys, "argv", ["makro_execute_owned.py", "--cdp-port", "9333"])

    with pytest.raises(RuntimeError, match="generation 已标记失效"):
        owned.main()
    assert events == [("enter", 9333), ("exit", 9333)]
