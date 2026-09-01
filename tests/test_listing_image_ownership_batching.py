from __future__ import annotations

from pathlib import Path

import pytest

from app.listing_image_ranker import (
    OWNERSHIP_BATCH_SIZE,
    _Candidate,
    _run_ownership_request,
    build_listing_image_ownership_request,
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
        raw_ids = context.get("candidate_image_ids")
        assert isinstance(raw_ids, list)
        image_ids = [str(value) for value in raw_ids]
        self.batch_ids.append(image_ids)
        if self.fail_on_call == self.calls:
            raise RuntimeError("synthetic ownership transport failure")
        return {
            "decisions": {
                image_id: {
                    "visual_subject": "product photo",
                    "visible_identity": "Brand Target",
                    "visible_configuration": "target configuration",
                    "target_conflicts": "",
                    "target_match_evidence": "Synthetic pixels positively establish the target identity.",
                    "target_identity_gaps": "",
                    "classification": "EXACT_TARGET",
                    "confidence": 0.9,
                    "reason": "synthetic exact-target decision",
                }
                for image_id in image_ids
            },
            "summary": f"classified {len(image_ids)} candidates",
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


def test_ownership_request_is_bounded_and_merged_in_original_order() -> None:
    candidates = _candidates(15)
    request = build_listing_image_ownership_request(
        product_context={"name": "Target", "brand": "Brand"},
        candidates=candidates,
    )
    provider = _BatchProvider()

    parsed = _run_ownership_request(provider, request, candidates)

    assert OWNERSHIP_BATCH_SIZE == 6
    assert [len(batch) for batch in provider.batch_ids] == [6, 6, 3]
    assert parsed.model_calls == 3
    assert list(parsed.decisions) == [candidate.image_id for candidate in candidates]
    assert parsed.eligible_ids == tuple(candidate.image_id for candidate in candidates)
    for batch in provider.batch_ids:
        assert len(batch) <= OWNERSHIP_BATCH_SIZE


def test_ownership_batch_failure_preserves_fail_closed_call_count() -> None:
    candidates = _candidates(15)
    request = build_listing_image_ownership_request(
        product_context={"name": "Target", "brand": "Brand"},
        candidates=candidates,
    )
    provider = _BatchProvider(fail_on_call=2)

    with pytest.raises(RuntimeError, match="synthetic ownership transport failure") as caught:
        _run_ownership_request(provider, request, candidates)

    assert provider.calls == 2
    assert getattr(caught.value, "listing_image_ownership_model_calls") == 2
