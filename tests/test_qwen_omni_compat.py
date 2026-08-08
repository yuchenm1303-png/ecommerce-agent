from __future__ import annotations

import json
from types import SimpleNamespace

from app.ai_decisions import AI_DECISION_JSON_SCHEMA
from app.providers.openai_compatible import OpenAICompatibleSemanticProvider


def _request_payload():
    return {
        "task": "resolve_all_live_marketplace_fields_from_product_sources",
        "system_instruction": "You are the primary product-listing resolver.",
        "prompt_instruction": "Resolve every target field from grounded sources.",
        "product_identity": {"sku": "SKU-1", "model_number": "M8", "brand": ""},
        "schema_sha256": "schema",
        "source_manifest_sha256": "sources",
        "target_fields": [
            {
                "field_id": "mf_colour",
                "attribute_key": "colour",
                "label": "Colour",
                "section_heading": "Product Description",
                "required": True,
                "multi_value": False,
                "options": ["Black", "White"],
                "qualifier_options": [],
                "help_text": "",
                "business_locked": False,
            }
        ],
        "rules": ["Do not invent unsupported product facts."],
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
        "json_contract": AI_DECISION_JSON_SCHEMA,
    }


class FakeStreamingCreate:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        text = json.dumps(
            {
                "contract_version": 1,
                "product_identity": {"sku": "SKU-1", "model_number": "M8", "brand": ""},
                "schema_sha256": "schema",
                "source_manifest_sha256": "sources",
                "decisions": [
                    {
                        "field_id": "mf_colour",
                        "status": "ready",
                        "values": ["Black"],
                        "qualifier": "",
                        "confidence": 0.95,
                        "citations": [
                            {
                                "source_reference": "supplier:001:text:0001",
                                "evidence_text": "Colour: Black.",
                            }
                        ],
                        "alternatives": [],
                        "reason": "supported",
                        "search_queries": [],
                    }
                ],
                "model_summary": "resolved product",
                "warnings": [],
            }
        )
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


def test_qwen_omni_profile_streams_text_only_and_parses_ai_decision_json():
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
    assert payload["decisions"][0]["values"] == ["Black"]
