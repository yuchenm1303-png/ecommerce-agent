from __future__ import annotations

import pytest

from app.image_evidence import ImageEvidenceError, run_image_evidence
from app.semantic_grounding import GroundedSource, IMAGE_KIND


def images(count: int) -> list[GroundedSource]:
    return [
        GroundedSource(
            source_id=f"image:{index:03d}",
            source_type="product_image",
            kind=IMAGE_KIND,
            origin=f"image-{index}.jpg",
            image_path=f"image-{index}.jpg",
            sha256=f"{index:064x}",
        )
        for index in range(1, count + 1)
    ]


class FakeImageProvider:
    name = "fake-image-provider"

    def __init__(self, *, fail: bool = False):
        self.calls = 0
        self.requests = []
        self.fail = fail

    def extract_json(self, request):
        self.calls += 1
        self.requests.append(request)
        if self.fail:
            raise RuntimeError("vision unavailable")
        return {
            "images": {
                image_id: {
                    "visible_text": f"text on {image_id}",
                    "facts": [
                        {
                            "name": "dimension",
                            "scope": "packaging",
                            "value": "16 x 11 x 7",
                            "qualifier": "cm",
                            "evidence_text": f"{image_id} shows package dimensions",
                        }
                    ],
                    "notes": "",
                }
                for image_id in request["image_ids"]
            },
            "summary": "independent image observations",
        }


def test_image_evidence_is_mechanically_batched_and_cached_per_image(tmp_path):
    provider = FakeImageProvider()
    sources = images(7)
    first = run_image_evidence(
        provider,
        sources,
        batch_size=3,
        concurrency=3,
        cache_dir=tmp_path,
        cache_namespace="contract-a",
    )
    second = run_image_evidence(
        provider,
        sources,
        batch_size=2,
        concurrency=2,
        cache_dir=tmp_path,
        cache_namespace="contract-a",
    )

    assert first.batch_count == 3
    assert first.model_calls == 3
    assert first.cache_hits == 0
    assert second.batch_count == 0
    assert second.model_calls == 0
    assert second.cache_hits == 7
    assert provider.calls == 3
    assert [item.image_id for item in second.observations] == [item.source_id for item in sources]


def test_image_request_has_no_marketplace_schema_or_non_image_sources(tmp_path):
    provider = FakeImageProvider()
    run_image_evidence(provider, images(2), batch_size=2, cache_dir=tmp_path)
    request = provider.requests[0]
    assert request["task"] == "extract_independent_product_image_evidence"
    assert request["target_fields"] == []
    assert all(source["kind"] == "image" for source in request["grounded_sources"])
    assert set(request["json_contract"]["properties"]["images"]["required"]) == {
        "image:001",
        "image:002",
    }


def test_image_batch_failure_stops_before_incomplete_profile(tmp_path):
    with pytest.raises(ImageEvidenceError, match="vision unavailable"):
        run_image_evidence(
            FakeImageProvider(fail=True),
            images(2),
            batch_size=2,
            cache_dir=tmp_path,
        )
