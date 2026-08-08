from __future__ import annotations

from app.answer_resolver import CONFLICT
from app.evidence_contract import ProductIdentity, bundle_from_evidence_packet
from app.qa_catalog import QuestionCatalog, QuestionRecord
from app.resolution_engine import resolve_one
from app.semantic_grounding import GroundedSource, GroundingCatalog, IMAGE_KIND, TEXT_KIND
from app.semantic_sources import (
    build_semantic_pending_catalog,
    run_grounded_semantic_sources,
)


def catalog() -> QuestionCatalog:
    return QuestionCatalog(
        source_path="qa.xlsx",
        sheet_name="Sheet1",
        header_row=3,
        questions=[
            QuestionRecord(number="1", question="Screen Size"),
            QuestionRecord(number="2", question="Image Resolution"),
            QuestionRecord(number="3", question="Warranty Summary", answer="1 year"),
            QuestionRecord(number="4", question="Selling Price"),
            QuestionRecord(number="5", question="Color"),
        ],
    )


def grounding() -> GroundingCatalog:
    return GroundingCatalog(
        sources=[
            GroundedSource(
                source_id="supplier:001:text:0001:a",
                source_type="supplier_web",
                kind=TEXT_KIND,
                origin="https://supplier.test/item",
                content="Screen Size: 3.0 inch. Image Resolution: 1080P.",
            ),
            GroundedSource(
                source_id="supplier:001:text:0002:b",
                source_type="supplier_web",
                kind=TEXT_KIND,
                origin="https://supplier.test/item",
                content="Color: Black.",
            ),
            GroundedSource(
                source_id="image:001",
                source_type="product_image",
                kind=IMAGE_KIND,
                origin="/tmp/front.jpg",
                image_path="front.jpg",
            ),
        ]
    )


def test_pending_catalog_skips_answered_and_business_questions():
    pending = build_semantic_pending_catalog(catalog())
    assert [item.question for item in pending.questions] == [
        "Screen Size",
        "Image Resolution",
        "Color",
    ]


class SourceFirstProvider:
    name = "source-first-stub"

    def __init__(self):
        self.calls: list[tuple[str, tuple[str, ...], int]] = []

    def extract_json(self, request_payload):
        sources = request_payload["grounded_sources"]
        questions = tuple(item["question"] for item in request_payload["questions"])
        logical_id = request_payload["source_pass_id"]
        self.calls.append((logical_id, questions, len(sources)))

        if logical_id == "supplier:001":
            facts = [
                {
                    "key": "Screen Size",
                    "aliases": [],
                    "value": "3.0 inch",
                    "source_type": "supplier_web",
                    "source_reference": "supplier:001:text:0001:a",
                    "confidence": 0.9,
                    "evidence_text": "Screen Size: 3.0 inch.",
                },
                {
                    "key": "Image Resolution",
                    "aliases": [],
                    "value": "1080P",
                    "source_type": "supplier_web",
                    "source_reference": "supplier:001:text:0001:a",
                    "confidence": 0.9,
                    "evidence_text": "Image Resolution: 1080P.",
                },
                {
                    "key": "Color",
                    "aliases": [],
                    "value": "Black",
                    "source_type": "supplier_web",
                    "source_reference": "supplier:001:text:0002:b",
                    "confidence": 0.9,
                    "evidence_text": "Color: Black.",
                },
            ]
        else:
            facts = [
                {
                    "key": "Screen Size",
                    "aliases": [],
                    "value": "3.16 inch",
                    "source_type": "product_image",
                    "source_reference": "image:001",
                    "confidence": 0.9,
                    "evidence_text": "Screen Size visibly printed as 3.16 inch.",
                }
            ]
        return {
            "extractor": self.name,
            "product_identity": {"model_number": "L11"},
            "facts": facts,
            "warnings": [],
        }


def test_source_first_calls_each_logical_source_once_and_chunks_stay_one_call():
    provider = SourceFirstProvider()
    result = run_grounded_semantic_sources(
        provider,
        catalog(),
        grounding(),
        expected_identity=ProductIdentity(model_number="L11"),
        max_repair_attempts=0,
        cache_dir=None,
    )

    assert result.total_sources == 2
    assert result.completed_sources == 2
    assert result.failed_sources == 0
    assert result.model_calls == 2
    assert [item[0] for item in provider.calls] == ["supplier:001", "image:001"]
    assert provider.calls[0][2] == 2  # both supplier chunks in one request
    assert provider.calls[1][2] == 1
    assert all(
        questions == ("Screen Size", "Image Resolution", "Color")
        for _, questions, _ in provider.calls
    )


def test_cross_source_conflict_is_preserved_for_resolver():
    provider = SourceFirstProvider()
    result = run_grounded_semantic_sources(
        provider,
        catalog(),
        grounding(),
        expected_identity=ProductIdentity(model_number="L11"),
        max_repair_attempts=0,
        cache_dir=None,
    )
    bundle = bundle_from_evidence_packet(
        result.packet,
        expected_identity=ProductIdentity(model_number="L11"),
    )
    record = resolve_one(
        {"attribute_key": "screen_size", "label": "Screen Size", "controls": []},
        bundle,
    )

    assert record.status == CONFLICT
    assert record.eligible_for_autofill is False
    assert {item["source_reference"] for item in record.provenance} == {
        "supplier:001:text:0001:a",
        "image:001",
    }


class PartiallyBadProvider:
    name = "partial-fact-stub"

    def __init__(self):
        self.calls = 0

    def extract_json(self, request_payload):
        self.calls += 1
        source = request_payload["grounded_sources"][0]
        return {
            "extractor": self.name,
            "product_identity": {"model_number": "L11"},
            "facts": [
                {
                    "key": "Screen Size",
                    "aliases": [],
                    "value": "3.0 inch",
                    "source_type": "supplier_web",
                    "source_reference": source["source_id"],
                    "confidence": 0.9,
                    "evidence_text": "Screen Size: 3.0 inch.",
                },
                {
                    "key": "Image Resolution",
                    "aliases": [],
                    "value": "1080P",
                    "source_type": "supplier_web",
                    "source_reference": "invented-source",
                    "confidence": 0.9,
                    "evidence_text": "Image Resolution: 1080P.",
                },
            ],
            "warnings": [],
        }


def test_one_bad_fact_is_dropped_without_repeating_the_source():
    single = GroundingCatalog(sources=[grounding().sources[0]])
    provider = PartiallyBadProvider()
    result = run_grounded_semantic_sources(
        provider,
        catalog(),
        single,
        expected_identity=ProductIdentity(model_number="L11"),
        max_repair_attempts=1,
        cache_dir=None,
    )

    assert provider.calls == 1
    assert result.model_calls == 1
    assert result.completed_sources == 1
    assert result.source_stats[0].rejected_fact_count == 1
    assert [fact.key for fact in result.packet.facts] == ["Screen Size"]
    assert any("rejected semantic fact ignored" in item for item in result.warnings)


class RepairOnceProvider:
    name = "repair-source-stub"

    def __init__(self):
        self.calls = 0

    def extract_json(self, request_payload):
        self.calls += 1
        source = request_payload["grounded_sources"][0]
        source_reference = "invented-source" if self.calls == 1 else source["source_id"]
        return {
            "extractor": self.name,
            "product_identity": {"model_number": "L11"},
            "facts": [
                {
                    "key": "Screen Size",
                    "aliases": [],
                    "value": "3.0 inch",
                    "source_type": "supplier_web",
                    "source_reference": source_reference,
                    "confidence": 0.9,
                    "evidence_text": "Screen Size: 3.0 inch.",
                }
            ],
            "warnings": [],
        }


def test_all_rejected_facts_allow_only_one_bounded_source_repair():
    provider = RepairOnceProvider()
    single = GroundingCatalog(sources=[grounding().sources[0]])
    result = run_grounded_semantic_sources(
        provider,
        catalog(),
        single,
        expected_identity=ProductIdentity(model_number="L11"),
        max_repair_attempts=1,
        cache_dir=None,
    )

    assert provider.calls == 2
    assert result.model_calls == 2
    assert result.source_stats[0].repair_attempts == 1
    assert result.completed_sources == 1
    assert [fact.key for fact in result.packet.facts] == ["Screen Size"]


def test_validated_source_cache_makes_identical_retry_zero_call(tmp_path):
    single = GroundingCatalog(sources=[grounding().sources[0]])
    first_provider = PartiallyBadProvider()
    first = run_grounded_semantic_sources(
        first_provider,
        catalog(),
        single,
        expected_identity=ProductIdentity(model_number="L11"),
        max_repair_attempts=0,
        cache_dir=tmp_path,
        cache_namespace="model=qwen-test",
    )
    assert first_provider.calls == 1
    assert first.cache_hits == 0

    second_provider = PartiallyBadProvider()
    second = run_grounded_semantic_sources(
        second_provider,
        catalog(),
        single,
        expected_identity=ProductIdentity(model_number="L11"),
        max_repair_attempts=0,
        cache_dir=tmp_path,
        cache_namespace="model=qwen-test",
    )

    assert second_provider.calls == 0
    assert second.model_calls == 0
    assert second.cache_hits == 1
    assert [fact.key for fact in second.packet.facts] == ["Screen Size"]


def test_cache_is_bound_to_expected_product_identity(tmp_path):
    single = GroundingCatalog(sources=[grounding().sources[0]])
    first_provider = PartiallyBadProvider()
    run_grounded_semantic_sources(
        first_provider,
        catalog(),
        single,
        expected_identity=ProductIdentity(model_number="L11"),
        max_repair_attempts=0,
        cache_dir=tmp_path,
        cache_namespace="model=qwen-test",
    )

    second_provider = PartiallyBadProvider()
    second = run_grounded_semantic_sources(
        second_provider,
        catalog(),
        single,
        expected_identity=ProductIdentity(model_number="L12"),
        max_repair_attempts=0,
        cache_dir=tmp_path,
        cache_namespace="model=qwen-test",
    )

    # Different product identity cannot reuse L11 cache. The stub then reports
    # L11 and correctly triggers the hard identity guard.
    assert second_provider.calls == 1
