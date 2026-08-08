from __future__ import annotations

import time

import pytest

from app.providers.dashscope_web_search import DashScopeWebSearchProvider
from app.providers.errors import JSONTaskTransportError


def _response(text: str):
    return {
        "id": "resp-123",
        "output_text": text,
        "output": [
            {
                "type": "web_search_call",
                "action": {
                    "type": "search",
                    "query": "M8 dash cam specs",
                    "sources": [
                        {
                            "type": "url",
                            "url": "https://example.test/m8-spec",
                            "title": "M8 Dash Cam Specification",
                        }
                    ],
                },
            }
        ],
    }


def test_dashscope_web_search_uses_responses_api_without_incompatible_json_format():
    calls = []

    def fake_call(**kwargs):
        calls.append(kwargs)
        return _response('{"decisions": [], "summary": "done"}')

    provider = DashScopeWebSearchProvider(
        model="qwen3.6-flash",
        api_key="test-key",
        call_fn=fake_call,
    )
    result = provider.search_json("research this product")

    assert len(calls) == 1
    kwargs = calls[0]
    assert kwargs["model"] == "qwen3.6-flash"
    assert kwargs["tools"] == [{"type": "web_search"}]
    assert kwargs["extra_body"] == {"enable_thinking": False}
    assert kwargs["store"] is False
    assert "response_format" not in kwargs
    assert "enable_search" not in kwargs
    assert result.payload == {"decisions": [], "summary": "done"}
    assert result.request_id == "resp-123"
    assert [source.url for source in result.sources] == [
        "https://example.test/m8-spec"
    ]


def test_web_search_wall_clock_deadline_stops_hanging_call_quickly():
    def slow_call(**kwargs):
        time.sleep(1.0)
        return _response('{"decisions": []}')

    provider = DashScopeWebSearchProvider(
        model="qwen3.6-flash",
        api_key="test-key",
        request_timeout_seconds=10,
        call_fn=slow_call,
    )
    provider.request_timeout_seconds = 0.05

    started = time.monotonic()
    with pytest.raises(JSONTaskTransportError, match="wall-clock deadline"):
        provider.search_json("research this product")
    elapsed = time.monotonic() - started

    assert elapsed < 0.5
