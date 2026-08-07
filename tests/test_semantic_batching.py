from __future__ import annotations

from app.evidence_contract import ProductIdentity
from app.qa_catalog import QuestionCatalog, QuestionRecord
from app.semantic_batching import (
    build_semantic_question_batches,
    run_grounded_semantic_batches,
)
from app.semantic_grounding import GroundedSource, GroundingCatalog, TEXT_KIND


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
