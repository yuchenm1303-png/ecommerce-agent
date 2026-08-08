from __future__ import annotations

from app.providers.dashscope_web_search import DashScopeWebSearchProvider


def test_dashscope_web_search_collects_json_and_exact_returned_sources():
    calls = []

    def fake_call(**kwargs):
        calls.append(kwargs)
        return [
            {
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
                    "choices": [
                        {
                            "message": {
                                "content": '{"decisions": [], "summary": "done"}'
                            }
                        }
                    ],
                },
            }
        ]

    provider = DashScopeWebSearchProvider(
        model="qwen3.5-omni-plus",
        api_key="test-key",
        call_fn=fake_call,
    )
    result = provider.search_json("research this product")

    assert len(calls) == 1
    assert calls[0]["enable_search"] is True
    assert calls[0]["search_options"] == {
        "search_strategy": "agent",
        "enable_source": True,
    }
    assert calls[0]["stream"] is True
    assert result.payload == {"decisions": [], "summary": "done"}
    assert result.request_id == "req-123"
    assert [source.url for source in result.sources] == [
        "https://example.test/m8-spec"
    ]
