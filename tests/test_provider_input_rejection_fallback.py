from __future__ import annotations

import copy

import pytest

from app.providers.input_rejection_fallback import ProductIdentityInputFallbackProvider


class _RejectImageThenSucceed:
    name = "fake-openai-compatible"
    model = "qwen-test"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def extract_json(self, payload: dict) -> dict:
        self.calls.append(copy.deepcopy(payload))
        if any(
            isinstance(item, dict) and item.get("kind") == "image"
            for item in payload.get("grounded_sources") or []
        ):
            raise RuntimeError(
                "Error code: 400 - InternalError.Algo.DataInspectionFailed: "
                "Input image data may contain inappropriate content. "
                "code=data_inspection_failed type=data_inspection_failed"
            )
        return {"status": "ok"}


class _AlwaysReject:
    name = "fake-openai-compatible"

    def __init__(self, message: str) -> None:
        self.message = message
        self.calls = 0

    def extract_json(self, payload: dict) -> dict:
        self.calls += 1
        raise RuntimeError(self.message)


def _identity_request() -> dict:
    return {
        "task": "infer_grounded_supplier_product_identity",
        "prompt_instruction": "Use only grounded sources.",
        "context": {
            "allowed_evidence_refs": ["identity:page-title", "identity:image:1"],
        },
        "grounded_sources": [
            {
                "source_id": "identity:page-title",
                "source_type": "supplier_product_heading",
                "kind": "text",
                "origin": "supplier",
                "content": "Portable neck massager",
            },
            {
                "source_id": "identity:image:1",
                "source_type": "supplier_product_image",
                "kind": "image",
                "image_path": "unused.jpg",
            },
        ],
        "json_contract": {
            "type": "object",
            "properties": {
                "evidence_refs": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["identity:page-title", "identity:image:1"],
                    },
                },
            },
        },
    }


def test_product_identity_retries_once_without_rejected_images() -> None:
    delegate = _RejectImageThenSucceed()
    provider = ProductIdentityInputFallbackProvider(delegate)

    result = provider.extract_json(_identity_request())

    assert result == {"status": "ok"}
    assert len(delegate.calls) == 2
    first, second = delegate.calls
    assert [item["kind"] for item in first["grounded_sources"]] == ["text", "image"]
    assert [item["kind"] for item in second["grounded_sources"]] == ["text"]
    assert second["context"]["allowed_evidence_refs"] == ["identity:page-title"]
    assert second["context"]["visual_evidence_status"] == "provider_rejected_image_input"
    assert second["json_contract"]["properties"]["evidence_refs"]["items"]["enum"] == [
        "identity:page-title"
    ]
    assert "Do not infer or cite any visual-only fact" in second["prompt_instruction"]


def test_non_inspection_failure_is_not_retried() -> None:
    delegate = _AlwaysReject("Error code: 400 invalid_request_error")
    provider = ProductIdentityInputFallbackProvider(delegate)

    with pytest.raises(RuntimeError, match="invalid_request_error"):
        provider.extract_json(_identity_request())

    assert delegate.calls == 1


def test_inspection_failure_without_text_evidence_is_not_retried() -> None:
    delegate = _AlwaysReject("data_inspection_failed: image rejected")
    provider = ProductIdentityInputFallbackProvider(delegate)
    request = _identity_request()
    request["grounded_sources"] = [request["grounded_sources"][1]]
    request["context"]["allowed_evidence_refs"] = ["identity:image:1"]

    with pytest.raises(RuntimeError, match="data_inspection_failed"):
        provider.extract_json(request)

    assert delegate.calls == 1


def test_other_tasks_never_use_product_identity_fallback() -> None:
    delegate = _AlwaysReject("data_inspection_failed: image rejected")
    provider = ProductIdentityInputFallbackProvider(delegate)
    request = _identity_request()
    request["task"] = "resolve_compact_product_facts"

    with pytest.raises(RuntimeError, match="data_inspection_failed"):
        provider.extract_json(request)

    assert delegate.calls == 1
