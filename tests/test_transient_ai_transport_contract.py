from __future__ import annotations

import pytest

from app.providers import transient_retry


class RemoteProtocolError(RuntimeError):
    pass


class APIConnectionError(RuntimeError):
    pass


def test_nested_connection_transport_chain_is_retryable() -> None:
    low = RemoteProtocolError("Server disconnected without sending a response.")
    high = APIConnectionError("Connection error.")
    high.__cause__ = low

    assert transient_retry.is_retryable_ai_error(high) is True
    text = transient_retry.exception_text(high)
    assert "apiconnectionerror" in text
    assert "remoteprotocolerror" in text
    assert "server disconnected without sending a response" in text


def test_connection_error_retries_three_times(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(transient_retry.time, "sleep", lambda _seconds: None)
    calls = 0

    def operation() -> None:
        nonlocal calls
        calls += 1
        raise APIConnectionError("Connection error.")

    with pytest.raises(APIConnectionError, match="Connection error"):
        transient_retry.run_with_transient_retry(operation)

    assert calls == 3
