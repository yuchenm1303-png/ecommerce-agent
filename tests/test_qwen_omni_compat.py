from __future__ import annotations

from types import SimpleNamespace

from app.providers.openai_compatible import OpenAICompatibleSemanticProvider


def _request_payload():
    return {
        "task": "extract_only_source_grounded_answers_for_current_qa",
        "batch_id": "batch-001",
        "product_identity": {"sku": "", "model_number": "", "brand": ""},
        "questions": [{"question": "Screen Size", "business_locked": False}],
        "business_locked_questions": [],
        "rules": ["Do not guess."],
        "source_reference_rule": "Use exact source id.",
        "required_output_shape": {},
        "grounded_sources": [
            {
                "source_id": "supplier:001:text:0001",
                "source_type": "supplier_web",
                "kind": "text",
                "sha256": "abc",
                "origin": "https://supplier.test/item",
                "content": "Screen Size: 3.0 inch.",
            }
        ],
    }


class FakeStreamingCreate:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        text = (
            '{"product_identity":{"sku":"","model_number":"","brand":""},'
            '"facts":[{"key":"Screen Size","aliases":[],"value":["3.0 inch"],'
            '"source_type":"supplier_web","source_reference":"supplier:001:text:0001",'
            '"confidence":0.9,"evidence_text":"Screen Size: 3.0 inch.","note":""}],'
            '"warnings":[]}'
        )
        split = len(text) // 2
        return [
            SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content=text[:split]))]
            ),
            SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content=text[split:]))]
            ),
            SimpleNamespace(choices=[]),
        ]


class FakeClient:
    def __init__(self):
        self.create_api = FakeStreamingCreate()
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self.create_api.create)
        )


def test_qwen_omni_profile_streams_text_only_and_parses_json():
    client = FakeClient()
    provider = OpenAICompatibleSemanticProvider(
        model="qwen3.5-omni-plus-2026-03-15",
        api_key="secret",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        client=client,
        compat_profile="qwen-omni",
    )

    payload = provider.extract_json(_request_payload())

    kwargs = client.create_api.calls[0]
    assert kwargs["stream"] is True
    assert kwargs["stream_options"] == {"include_usage": True}
    assert kwargs["modalities"] == ["text"]
    assert payload["facts"][0]["value"] == ["3.0 inch"]
