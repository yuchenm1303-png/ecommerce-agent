from __future__ import annotations

import copy
from pathlib import Path

import pytest

from app.listing_image_ranker import ListingImageRankingError, _Candidate, _parse_ranking
from app.providers.openai_compatible import (
    OpenAICompatibleSemanticProvider,
    _transport_json_schema,
)


def _provider_without_network(captured: dict[str, object]) -> OpenAICompatibleSemanticProvider:
    provider = object.__new__(OpenAICompatibleSemanticProvider)
    provider.model = "qwen3.7-plus"
    provider.image_detail = "auto"
    provider.max_output_tokens = 12000
    provider.structured_mode = "prompt_only"
    provider.compat_profile = "generic"
    provider.request_timeout_seconds = 120.0
    provider.enable_thinking = None
    provider.progress_callback = None

    def fake_network_text(kwargs: dict[str, object], *, streaming: bool) -> str:
        captured["kwargs"] = kwargs
        captured["streaming"] = streaming
        return '{"ok":true}'

    provider._network_text = fake_network_text  # type: ignore[method-assign]
    return provider


def test_transport_schema_strips_only_nonportable_array_keywords() -> None:
    canonical = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "selected_image_ids": {
                "type": "array",
                "minItems": 0,
                "maxItems": 5,
                "uniqueItems": True,
                "contains": {"type": "string"},
                "minContains": 0,
                "maxContains": 5,
                "items": {"type": "string", "enum": ["image_01", "image_02"]},
            }
        },
        "required": ["selected_image_ids"],
    }
    original = copy.deepcopy(canonical)

    portable = _transport_json_schema(canonical)
    selected = portable["properties"]["selected_image_ids"]

    assert canonical == original
    assert selected["type"] == "array"
    assert selected["minItems"] == 0
    assert selected["maxItems"] == 5
    assert selected["items"] == canonical["properties"]["selected_image_ids"]["items"]
    for keyword in ("uniqueItems", "contains", "minContains", "maxContains"):
        assert keyword not in selected


def test_strict_response_format_uses_portable_schema_but_prompt_keeps_canonical_contract() -> None:
    captured: dict[str, object] = {}
    provider = _provider_without_network(captured)
    canonical = {
        "type": "object",
        "properties": {
            "ids": {
                "type": "array",
                "maxItems": 5,
                "uniqueItems": True,
                "items": {"type": "string"},
            }
        },
        "required": ["ids"],
    }

    result = provider.extract_json(
        {
            "task": "portable_schema_test",
            "json_contract": canonical,
            "strict_json_schema": True,
            "grounded_sources": [],
        }
    )

    assert result["ok"] is True
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    response_schema = kwargs["response_format"]["json_schema"]["schema"]
    assert "uniqueItems" not in response_schema["properties"]["ids"]
    assert response_schema["properties"]["ids"]["maxItems"] == 5

    user_content = kwargs["messages"][1]["content"]
    assert isinstance(user_content, list)
    prompt_text = user_content[0]["text"]
    assert '"uniqueItems":true' in prompt_text


def test_gallery_parser_still_rejects_duplicate_selected_ids() -> None:
    candidates = [
        _Candidate("image_01", Path("image_01.jpg"), "a" * 64, 1),
        _Candidate("image_02", Path("image_02.jpg"), "b" * 64, 2),
    ]

    with pytest.raises(ListingImageRankingError, match="contains duplicates"):
        _parse_ranking(
            {
                "selected_image_ids": ["image_01", "image_01"],
                "decisions": {
                    "image_01": {"selected": True, "reason": "best"},
                    "image_02": {"selected": False, "reason": "not selected"},
                },
                "summary": "duplicate should fail application validation",
            },
            candidates,
        )
