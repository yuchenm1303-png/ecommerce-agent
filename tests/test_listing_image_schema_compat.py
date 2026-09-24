from __future__ import annotations

from pathlib import Path

import pytest

from app.listing_image_ranker import (
    ListingImageRankingError,
    _Candidate,
    _parse_ranking,
    build_listing_image_ranking_request,
)


def _candidate(image_id: str, index: int) -> _Candidate:
    return _Candidate(
        image_id=image_id,
        path=Path(f"{image_id}.jpg"),
        sha256=f"sha-{image_id}",
        source_index=index,
    )


def test_gallery_schema_avoids_openai_compatible_unsupported_array_keywords() -> None:
    candidates = [_candidate("image_01", 1), _candidate("image_02", 2)]

    request = build_listing_image_ranking_request(
        product_context={"name": "Example product"},
        candidates=candidates,
    )

    selected_schema = request["json_contract"]["properties"]["selected_image_ids"]
    assert selected_schema["type"] == "array"
    assert selected_schema["maxItems"] == 5
    for unsupported in ("uniqueItems", "contains", "minContains", "maxContains"):
        assert unsupported not in selected_schema


def test_gallery_parser_still_rejects_duplicate_selected_ids() -> None:
    candidates = [_candidate("image_01", 1), _candidate("image_02", 2)]

    with pytest.raises(ListingImageRankingError, match="contains duplicates"):
        _parse_ranking(
            {
                "selected_image_ids": ["image_01", "image_01"],
                "decisions": {
                    "image_01": {"selected": True, "reason": "main"},
                    "image_02": {"selected": False, "reason": "not selected"},
                },
                "summary": "duplicate output must fail closed",
            },
            candidates,
        )
