from __future__ import annotations

from app.ai_decisions import BUSINESS_LOCKED, CONFLICT, MISSING, READY
from app.evidence_contract import ProductIdentity
from app.field_mapping import build_field_mapping_request, run_field_mapping
from app.semantic_grounding import GroundedSource, GroundingCatalog, IMAGE_KIND, TEXT_KIND


def field(index: int, *, business: bool = False):
    return {
        "attribute_key": "flipkart_selling_price" if business else f"field_{index}",
        "label": "Your selling price" if business else f"Field {index}",
        "section_heading": "Price, Stock and Shipping Information" if business else "Additional Description",
        "required": False,
        "multi_value": False,
        "options": [],
        "qualifier_options": [],
        "controls": [],
        "help_text": "",
    }


def grounding() -> GroundingCatalog:
    return GroundingCatalog(
        sources=[
            GroundedSource(
                source_id="supplier:001:text:0001:abc",
                source_type="supplier_web",
                kind=TEXT_KIND,
                origin="https://detail.1688.com/offer/1.html",
                content="Value: known",
                sha256="a" * 64,
            ),
            GroundedSource(
                source_id="image:001:def",
                source_type="product_image",
                kind=IMAGE_KIND,
                origin="source-page.png",
                image_path="source-page.png",
                sha256="b" * 64,
            ),
        ]
    )


class FakeMapProvider:
    name = "fake-map"

    def __init__(self, fail_call: int | None = None):
        self.calls = 0
        self.requests = []
        self.fail_call = fail_call

    def extract_json(self, request):
        self.calls += 1
        self.requests.append(request)
        if self.fail_call == self.calls:
            raise RuntimeError("batch failed")
        return {
            "ready": [
                {
                    "field_id": target["field_id"],
                    "values": ["known"],
                    "qualifier": "",
                    "confidence": 0.95,
                    "citations": [
                        {
                            "source_reference": "supplier:001:text:0001:abc",
                            "evidence_text": "Value: known",
                        }
                    ],
                }
                for target in request["target_fields"]
            ],
            "conflicts": [],
            "missing": [],
            "model_summary": "",
        }


def test_mapping_mechanically_batches_non_business_fields_from_raw_sources():
    fields = [field(index) for index in range(5)] + [field(99, business=True)]
    provider = FakeMapProvider()
    result = run_field_mapping(
        provider,
        fields,
        grounding(),
        expected_identity=ProductIdentity(sku="SKU-1"),
        product_url="https://detail.1688.com/offer/1.html",
        batch_size=2,
        concurrency=3,
    )
    assert result.batch_count == 3
    assert result.model_calls == 3
    assert len(provider.requests) == 3
    assert all(len(request["target_fields"]) <= 2 for request in provider.requests)
    assert all(any(source["kind"] == "text" for source in request["grounded_sources"]) for request in provider.requests)
    assert all(any(source["kind"] == "image" for source in request["grounded_sources"]) for request in provider.requests)
    assert all(request["task"] == "fill_marketplace_fields_from_exact_product_evidence" for request in provider.requests)
    assert result.packet.decisions[-1].status == BUSINESS_LOCKED
    assert all(item.status == READY for item in result.packet.decisions[:-1])


def test_mapping_request_is_direct_and_contains_no_intermediate_profile():
    request = build_field_mapping_request(
        [field(1)],
        grounding(),
        expected_identity=ProductIdentity(sku="SKU-1", model_number="M8"),
        product_url="https://detail.1688.com/offer/1.html",
    )
    target = request["target_fields"][0]
    assert request["task"] == "fill_marketplace_fields_from_exact_product_evidence"
    assert request["product_identity"]["source_product_url"].startswith("https://detail.1688.com/")
    assert request["strict_json_schema"] is True
    assert set(request["json_contract"]["properties"]) == {"ready", "conflicts", "missing", "model_summary"}
    missing_properties = request["json_contract"]["properties"]["missing"]["items"]["properties"]
    assert set(missing_properties) == {"field_id", "search_queries"}
    assert "reason" not in missing_properties
    assert "target_scope" not in target
    assert any(source["source_type"] == "supplier_web" for source in request["grounded_sources"])
    assert all(source["source_type"] != "derived_product_profile" for source in request["grounded_sources"])


def test_mapping_preserves_ai_semantic_ready_when_citation_is_grounded():
    class SemanticProvider(FakeMapProvider):
        def extract_json(self, request):
            self.calls += 1
            target = request["target_fields"][0]
            return {
                "ready": [
                    {
                        "field_id": target["field_id"],
                        "values": ["No"],
                        "qualifier": "",
                        "confidence": 0.9,
                        "citations": [
                            {
                                "source_reference": "supplier:001:text:0001:abc",
                                "evidence_text": "Value: known",
                            }
                        ],
                    }
                ],
                "conflicts": [],
                "missing": [],
                "model_summary": "",
            }

    target = field(1)
    target.update(attribute_key="remote_control", label="Remote Control")
    result = run_field_mapping(
        SemanticProvider(),
        [target],
        grounding(),
        expected_identity=ProductIdentity(sku="SKU-1"),
    )
    assert result.packet.decisions[0].status == READY


def test_mapping_typed_conflict_cannot_fall_back_to_missing_reason():
    class ConflictProvider(FakeMapProvider):
        def extract_json(self, request):
            target = request["target_fields"][0]
            return {
                "ready": [],
                "conflicts": [
                    {
                        "field_id": target["field_id"],
                        "confidence": 0.9,
                        "alternatives": [
                            {
                                "values": ["720p"],
                                "qualifier": "",
                                "citations": [
                                    {
                                        "source_reference": "supplier:001:text:0001:abc",
                                        "evidence_text": "720p",
                                    }
                                ],
                            },
                            {
                                "values": ["1080p"],
                                "qualifier": "",
                                "citations": [
                                    {
                                        "source_reference": "image:001:def",
                                        "evidence_text": "1080p",
                                    }
                                ],
                            },
                        ],
                    }
                ],
                "missing": [],
                "model_summary": "",
            }

    target = field(1)
    result = run_field_mapping(ConflictProvider(), [target], grounding())
    assert result.packet.decisions[0].status == CONFLICT
    assert [alt.values[0] for alt in result.packet.decisions[0].alternatives] == ["720p", "1080p"]


def test_mapping_batch_failure_only_leaves_that_batch_unresolved():
    fields = [field(index) for index in range(4)]
    provider = FakeMapProvider(fail_call=1)
    result = run_field_mapping(
        provider,
        fields,
        grounding(),
        expected_identity=ProductIdentity(sku="SKU-1"),
        batch_size=2,
        concurrency=1,
    )
    statuses = [item.status for item in result.packet.decisions]
    assert result.failed_batches == 1
    assert statuses.count(MISSING) == 2
    assert statuses.count(READY) == 2


def test_mapping_batch_cache_reuses_successful_batches(tmp_path):
    fields = [field(index) for index in range(4)]
    provider = FakeMapProvider()
    cache = tmp_path / "cache"
    kwargs = dict(
        expected_identity=ProductIdentity(sku="SKU-1"),
        product_url="https://detail.1688.com/offer/1.html",
        batch_size=2,
        concurrency=2,
        cache_dir=cache,
        cache_namespace="model",
    )
    first = run_field_mapping(provider, fields, grounding(), **kwargs)
    second = run_field_mapping(provider, fields, grounding(), **kwargs)
    assert first.model_calls == 2
    assert second.model_calls == 0
    assert second.cache_hits == 2
    assert provider.calls == 2


def test_mapping_targets_are_stable_live_field_ids():
    target = field(1)
    provider = FakeMapProvider()
    run_field_mapping(
        provider,
        [target],
        grounding(),
        expected_identity=ProductIdentity(sku="SKU-1"),
    )
    assert provider.requests[0]["target_fields"][0]["field_id"]
