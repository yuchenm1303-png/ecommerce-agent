from __future__ import annotations

from pathlib import Path

import pytest

from app.listing_image_ranker import (
    VISUAL_FACTS_BATCH_SIZE,
    _Candidate,
    _run_visual_facts_request,
    build_listing_image_visual_facts_request,
)


class _BatchProvider:
    name = "fake-semantic"
    model = "fake-model"

    def __init__(self, *, fail_on_call: int | None = None) -> None:
        self.fail_on_call = fail_on_call
        self.calls = 0
        self.batch_ids: list[list[str]] = []

    def extract_json(self, request_payload: dict[str, object]) -> dict[str, object]:
        self.calls += 1
        context = request_payload.get("context")
        assert isinstance(context, dict)
        assert "target_product" not in context
        raw_ids = context.get("candidate_image_ids")
        assert isinstance(raw_ids, list)
        image_ids = [str(value) for value in raw_ids]
        self.batch_ids.append(image_ids)
        if self.fail_on_call == self.calls:
            raise RuntimeError("synthetic blind perception transport failure")
        return {
            "facts": {
                image_id: {
                    "visual_subject": "product photo",
                    "readable_identity": "Brand",
                    "raw_colour_materials": "literal neutral colour description",
                    "design_configuration": "distinctive neutral design facts",
                    "neutral_product_guess": "likely product family",
                    "visual_uncertainty": "exact catalog model uncertain",
                    "presentation_quality": "clear product view",
                }
                for image_id in image_ids
            },
            "summary": f"observed {len(image_ids)} candidates",
        }


def _candidates(count: int) -> list[_Candidate]:
    return [
        _Candidate(
            image_id=f"image_{index:02d}",
            path=Path(f"image_{index:02d}.jpg"),
            sha256=f"sha-{index:02d}",
            source_index=index,
        )
        for index in range(1, count + 1)
    ]


def test_blind_visual_perception_is_bounded_and_merged_in_original_order() -> None:
    candidates = _candidates(15)
    request = build_listing_image_visual_facts_request(candidates=candidates)
    provider = _BatchProvider()

    parsed = _run_visual_facts_request(provider, request, candidates)

    assert VISUAL_FACTS_BATCH_SIZE == 6
    assert [len(batch) for batch in provider.batch_ids] == [6, 6, 3]
    assert parsed.model_calls == 3
    assert list(parsed.facts) == [candidate.image_id for candidate in candidates]
    for batch in provider.batch_ids:
        assert len(batch) <= VISUAL_FACTS_BATCH_SIZE


def test_blind_visual_perception_failure_preserves_fail_closed_call_count() -> None:
    candidates = _candidates(15)
    request = build_listing_image_visual_facts_request(candidates=candidates)
    provider = _BatchProvider(fail_on_call=2)

    with pytest.raises(RuntimeError, match="synthetic blind perception transport failure") as caught:
        _run_visual_facts_request(provider, request, candidates)

    assert provider.calls == 2
    assert getattr(caught.value, "listing_image_visual_facts_model_calls") == 2
