from __future__ import annotations

from pathlib import Path

from app.evidence_contract import ProductIdentity
from app.product_profile import PROFILE_CONFLICT, run_product_profile
from app.semantic_grounding import GroundedSource, GroundingCatalog, IMAGE_KIND, TEXT_KIND


def grounding(tmp_path: Path) -> GroundingCatalog:
    image = tmp_path / "product.png"
    image.write_bytes(b"fake")
    return GroundingCatalog(
        sources=[
            GroundedSource(
                source_id="image:001",
                source_type="product_image",
                kind=IMAGE_KIND,
                origin=str(image),
                image_path=str(image),
                sha256="a" * 64,
            ),
            GroundedSource(
                source_id="supplier:001:text:0001:abc",
                source_type="supplier_web",
                kind=TEXT_KIND,
                origin="supplier",
                content="Display 3.0 inch; package 16 x 11 x 7 cm",
                sha256="b" * 64,
            ),
        ]
    )


class FakeProfileProvider:
    name = "fake-profile"

    def __init__(self):
        self.calls = 0
        self.requests = []

    def extract_json(self, request):
        self.calls += 1
        self.requests.append(request)
        return {
            "facts": [
                {
                    "name": "display_size",
                    "scope": "product",
                    "status": "conflict",
                    "candidates": [
                        {
                            "value": "3.0 inch",
                            "citations": [
                                {
                                    "source_reference": "supplier:001:text:0001:abc",
                                    "evidence_text": "Display 3.0 inch",
                                }
                            ],
                        },
                        {
                            "value": "3.16 inch",
                            "citations": [
                                {
                                    "source_reference": "image:001",
                                    "evidence_text": "visible 3.16 inch display marking",
                                }
                            ],
                        },
                    ],
                }
            ],
            "summary": "M8 product profile",
        }


def test_product_profile_uses_all_raw_sources_once_and_preserves_conflict(tmp_path):
    provider = FakeProfileProvider()
    sources = grounding(tmp_path)
    result = run_product_profile(
        provider,
        sources,
        expected_identity=ProductIdentity(sku="SKU-1"),
    )
    assert provider.calls == 1
    request = provider.requests[0]
    assert request["task"] == "understand_product_from_local_evidence"
    assert request["target_fields"] == []
    assert len(request["grounded_sources"]) == 2
    assert result.profile.facts[0].status == PROFILE_CONFLICT
    assert len(result.profile.facts[0].candidates) == 2


def test_product_profile_cache_is_independent_of_live_schema(tmp_path):
    provider = FakeProfileProvider()
    sources = grounding(tmp_path)
    cache = tmp_path / "cache"
    first = run_product_profile(
        provider,
        sources,
        expected_identity=ProductIdentity(sku="SKU-1"),
        cache_dir=cache,
        cache_namespace="model",
    )
    second = run_product_profile(
        provider,
        sources,
        expected_identity=ProductIdentity(sku="SKU-1"),
        cache_dir=cache,
        cache_namespace="model",
    )
    assert first.model_calls == 1
    assert second.model_calls == 0
    assert second.cache_hit is True
    assert provider.calls == 1


def test_profile_drops_ungrounded_candidate_instead_of_trusting_it(tmp_path):
    class BadProvider(FakeProfileProvider):
        def extract_json(self, request):
            self.calls += 1
            return {
                "facts": [
                    {
                        "name": "gps",
                        "status": "supported",
                        "candidates": [
                            {
                                "value": "No",
                                "citations": [
                                    {
                                        "source_reference": "supplier:001:text:0001:abc",
                                        "evidence_text": "GPS: No",
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }

    result = run_product_profile(BadProvider(), grounding(tmp_path))
    assert result.profile.facts == []


def test_profile_rebinds_wrong_text_chunk_only_within_same_source_document():
    sources = GroundingCatalog(
        sources=[
            GroundedSource(
                source_id="supplier:001:text:0001:a",
                source_type="supplier_web",
                kind=TEXT_KIND,
                origin="supplier-snapshot.json",
                content="Model M8",
                sha256="a" * 64,
            ),
            GroundedSource(
                source_id="supplier:001:text:0002:b",
                source_type="supplier_web",
                kind=TEXT_KIND,
                origin="supplier-snapshot.json",
                content="Package 16 x 11 x 7 cm; 285 g",
                sha256="b" * 64,
            ),
        ]
    )

    class WrongChunkProvider:
        name = "wrong-chunk"

        def extract_json(self, request):
            return {
                "facts": [
                    {
                        "name": "packaging_dimensions",
                        "scope": "packaging",
                        "status": "supported",
                        "candidates": [
                            {
                                "value": "16 x 11 x 7 cm",
                                "citations": [
                                    {
                                        "source_reference": "supplier:001:text:0001:a",
                                        "evidence_text": "Package 16 x 11 x 7 cm",
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }

    result = run_product_profile(WrongChunkProvider(), sources)
    citation = result.profile.facts[0].candidates[0].citations[0]
    assert citation.source_reference == "supplier:001:text:0002:b"
    assert any("citation rebound" in item for item in result.profile.warnings)
