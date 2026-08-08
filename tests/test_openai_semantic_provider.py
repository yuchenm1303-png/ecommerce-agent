from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

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
        "extractor": "model-chosen-name",
        "product_identity": {"sku": "", "model_number": "L11", "brand": ""},
        "facts": [
            {
                "key": "Screen Size",
                "aliases": [],
                "value": ["3.0 inch"],
                "source_type": "supplier_web",
                "source_reference": "supplier:001:text:0001",
                "confidence": 0.88,
                "evidence_text": "Screen Size: 3.0 inch.",
                "note": "",
            }
        ],
        "warnings": [],
    }


def request(tmp_path):
    image = tmp_path / "front.jpg"
    image.write_bytes(b"fake-jpeg")
    return {
        "task": "extract_only_source_grounded_answers_for_current_qa",
        "batch_id": "batch-001",
        "product_identity": {"sku": "", "model_number": "L11", "brand": ""},
        "questions": [{"number": "1", "question": "Screen Size", "business_locked": False}],
        "business_locked_questions": [],
        "rules": ["Do not guess."],
        "source_reference_rule": "exact source id",
        "grounded_sources": [
            {
                "source_id": "supplier:001:text:0001",
                "source_type": "supplier_web",
                "kind": "text",
                "origin": "https://supplier.test/item",
                "sha256": "a" * 64,
                "content": "Screen Size: 3.0 inch.",
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
    }


def test_openai_provider_uses_responses_structured_output_and_image_data_uri(tmp_path):
    response = SimpleNamespace(
        status="completed",
        output_text=json.dumps(valid_output()),
        incomplete_details=None,
    )
    client = FakeClient(response)
    provider = OpenAISemanticProvider(client=client, model="gpt-5.6", image_detail="high")

    payload = provider.extract_json(request(tmp_path))

    assert payload["extractor"] == "openai-responses-semantic"
    call = client.responses.calls[0]
    assert call["model"] == "gpt-5.6"
    assert call["text"]["format"]["type"] == "json_schema"
    assert call["text"]["format"]["strict"] is True

    user_content = call["input"][1]["content"]
    image_parts = [item for item in user_content if item["type"] == "input_image"]
    assert len(image_parts) == 1
    assert image_parts[0]["image_url"].startswith("data:image/jpeg;base64,")

    prompt_text = user_content[0]["text"]
    assert "Screen Size: 3.0 inch." in prompt_text
    # Local image paths are not copied into the textual prompt; bytes are sent
    # only through the input_image part.
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


def test_openai_provider_foregrounds_grounding_rules(tmp_path):
    response = SimpleNamespace(
        status="completed",
        output_text=json.dumps(valid_output()),
        incomplete_details=None,
    )
    client = FakeClient(response)
    provider = OpenAISemanticProvider(client=client, model="gpt-5.6")

    provider.extract_json(request(tmp_path))

    prompt_text = client.responses.calls[0]["input"][1]["content"][0]["text"]
    assert "GROUNDED OUTPUT RULES" in prompt_text
    assert 'source_type="ai_synthesis"' in prompt_text
    assert "character-for-character" in prompt_text
