from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.providers.openai_compatible import (
    OpenAICompatibleProviderError,
    OpenAICompatibleSemanticProvider,
)


class FakeCreate:
    def __init__(self, content: str):
        self.content = content
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))]
        )


class FakeClient:
    def __init__(self, content: str):
        self.create_api = FakeCreate(content)
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self.create_api.create)
        )


def request_payload(image_path: str | None = None):
    sources = [
        {
            "source_id": "supplier:001:text:0001",
            "source_type": "supplier_web",
            "kind": "text",
            "sha256": "abc",
            "origin": "https://supplier.test/item",
            "content": "Screen Size: 3.0 inch.",
        }
    ]
    if image_path:
        sources.append(
            {
                "source_id": "image:001",
                "source_type": "product_image",
                "kind": "image",
                "sha256": "def",
                "image_path": image_path,
            }
        )
    return {
        "task": "extract_only_source_grounded_answers_for_current_qa",
        "batch_id": "batch-001",
        "product_identity": {"sku": "", "model_number": "L11", "brand": ""},
        "questions": [{"question": "Screen Size", "business_locked": False}],
        "business_locked_questions": [],
        "rules": ["Do not guess."],
        "source_reference_rule": "Use exact source id.",
        "required_output_shape": {},
        "grounded_sources": sources,
    }


def valid_json():
    return (
        '{"product_identity":{"sku":"","model_number":"L11","brand":""},'
        '"facts":[{"key":"Screen Size","aliases":[],"value":["3.0 inch"],'
        '"source_type":"supplier_web","source_reference":"supplier:001:text:0001",'
        '"confidence":0.88,"evidence_text":"Screen Size: 3.0 inch.","note":""}],'
        '"warnings":[]}'
    )


def test_prompt_only_provider_parses_fenced_json_and_keeps_api_key_out_of_prompt(tmp_path):
    image = tmp_path / "front.png"
    image.write_bytes(b"not-a-real-png-but-local-bytes")
    client = FakeClient(f"```json\n{valid_json()}\n```")
    provider = OpenAICompatibleSemanticProvider(
        model="vision-model",
        api_key="secret-key",
        base_url="https://api.vendor.test/v1",
        client=client,
        structured_mode="prompt_only",
    )

    payload = provider.extract_json(request_payload(str(image)))

    assert payload["extractor"] == provider.name
    assert payload["facts"][0]["value"] == ["3.0 inch"]
    kwargs = client.create_api.calls[0]
    assert kwargs["model"] == "vision-model"
    assert "response_format" not in kwargs
    serialized = repr(kwargs)
    assert "secret-key" not in serialized
    assert str(image) not in serialized
    user_content = kwargs["messages"][1]["content"]
    assert any(item.get("type") == "image_url" for item in user_content)
    assert "data:image/png;base64," in repr(user_content)


def test_json_object_mode_requests_common_compat_response_format():
    client = FakeClient(valid_json())
    provider = OpenAICompatibleSemanticProvider(
        model="vision-model",
        api_key="secret-key",
        base_url="https://api.vendor.test/v1/",
        client=client,
        structured_mode="json_object",
    )

    provider.extract_json(request_payload())

    kwargs = client.create_api.calls[0]
    assert kwargs["response_format"] == {"type": "json_object"}
    assert provider.base_url == "https://api.vendor.test/v1"


def test_non_json_provider_output_fails_closed():
    client = FakeClient("I think the screen is three inches.")
    provider = OpenAICompatibleSemanticProvider(
        model="vision-model",
        api_key="secret-key",
        base_url="https://api.vendor.test/v1",
        client=client,
    )

    with pytest.raises(OpenAICompatibleProviderError, match="JSON object"):
        provider.extract_json(request_payload())


def test_missing_image_is_rejected_before_api_call(tmp_path):
    client = FakeClient(valid_json())
    provider = OpenAICompatibleSemanticProvider(
        model="vision-model",
        api_key="secret-key",
        base_url="https://api.vendor.test/v1",
        client=client,
    )

    with pytest.raises(OpenAICompatibleProviderError, match="找不到图片"):
        provider.extract_json(request_payload(str(tmp_path / "missing.png")))
    assert not client.create_api.calls
