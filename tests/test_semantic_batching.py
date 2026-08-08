from __future__ import annotations

from app.evidence_contract import ProductIdentity, bundle_from_evidence_packet
from app.qa_catalog import QuestionCatalog, QuestionRecord
from app.semantic_batching import (
    build_semantic_question_batches,
    run_grounded_semantic_batches,
)
from app.semantic_grounding import GroundedSource, GroundingCatalog, IMAGE_KIND, TEXT_KIND


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
                source_id="supplier:001:text:0001",
                source_type="supplier_web",
                kind=TEXT_KIND,
                origin="https://supplier.test/item",
                content=(
                    "Screen Size: 3.0 inch. Image Resolution: 1080P. "
                    "Color: Black."
                ),
            )
        ]
    )


def test_batches_skip_answered_and_business_questions():
    batches = build_semantic_question_batches(catalog(), batch_size=2)

    assert [batch.question_numbers for batch in batches] == [("1", "2"), ("5",)]
    assert all(
        "Warranty Summary" not in [q.question for q in batch.catalog.questions]
        for batch in batches
    )
    assert all(
        "Selling Price" not in [q.question for q in batch.catalog.questions]
        for batch in batches
    )


class Provider:
    name = "batch-stub"

    def extract_json(self, request_payload):
        batch_id = request_payload["batch_id"]
        if batch_id == "batch-001":
            return {
                "extractor": self.name,
                "product_identity": {"model_number": "L11"},
                "facts": [
                    {
                        "key": "Screen Size",
                        "aliases": [],
                        "value": "3.0 inch",
                        "source_type": "supplier_web",
                        "source_reference": "supplier:001:text:0001",
                        "confidence": 0.88,
                        "evidence_text": "Screen Size: 3.0 inch.",
                    },
                    {
                        "key": "Image Resolution",
                        "aliases": [],
                        "value": "1080P",
                        "source_type": "supplier_web",
                        "source_reference": "supplier:001:text:0001",
                        "confidence": 0.88,
                        "evidence_text": "Image Resolution: 1080P.",
                    },
                ],
            }
        return {
            "extractor": self.name,
            "product_identity": {"model_number": "L11"},
            "facts": [
                {
                    "key": "Color",
                    "aliases": [],
                    "value": "Black",
                    "source_type": "supplier_web",
                    "source_reference": "supplier:001:text:0001",
                    "confidence": 0.88,
                    "evidence_text": "Color: Black.",
                }
            ],
        }


def test_batch_runner_merges_only_validated_packets():
    result = run_grounded_semantic_batches(
        Provider(),
        catalog(),
        grounding(),
        expected_identity=ProductIdentity(model_number="L11"),
        batch_size=2,
    )

    assert result.completed_batches == 2
    assert result.failed_batches == 0
    assert [fact.key for fact in result.packet.facts] == [
        "Screen Size",
        "Image Resolution",
        "Color",
    ]


class PartiallyBadProvider(Provider):
    name = "partial-stub"

    def extract_json(self, request_payload):
        if request_payload["batch_id"] == "batch-002":
            return {
                "extractor": self.name,
                "product_identity": {"model_number": "L11"},
                "facts": [
                    {
                        "key": "Color",
                        "aliases": [],
                        "value": "Black",
                        "source_type": "supplier_web",
                        "source_reference": "invented-source",
                        "confidence": 0.9,
                        "evidence_text": "Color: Black.",
                    }
                ],
            }
        return super().extract_json(request_payload)


def test_bad_batch_is_excluded_and_questions_remain_for_review():
    result = run_grounded_semantic_batches(
        PartiallyBadProvider(),
        catalog(),
        grounding(),
        expected_identity=ProductIdentity(model_number="L11"),
        batch_size=2,
        continue_on_batch_error=True,
    )

    assert result.completed_batches == 1
    assert result.failed_batches == 1
    assert result.failures[0].question_numbers == ("5",)
    assert [fact.key for fact in result.packet.facts] == ["Screen Size", "Image Resolution"]
    assert any("questions remain blocked" in warning for warning in result.warnings)


class RepairableProvider:
    name = "repair-stub"

    def __init__(self):
        self.calls = 0

    @staticmethod
    def _fact(batch_id: str, *, with_evidence: bool) -> dict:
        if batch_id == "batch-002":
            fact = {
                "key": "Color",
                "aliases": [],
                "value": "Black",
                "source_type": "supplier_web",
                "source_reference": "supplier:001:text:0001",
                "confidence": 0.88,
                "evidence_text": "Color: Black.",
            }
        else:
            fact = {
                "key": "Screen Size",
                "aliases": [],
                "value": "3.0 inch",
                "source_type": "supplier_web",
                "source_reference": "supplier:001:text:0001",
                "confidence": 0.88,
                "evidence_text": "Screen Size: 3.0 inch.",
            }
        if not with_evidence:
            fact.pop("evidence_text")
        return fact

    def extract_json(self, request_payload):
        self.calls += 1
        batch_id = request_payload["batch_id"]
        repaired = "validation_error" in request_payload
        return {
            "extractor": self.name,
            "product_identity": {"model_number": "L11"},
            "facts": [self._fact(batch_id, with_evidence=repaired)],
        }


def test_repair_loop_reprompts_with_validation_error_and_recovers():
    provider = RepairableProvider()
    result = run_grounded_semantic_batches(
        provider,
        catalog(),
        grounding(),
        expected_identity=ProductIdentity(model_number="L11"),
        batch_size=2,
    )

    assert provider.calls == 4  # batch-001 and batch-002 each need one repair
    assert result.completed_batches == 2
    assert result.failed_batches == 0
    assert [fact.key for fact in result.packet.facts] == ["Screen Size", "Color"]


def test_repair_loop_keeps_failing_batch_isolated_after_max_attempts():
    provider = PartiallyBadProvider()
    result = run_grounded_semantic_batches(
        provider,
        catalog(),
        grounding(),
        expected_identity=ProductIdentity(model_number="L11"),
        batch_size=2,
        continue_on_batch_error=True,
    )

    # batch-002 is invalid on every attempt; the repair loop must not turn it
    # into a success and other batches must still complete.
    assert result.completed_batches == 1
    assert result.failed_batches == 1
    assert result.failures[0].question_numbers == ("5",)
    assert [fact.key for fact in result.packet.facts] == ["Screen Size", "Image Resolution"]


class PlaceholderProvider:
    name = "placeholder-stub"

    def __init__(self):
        self.calls = 0

    def extract_json(self, request_payload):
        self.calls += 1
        facts = [
            {
                "key": "Screen Size",
                "aliases": [],
                "value": "3.0 inch",
                "source_type": "supplier_web",
                "source_reference": "supplier:001:text:0001",
                "confidence": 0.88,
                "evidence_text": "Screen Size: 3.0 inch.",
            },
            {
                "key": "Image Resolution",
                "aliases": [],
                "value": [],
                "source_type": "",
                "source_reference": "",
                "confidence": 0.0,
                "evidence_text": "",
            },
        ]
        if request_payload["batch_id"] == "batch-002":
            facts = [
                {
                    "key": "Color",
                    "aliases": [],
                    "value": "Black",
                    "source_type": "supplier_web",
                    "source_reference": "supplier:001:text:0001",
                    "confidence": 0.88,
                    "evidence_text": "Color: Black.",
                }
            ]
        return {
            "extractor": self.name,
            "product_identity": {"model_number": "L11"},
            "facts": facts,
        }


def test_empty_value_placeholder_facts_do_not_fail_batch():
    provider = PlaceholderProvider()
    result = run_grounded_semantic_batches(
        provider,
        catalog(),
        grounding(),
        expected_identity=ProductIdentity(model_number="L11"),
        batch_size=2,
    )

    assert provider.calls == 2  # one pass per batch, no repair needed
    assert result.completed_batches == 2
    assert result.failed_batches == 0
    assert [fact.key for fact in result.packet.facts] == ["Screen Size", "Color"]
    assert any("empty-value fact ignored" in warning for warning in result.warnings)


def _image_text_grounding() -> GroundingCatalog:
    return GroundingCatalog(
        sources=[
            GroundedSource(
                source_id="supplier:001:text:0001",
                source_type="supplier_web",
                kind=TEXT_KIND,
                origin="https://supplier.test/item",
                content="Screen Size: 3.0 inch.",
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


def _single_question_catalog() -> QuestionCatalog:
    return QuestionCatalog(
        source_path="qa.xlsx",
        sheet_name="Sheet1",
        header_row=3,
        questions=[QuestionRecord(number="1", question="Screen Size")],
    )


class SourceSplitProvider:
    """Returns one fact per grounded source; values disagree across sources."""

    name = "source-split-stub"

    def __init__(self):
        self.calls = []

    def extract_json(self, request_payload):
        sources = request_payload["grounded_sources"]
        assert len(sources) == 1  # per-source passes must stay isolated
        source = sources[0]
        self.calls.append(source["source_id"])
        if source["source_id"] == "supplier:001:text:0001":
            fact = {
                "key": "Screen Size",
                "aliases": [],
                "value": "3.0 inch",
                "source_type": "supplier_web",
                "source_reference": source["source_id"],
                "confidence": 0.9,
                "evidence_text": "Screen Size: 3.0 inch.",
            }
        else:
            fact = {
                "key": "Screen Size",
                "aliases": [],
                "value": "3.16 inch",
                "source_type": "product_image",
                "source_reference": source["source_id"],
                "confidence": 0.9,
                "evidence_text": "Visible printed specification: 3.16 inch.",
            }
        return {
            "extractor": self.name,
            "product_identity": {"model_number": "L11"},
            "facts": [fact],
            "warnings": [],
        }


def test_per_source_extraction_recalls_evidence_from_every_source():
    provider = SourceSplitProvider()
    result = run_grounded_semantic_batches(
        provider,
        _single_question_catalog(),
        _image_text_grounding(),
        expected_identity=ProductIdentity(model_number="L11"),
        batch_size=1,
    )

    assert provider.calls == ["supplier:001:text:0001", "image:001"]
    assert result.completed_batches == 1
    assert result.failed_batches == 0
    facts = {(fact.source_reference, fact.value) for fact in result.packet.facts}
    assert facts == {
        ("supplier:001:text:0001", "3.0 inch"),
        ("image:001", "3.16 inch"),
    }


def test_per_source_conflicting_values_reach_resolver_as_conflict():
    from app.answer_resolver import CONFLICT
    from app.resolution_engine import resolve_one

    provider = SourceSplitProvider()
    result = run_grounded_semantic_batches(
        provider,
        _single_question_catalog(),
        _image_text_grounding(),
        expected_identity=ProductIdentity(model_number="L11"),
        batch_size=1,
    )
    bundle = bundle_from_evidence_packet(
        result.packet,
        expected_identity=ProductIdentity(model_number="L11"),
    )
    field = {"attribute_key": "screen_size", "label": "Screen Size", "controls": []}

    record = resolve_one(field, bundle)

    assert record.status == CONFLICT
    assert record.eligible_for_autofill is False
    assert len(record.provenance) == 2
    assert {item["source_reference"] for item in record.provenance} == {
        "supplier:001:text:0001",
        "image:001",
    }


class CrossSourceCitingProvider:
    """Always cites the text source, even during an image-only pass."""

    name = "cross-citing-stub"

    def __init__(self):
        self.calls = 0

    def extract_json(self, request_payload):
        self.calls += 1
        return {
            "extractor": self.name,
            "product_identity": {"model_number": "L11"},
            "facts": [
                {
                    "key": "Screen Size",
                    "aliases": [],
                    "value": "3.0 inch",
                    "source_type": "supplier_web",
                    "source_reference": "supplier:001:text:0001",
                    "confidence": 0.9,
                    "evidence_text": "Screen Size: 3.0 inch.",
                }
            ],
        }


def test_per_source_pass_cannot_cite_a_source_outside_its_pass():
    provider = CrossSourceCitingProvider()
    result = run_grounded_semantic_batches(
        provider,
        _single_question_catalog(),
        _image_text_grounding(),
        expected_identity=ProductIdentity(model_number="L11"),
        batch_size=1,
        continue_on_batch_error=True,
    )

    # The text pass succeeds; the image-only pass fails closed because the
    # model cited a source that was not supplied in that pass.
    assert result.completed_batches == 1
    assert result.failed_batches == 1
    assert [fact.source_reference for fact in result.packet.facts] == [
        "supplier:001:text:0001"
    ]
    assert "[source=image:001]" in result.failures[0].error
