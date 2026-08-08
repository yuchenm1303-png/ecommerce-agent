from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

from app.providers.openai_compatible import (
    OpenAICompatibleProviderError,
    OpenAICompatibleSemanticProvider,
    OpenAICompatibleTransportError,
)


TASK_JSON_SCHEMA = {
    "type": "object",
    "properties": {"decisions": {"type": "array"}},
    "required": ["decisions"],
}


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
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create_api.create))


class SlowCreate:
    def __init__(self, seconds: float):
        self.seconds = seconds
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        time.sleep(self.seconds)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=valid_json()))]
        )


class SlowClient:
    def __init__(self, seconds: float):
        self.create_api = SlowCreate(seconds)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create_api.create))


def request_payload(image_path: str | None = None):
    sources = [
        {
            "source_id": "supplier:001:text:0001",
            "source_type": "supplier_web",
            "kind": "text",
            "sha256": "abc",
            "origin": "https://supplier.test/item",
            "content": "Colour: Black.",
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
        "task": "generic_json_task",
        "system_instruction": "Execute the supplied JSON task.",
        "prompt_instruction": "Return structured decisions.",
        "product_identity": {"sku": "SKU-1", "model_number": "M8", "brand": ""},
        "target_fields": [{"field_id": "mf_colour", "label": "Colour"}],
        "rules": ["Do not invent unsupported product facts."],
        "grounded_sources": sources,
        "json_contract": TASK_JSON_SCHEMA,
    }


def valid_json():
    return json.dumps(
        {
            "decisions": [
                {
                    "field_id": "mf_colour",
                    "status": "ready",
                    "values": ["Black"],
                }
            ]
        }
    )


def test_prompt_only_provider_parses_json_and_keeps_api_key_and_paths_out_of_prompt(tmp_path):
    image = tmp_path / "front.png"
    image.write_bytes(b"not-a-real-png-but-local-bytes")
    client = FakeClient(f"```json\n{valid_json()}\n```")
    provider = OpenAICompatibleSemanticProvider(
        model="vision-model",
        api_key="secret-key",
        base_url="https://api.vendor.test/v1",
        client=client,
        structured_mode="prompt_only",
        request_timeout_seconds=80,
    )
    payload = provider.extract_json(request_payload(str(image)))
    assert payload["extractor"] == provider.name
    assert payload["decisions"][0]["values"] == ["Black"]
    kwargs = client.create_api.calls[0]
    assert kwargs["model"] == "vision-model"
    assert kwargs["timeout"] == 80
    assert kwargs["max_tokens"] == 12000
    assert "response_format" not in kwargs
    assert "temperature" not in kwargs
    assert "extra_body" not in kwargs
    serialized = repr(kwargs)
    assert "secret-key" not in serialized
    assert str(image) not in serialized
    user_content = kwargs["messages"][1]["content"]
    image_items = [item for item in user_content if item.get("type") == "image_url"]
    assert len(image_items) == 1
    assert "data:image/png;base64," in repr(image_items[0])
    assert "detail" not in image_items[0]["image_url"]


def test_explicit_thinking_mode_is_forwarded_via_extra_body():
    client = FakeClient(valid_json())
    provider = OpenAICompatibleSemanticProvider(
        model="qwen3.5-omni-plus",
        api_key="secret-key",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        client=client,
        enable_thinking=False,
    )
    provider.extract_json(request_payload())
    assert client.create_api.calls[0]["extra_body"] == {"enable_thinking": False}


def test_explicit_high_detail_is_only_sent_when_requested(tmp_path):
    image = tmp_path / "front.png"
    image.write_bytes(b"image-bytes")
    client = FakeClient(valid_json())
    provider = OpenAICompatibleSemanticProvider(
        model="vision-model",
        api_key="secret-key",
        base_url="https://api.vendor.test/v1",
        client=client,
        image_detail="high",
    )
    provider.extract_json(request_payload(str(image)))
    user_content = client.create_api.calls[0]["messages"][1]["content"]
    image_item = next(item for item in user_content if item.get("type") == "image_url")
    assert image_item["image_url"]["detail"] == "high"


def test_json_object_mode_requests_native_json_and_does_not_set_max_tokens():
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
    assert "max_tokens" not in kwargs
    assert provider.base_url == "https://api.vendor.test/v1"


def test_whole_request_wall_clock_deadline_stops_hanging_request_quickly():
    client = SlowClient(1.0)
    provider = OpenAICompatibleSemanticProvider(
        model="vision-model",
        api_key="secret-key",
        base_url="https://api.vendor.test/v1",
        client=client,
        request_timeout_seconds=10,
    )
    provider.request_timeout_seconds = 0.05
    started = time.monotonic()
    with pytest.raises(OpenAICompatibleTransportError, match="wall-clock deadline"):
        provider.extract_json(request_payload())
    assert time.monotonic() - started < 0.5
    assert len(client.create_api.calls) == 1


def test_non_json_provider_output_fails_closed_with_response_prefix():
    provider = OpenAICompatibleSemanticProvider(
        model="vision-model",
        api_key="secret-key",
        base_url="https://api.vendor.test/v1",
        client=FakeClient("not-json"),
    )
    with pytest.raises(OpenAICompatibleProviderError, match="response_prefix"):
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


def test_provider_prompt_is_generic_and_omits_local_packet_digests():
    client = FakeClient(valid_json())
    provider = OpenAICompatibleSemanticProvider(
        model="vision-model",
        api_key="secret-key",
        base_url="https://api.vendor.test/v1",
        client=client,
    )
    provider.extract_json(request_payload())
    user_text = client.create_api.calls[0]["messages"][1]["content"][0]["text"]
    assert "Return structured decisions" in user_text
    assert '"target_fields"' in user_text
    assert '"json_contract"' in user_text
    assert "schema_sha256" not in user_text
    assert "source_manifest_sha256" not in user_text
    assert "ai_synthesis" not in user_text


def test_provider_requires_task_json_contract():
    provider = OpenAICompatibleSemanticProvider(
        model="vision-model",
        api_key="secret-key",
        base_url="https://api.vendor.test/v1",
        client=FakeClient(valid_json()),
    )
    payload = request_payload()
    payload.pop("json_contract")
    with pytest.raises(OpenAICompatibleProviderError, match="json_contract"):
        provider.extract_json(payload)
