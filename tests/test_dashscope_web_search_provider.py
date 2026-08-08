from __future__ import annotations

import time

import pytest

from app.providers.dashscope_web_search import DashScopeWebSearchProvider
from app.providers.errors import JSONTaskTransportError


def _chunk(text: str):
    return {
        "status_code": 200,
        "request_id": "req-123",
        "output": {
            "search_info": {
                "search_results": [
                    {
                        "index": "1",
                        "title": "M8 Dash Cam Specification",
                        "url": "https://example.test/m8-spec",
                        "site_name": "Example",
                    }
                ]
            },
            "choices": [{"message": {"content": text}}],
        },
    }


def test_dashscope_web_search_uses_agent_sources_and_native_json_mode():
    calls = []

    def fake_call(**kwargs):
        calls.append(kwargs)
        return [_chunk('{"decisions": [], "summary": "done"}')]

    provider = DashScopeWebSearchProvider(
        model="qwen3.5-omni-plus",
        api_key="test-key",
        call_fn=fake_call,
    )
    result = provider.search_json("research this product")

    assert len(calls) == 1
    kwargs = calls[0]
    assert kwargs["enable_search"] is True
    assert kwargs["search_options"] == {
        "search_strategy": "agent",
        "enable_source": True,
    }
    assert kwargs["response_format"] == {"type": "json_object"}
    assert kwargs["stream"] is True
    assert "max_tokens" not in kwargs
    assert result.payload == {"decisions": [], "summary": "done"}
    assert result.request_id == "req-123"
    assert [source.url for source in result.sources] == [
        "https://example.test/m8-spec"
    ]


def test_web_search_wall_clock_deadline_stops_hanging_call_quickly():
    def slow_call(**kwargs):
        time.sleep(1.0)
        return [_chunk('{"decisions": []}')]

    provider = DashScopeWebSearchProvider(
        model="qwen3.5-omni-plus",
        api_key="test-key",
        request_timeout_seconds=10,
        call_fn=slow_call,
    )
    # Keep production constructor bounds but shorten this isolated unit instance.
    provider.request_timeout_seconds = 0.05

    started = time.monotonic()
    with pytest.raises(JSONTaskTransportError, match="wall-clock deadline"):
        provider.search_json("research this product")
    elapsed = time.monotonic() - started

    assert elapsed < 0.5
