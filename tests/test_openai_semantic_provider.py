from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.ai_decisions import AI_DECISION_JSON_SCHEMA
from app.providers.openai_semantic import OpenAIProviderError, OpenAISemanticProvider


class FakeResponses:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class FakeClient:
    def __init__(self, response):
        self.responses = FakeResponses(response)


def valid_output():
    return {
        "contract_version": 1,
        "product_identity": {"sku": "SKU-1", "model_number": "M8", "brand": ""},
        "schema_sha256": "schema-digest",
        "source_manifest_sha256": "source-digest",
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
                "reason": "supported by supplier source",
                "search_queries": [],
            }
        ],
        "model_summary": "resolved product",
        "warnings": [],
    }


def request(tmp_path):
    image = tmp_path / "front.jpg"
    image.write_bytes(b"fake-jpeg")
    return {
        "task": "resolve_all_live_marketplace_fields_from_product_sources",
        "system_instruction": "You are the primary product-listing resolver.",
        "prompt_instruction": "Resolve every target field from grounded sources.",
        "product_identity": {"sku": "SKU-1", "model_number": "M8", "brand": ""},
        "schema_sha256": "schema-digest",
        "source_manifest_sha256": "source-digest",
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
                "origin": "https://supplier.test/item",
                "sha256": "a" * 64,
                "content": "Colour: Black.",
            },
            {
                "source_id": "image:001",
                "source_type": "product_image",
                "kind": "image",
                "origin": str(image.resolve()),
                "sha256": "b" * 64,
                "image_path": str(image),
            },
        ],
        "json_contract": AI_DECISION_JSON_SCHEMA,
    }


def test_openai_provider_uses_strict_schema_and_image_data_uri(tmp_path):
    response = SimpleNamespace(
        status="completed",
        output_text=json.dumps(valid_output()),
        incomplete_details=None,
    )
    client = FakeClient(response)
    provider = OpenAISemanticProvider(client=client, model="gpt-5.6", image_detail="high")

    payload = provider.extract_json(request(tmp_path))

    assert payload["extractor"] == "openai-responses-semantic"
    assert payload["decisions"][0]["values"] == ["Black"]
    call = client.responses.calls[0]
    assert call["model"] == "gpt-5.6"
    assert call["text"]["format"]["type"] == "json_schema"
    assert call["text"]["format"]["strict"] is True
    assert call["text"]["format"]["schema"] == AI_DECISION_JSON_SCHEMA

    user_content = call["input"][1]["content"]
    image_parts = [item for item in user_content if item["type"] == "input_image"]
    assert len(image_parts) == 1
    assert image_parts[0]["image_url"].startswith("data:image/jpeg;base64,")
    prompt_text = user_content[0]["text"]
    assert "Colour: Black." in prompt_text
    assert str(tmp_path) not in prompt_text


def test_openai_provider_rejects_incomplete_response(tmp_path):
    response = SimpleNamespace(
        status="incomplete",
        output_text="",
        incomplete_details=SimpleNamespace(reason="max_output_tokens"),
    )
    provider = OpenAISemanticProvider(client=FakeClient(response))
    with pytest.raises(OpenAIProviderError, match="未完整完成"):
        provider.extract_json(request(tmp_path))


def test_openai_provider_rejects_non_json_output(tmp_path):
    response = SimpleNamespace(status="completed", output_text="not-json", incomplete_details=None)
    provider = OpenAISemanticProvider(client=FakeClient(response))
    with pytest.raises(OpenAIProviderError, match="不是有效 JSON"):
        provider.extract_json(request(tmp_path))


def test_openai_provider_uses_ai_first_instruction_not_legacy_fact_prompt(tmp_path):
    response = SimpleNamespace(
        status="completed",
        output_text=json.dumps(valid_output()),
        incomplete_details=None,
    )
    client = FakeClient(response)
    provider = OpenAISemanticProvider(client=client, model="gpt-5.6")
    provider.extract_json(request(tmp_path))

    system_text = client.responses.calls[0]["input"][0]["content"]
    prompt_text = client.responses.calls[0]["input"][1]["content"][0]["text"]
    assert "primary product-listing resolver" in system_text
    assert "Resolve every target field" in prompt_text
    assert "GROUNDED OUTPUT RULES" not in prompt_text
    assert "ai_synthesis" not in prompt_text


def test_openai_provider_requires_json_contract(tmp_path):
    response = SimpleNamespace(status="completed", output_text=json.dumps(valid_output()), incomplete_details=None)
    provider = OpenAISemanticProvider(client=FakeClient(response))
    payload = request(tmp_path)
    payload.pop("json_contract")
    with pytest.raises(OpenAIProviderError, match="json_contract"):
        provider.extract_json(payload)
