from __future__ import annotations

import pytest

from app.providers import transient_retry


def test_transient_ai_retry_succeeds_after_bounded_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(transient_retry.time, "sleep", lambda _seconds: None)
    calls = 0

    def operation() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise RuntimeError("OpenAI-compatible API 未返回可解析的 JSON object。")
        return "ok"

    assert transient_retry.run_with_transient_retry(operation) == "ok"
    assert calls == 3


def test_transient_ai_retry_does_not_retry_billing_or_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(transient_retry.time, "sleep", lambda _seconds: None)
    for message in (
        "Arrearage: account balance is insufficient",
        "invalid API key",
        "authentication failed",
    ):
        calls = 0

        def operation() -> None:
            nonlocal calls
            calls += 1
            raise RuntimeError(message)

        with pytest.raises(RuntimeError, match=message):
            transient_retry.run_with_transient_retry(operation)
        assert calls == 1


def test_transient_ai_retry_stops_after_three_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(transient_retry.time, "sleep", lambda _seconds: None)
    calls = 0

    def operation() -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("HTTP 503 temporarily unavailable")

    with pytest.raises(RuntimeError, match="503"):
        transient_retry.run_with_transient_retry(operation)
    assert calls == 3
