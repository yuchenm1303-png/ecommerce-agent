from __future__ import annotations

from types import SimpleNamespace

from app.providers.dashscope_web_search import (
    DashScopeWebSearchProvider,
    WEB_PROTOCOL_DASHSCOPE,
    WEB_PROTOCOL_OPENAI,
)


def _response(*, url: str = "https://example.com/source"):
    return SimpleNamespace(
        id="resp_test",
        output_text='{"probe":"ok"}',
        output=[
            SimpleNamespace(
                type="web_search_call",
                action=SimpleNamespace(sources=[SimpleNamespace(url=url, title="Source")]),
            )
        ],
    )


def test_runtime_provider_prefers_standard_responses_for_generic_relay() -> None:
    calls: list[dict] = []

    def call_fn(**kwargs):
        calls.append(kwargs)
        return _response()

    provider = DashScopeWebSearchProvider(
        model="sol-model",
        api_key="relay-key",
        base_url="https://relay.example/v1",
        request_timeout_seconds=10.0,
        call_fn=call_fn,
    )
    result = provider.search_json("Use web search and return JSON.")

    assert result.payload == {"probe": "ok"}
    assert result.protocol == WEB_PROTOCOL_OPENAI
    assert provider.resolved_protocol == WEB_PROTOCOL_OPENAI
    assert result.sources[0].url == "https://example.com/source"
    assert len(calls) == 1
    assert calls[0]["tools"] == [{"type": "web_search"}]
    assert "extra_body" not in calls[0]
    assert "tool_choice" not in calls[0]


def test_runtime_provider_falls_back_to_dashscope_extensions() -> None:
    calls: list[dict] = []

    def call_fn(**kwargs):
        calls.append(kwargs)
        if len(calls) <= 2:
            raise RuntimeError("standard Responses shape rejected")
        return _response()

    provider = DashScopeWebSearchProvider(
        model="qwen-model",
        api_key="relay-key",
        base_url="https://relay.example/v1",
        request_timeout_seconds=10.0,
        call_fn=call_fn,
    )
    result = provider.search_json("Use web search and return JSON.")

    assert result.protocol == WEB_PROTOCOL_DASHSCOPE
    assert len(calls) == 3
    assert calls[2]["tool_choice"] == "required"
    assert calls[2]["extra_body"]["search_options"]["forced_search"] is True


def test_runtime_provider_keeps_dashscope_first_for_official_endpoint() -> None:
    calls: list[dict] = []

    def call_fn(**kwargs):
        calls.append(kwargs)
        return _response(url="https://help.aliyun.com/source")

    provider = DashScopeWebSearchProvider(
        model="qwen3.7-max",
        api_key="dashscope-key",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        request_timeout_seconds=10.0,
        call_fn=call_fn,
    )
    result = provider.search_json("Use web search and return JSON.")

    assert result.protocol == WEB_PROTOCOL_DASHSCOPE
    assert len(calls) == 1
    assert calls[0]["extra_body"]["enable_thinking"] is False
    assert calls[0]["store"] is False


def test_standard_url_citations_are_accepted_as_sources() -> None:
    calls: list[dict] = []

    def call_fn(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            id="resp_citation",
            output_text='{"probe":"ok"}',
            output=[
                SimpleNamespace(type="web_search_call", action=SimpleNamespace(sources=[])),
                SimpleNamespace(
                    type="message",
                    content=[
                        SimpleNamespace(
                            type="output_text",
                            text="OpenAI",
                            annotations=[
                                SimpleNamespace(
                                    type="url_citation",
                                    url="https://openai.com/",
                                    title="OpenAI",
                                )
                            ],
                        )
                    ],
                ),
            ],
        )

    provider = DashScopeWebSearchProvider(
        model="sol-model",
        api_key="relay-key",
        base_url="https://relay.example/v1",
        request_timeout_seconds=10.0,
        call_fn=call_fn,
    )
    result = provider.search_json("Use web search and return JSON.")

    assert result.protocol == WEB_PROTOCOL_OPENAI
    assert [source.url for source in result.sources] == ["https://openai.com/"]
