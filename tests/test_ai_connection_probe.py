from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.ai_connection_probe import (
    CapabilityProbeError,
    RoleBinding,
    assert_verified_if_managed,
    binding_signature,
    discover_models,
    probe_role,
    save_verification_snapshot,
    verification_path,
)


class _Factory:
    def __init__(self, client):
        self.client = client
        self.kwargs = None

    def __call__(self, **kwargs):
        self.kwargs = kwargs
        return self.client


class _Models:
    def __init__(self, ids):
        self.ids = ids

    def list(self):
        return SimpleNamespace(data=[SimpleNamespace(id=value) for value in self.ids])


class _ChatCompletions:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        payload = self.payloads.pop(0)
        if isinstance(payload, BaseException):
            raise payload
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
        )


class _Responses:
    def __init__(self, outputs):
        if isinstance(outputs, tuple):
            self.outputs = list(outputs)
        else:
            self.outputs = [outputs]
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.outputs:
            raise AssertionError("unexpected extra Responses call")
        output = self.outputs.pop(0)
        if isinstance(output, BaseException):
            raise output
        if hasattr(output, "output"):
            return output
        return SimpleNamespace(output=output)


class _Client:
    def __init__(self, *, models=(), chat_payloads=(), web_output=()):
        self.models = _Models(models)
        self.chat = SimpleNamespace(completions=_ChatCompletions(chat_payloads))
        self.responses = _Responses(web_output)


def _binding(
    role: str,
    model: str = "model-a",
    key: str = "secret-key",
    base_url: str = "https://relay.example/v1",
) -> RoleBinding:
    return RoleBinding(role, base_url, model, key)


def _web_call_with_sources(url: str = "https://openai.com/"):
    return [
        SimpleNamespace(
            type="web_search_call",
            action=SimpleNamespace(sources=[SimpleNamespace(url=url)]),
        )
    ]


def test_discover_models_returns_sorted_unique_catalog() -> None:
    client = _Client(models=("z-model", "a-model", "z-model"))
    result = discover_models(
        base_url="https://relay.example/v1",
        api_key="secret-key",
        client_factory=_Factory(client),
    )
    assert result.catalog_available is True
    assert result.models == ("a-model", "z-model")


def test_discover_models_failure_does_not_leak_key() -> None:
    class BrokenModels:
        def list(self):
            raise RuntimeError("Authorization Bearer secret-key failed")

    client = _Client()
    client.models = BrokenModels()
    result = discover_models(
        base_url="https://relay.example/v1",
        api_key="secret-key",
        client_factory=_Factory(client),
    )
    assert result.catalog_available is False
    assert "secret-key" not in result.error
    assert "Bearer ***" in result.error


def test_fact_requires_real_strict_json_schema_call() -> None:
    client = _Client(chat_payloads=({"probe": "ok"},))
    report = probe_role(_binding("fact"), client_factory=_Factory(client))
    assert report.passed is True
    call = client.chat.completions.calls[0]
    assert call["response_format"]["type"] == "json_schema"
    assert call["response_format"]["json_schema"]["strict"] is True
    assert call["extra_body"] == {"enable_thinking": False}


def test_semantic_requires_vision_and_validates_image_content() -> None:
    sequence = ("red", "green", "blue")
    client = _Client(chat_payloads=({"probe": "ok"}, {"colors": list(sequence)}))
    report = probe_role(
        _binding("semantic"),
        client_factory=_Factory(client),
        vision_sequence=sequence,
    )
    assert report.passed is True
    vision_call = client.chat.completions.calls[1]
    content = vision_call["messages"][1]["content"]
    image_item = [item for item in content if item.get("type") == "image_url"][0]
    assert image_item["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_semantic_fails_when_endpoint_ignores_image() -> None:
    client = _Client(
        chat_payloads=(
            {"probe": "ok"},
            {"colors": ["yellow", "yellow", "yellow"]},
        )
    )
    report = probe_role(
        _binding("semantic"),
        client_factory=_Factory(client),
        vision_sequence=("red", "green", "blue"),
    )
    assert report.passed is False
    vision = [item for item in report.checks if item.name == "vision"][0]
    assert vision.passed is False


def test_web_relay_prefers_standard_responses_without_dashscope_fields() -> None:
    client = _Client(web_output=_web_call_with_sources())
    report = probe_role(_binding("web"), client_factory=_Factory(client))
    assert report.passed is True
    call = client.responses.calls[0]
    assert call["tools"] == [{"type": "web_search"}]
    assert "extra_body" not in call
    assert "tool_choice" not in call
    names = {item.name for item in report.checks if item.passed}
    assert "web_protocol_openai_responses" in names


def test_web_accepts_standard_url_citation_sources() -> None:
    response = SimpleNamespace(
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
        ]
    )
    client = _Client(web_output=response)
    report = probe_role(_binding("web"), client_factory=_Factory(client))
    assert report.passed is True
    names = {item.name for item in report.checks if item.passed}
    assert "web_sources" in names


def test_web_falls_back_to_dashscope_extensions_after_standard_rejection() -> None:
    client = _Client(
        web_output=(
            RuntimeError("standard minimal rejected"),
            RuntimeError("standard required rejected"),
            _web_call_with_sources(),
        )
    )
    report = probe_role(_binding("web"), client_factory=_Factory(client))
    assert report.passed is True
    assert len(client.responses.calls) == 3
    dashscope_call = client.responses.calls[2]
    assert dashscope_call["tool_choice"] == "required"
    assert dashscope_call["extra_body"]["search_options"]["forced_search"] is True
    names = {item.name for item in report.checks if item.passed}
    assert "web_protocol_dashscope_responses" in names


def test_official_dashscope_endpoint_keeps_dashscope_protocol_first() -> None:
    client = _Client(web_output=_web_call_with_sources())
    report = probe_role(
        _binding(
            "web",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        ),
        client_factory=_Factory(client),
    )
    assert report.passed is True
    call = client.responses.calls[0]
    assert call["extra_body"]["search_options"]["forced_search"] is True
    names = {item.name for item in report.checks if item.passed}
    assert "web_protocol_dashscope_responses" in names


@pytest.mark.parametrize(
    "output",
    [
        [],
        [SimpleNamespace(type="web_search_call", action=SimpleNamespace(sources=[]))],
    ],
)
def test_web_rejects_fake_or_sourceless_search(output) -> None:
    # Every protocol attempt receives the same unusable response.
    client = _Client(web_output=(output, output, output))
    report = probe_role(_binding("web"), client_factory=_Factory(client))
    assert report.passed is False


def test_verification_snapshot_contains_no_plaintext_key_and_guards_binding_changes(
    tmp_path: Path,
) -> None:
    bindings = (
        _binding("semantic", "semantic-model", "semantic-secret"),
        _binding("fact", "fact-model", "fact-secret"),
        _binding("web", "web-model", "web-secret"),
    )
    reports = (
        probe_role(
            bindings[0],
            client_factory=_Factory(
                _Client(
                    chat_payloads=(
                        {"probe": "ok"},
                        {"colors": ["red", "green", "blue"]},
                    )
                )
            ),
            vision_sequence=("red", "green", "blue"),
        ),
        probe_role(
            bindings[1],
            client_factory=_Factory(_Client(chat_payloads=({"probe": "ok"},))),
        ),
        probe_role(
            bindings[2],
            client_factory=_Factory(_Client(web_output=_web_call_with_sources("https://example.com/source"))),
        ),
    )
    save_verification_snapshot(config_dir=tmp_path, bindings=bindings, reports=reports)
    text = verification_path(tmp_path).read_text(encoding="utf-8")
    assert "semantic-secret" not in text
    assert "fact-secret" not in text
    assert "web-secret" not in text
    assert assert_verified_if_managed(config_dir=tmp_path, bindings=bindings) is True

    changed = (
        RoleBinding("semantic", bindings[0].base_url, "different-model", bindings[0].api_key),
        bindings[1],
        bindings[2],
    )
    with pytest.raises(CapabilityProbeError, match="已经变化"):
        assert_verified_if_managed(config_dir=tmp_path, bindings=changed)


def test_missing_verification_snapshot_preserves_legacy_configuration(tmp_path: Path) -> None:
    assert (
        assert_verified_if_managed(
            config_dir=tmp_path,
            bindings=(
                _binding("semantic"),
                _binding("fact"),
                _binding("web"),
            ),
        )
        is False
    )


def test_binding_signature_changes_for_url_model_or_key() -> None:
    original = _binding("fact")
    assert binding_signature(original) != binding_signature(
        RoleBinding("fact", "https://other.example/v1", original.model, original.api_key)
    )
    assert binding_signature(original) != binding_signature(
        RoleBinding("fact", original.base_url, "model-b", original.api_key)
    )
    assert binding_signature(original) != binding_signature(
        RoleBinding("fact", original.base_url, original.model, "different-key")
    )
