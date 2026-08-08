from __future__ import annotations

import json
from types import SimpleNamespace

from app.providers.openai_compatible import OpenAICompatibleSemanticProvider


TASK_JSON_SCHEMA = {
    "type": "object",
    "properties": {"result": {"type": "string"}},
    "required": ["result"],
}


def _request_payload():
    return {
        "task": "generic_json_task",
        "system_instruction": "You execute the supplied JSON task.",
        "prompt_instruction": "Return the result as JSON.",
        "product_identity": {},
        "target_fields": [],
        "rules": [],
        "grounded_sources": [
            {
                "source_id": "supplier:001:text:0001",
                "source_type": "supplier_web",
                "kind": "text",
                "sha256": "abc",
                "origin": "https://supplier.test/item",
                "content": "Colour: Black.",
            }
        ],
        "json_contract": TASK_JSON_SCHEMA,
    }


class FakeStreamingCreate:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        text = json.dumps({"result": "ok"})
        split = len(text) // 2
        return [
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text[:split]))]),
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text[split:]))]),
            SimpleNamespace(choices=[]),
        ]


class FakeClient:
    def __init__(self):
        self.create_api = FakeStreamingCreate()
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create_api.create))


def test_qwen_omni_profile_streams_text_only_and_parses_generic_json_task():
    client = FakeClient()
    provider = OpenAICompatibleSemanticProvider(
        model="qwen3.5-omni-plus-2026-03-15",
        api_key="secret",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        client=client,
        compat_profile="qwen-omni",
        enable_thinking=False,
    )
    payload = provider.extract_json(_request_payload())
    kwargs = client.create_api.calls[0]
    assert kwargs["stream"] is True
    assert kwargs["stream_options"] == {"include_usage": True}
    assert kwargs["modalities"] == ["text"]
    assert kwargs["extra_body"] == {"enable_thinking": False}
    assert payload["result"] == "ok"
