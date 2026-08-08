from __future__ import annotations

import pytest

from app.evidence_contract import ProductIdentity
from app.qa_catalog import QuestionCatalog, QuestionRecord
from app.semantic_extraction import (
    SemanticGroundingError,
    build_grounded_semantic_request,
    run_grounded_semantic_extraction,
    validate_grounded_semantic_packet,
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
            QuestionRecord(number="3", question="Selling Price"),
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
                content="Product model L11. Screen Size: 3.0 inch. Video output: 1080P.",
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


def packet(
    *,
    key="Screen Size",
    aliases=None,
    value="3.0 inch",
    source_reference="supplier:001:text:0001",
    source_type="supplier_web",
    evidence="Screen Size: 3.0 inch.",
):
    return {
        "extractor": "stub-model",
        "product_identity": {"model_number": "L11"},
        "facts": [
            {
                "key": key,
                "aliases": aliases or [],
                "value": value,
                "source_type": source_type,
                "source_reference": source_reference,
                "confidence": 0.88,
                "evidence_text": evidence,
            }
        ],
    }


def test_request_exposes_exact_source_ids_and_business_lock():
    payload = build_grounded_semantic_request(
        catalog(),
        grounding(),
        identity=ProductIdentity(model_number="L11"),
    )

    assert payload["grounded_sources"][0]["source_id"] == "supplier:001:text:0001"
    assert payload["grounded_sources"][1]["source_id"] == "image:001"
    assert payload["business_locked_questions"] == ["Selling Price"]
    assert "exactly equal" in payload["source_reference_rule"]
    assert payload["required_output_shape"]["facts"][0]["aliases"] == []
    assert any("every returned value" in rule for rule in payload["rules"])


def test_literal_text_evidence_is_accepted():
    validated = validate_grounded_semantic_packet(
        packet(),
        catalog(),
        grounding(),
        expected_identity=ProductIdentity(model_number="L11"),
    )

    assert len(validated.facts) == 1
    assert validated.facts[0].key == "Screen Size"
    assert validated.facts[0].source_reference == "supplier:001:text:0001"


def test_direct_value_representation_change_is_rejected_fail_closed():
    # The model should return the value as directly shown. Safe mechanical
    # normalization still exists later in the resolver for comparing independent
    # evidence sources, but it is not used to excuse a model changing its cited
    # source at this ingestion boundary.
    with pytest.raises(SemanticGroundingError, match="未机械出现在"):
        validate_grounded_semantic_packet(
            packet(value="3 inch", evidence="Screen Size: 3.0 inch."),
            catalog(),
            grounding(),
            expected_identity=ProductIdentity(model_number="L11"),
        )


def test_direct_answer_cannot_disagree_with_quoted_evidence():
    with pytest.raises(SemanticGroundingError, match="未机械出现在"):
        validate_grounded_semantic_packet(
            packet(value="3.16 inch", evidence="Screen Size: 3.0 inch."),
            catalog(),
            grounding(),
            expected_identity=ProductIdentity(model_number="L11"),
        )


def test_inferred_answer_may_differ_only_when_labeled_ai_synthesis():
    validated = validate_grounded_semantic_packet(
        packet(
            value="3.16 inch",
            source_type="ai_synthesis",
            evidence="Screen Size: 3.0 inch.",
        ),
        catalog(),
        grounding(),
        expected_identity=ProductIdentity(model_number="L11"),
    )
    assert validated.facts[0].source_type == "ai_synthesis"


def test_model_cannot_map_unrequested_key_using_self_authored_alias():
    with pytest.raises(SemanticGroundingError, match="自造别名"):
        validate_grounded_semantic_packet(
            packet(key="Display Diagonal", aliases=["Screen Size"]),
            catalog(),
            grounding(),
            expected_identity=ProductIdentity(model_number="L11"),
        )


def test_model_aliases_are_rejected_even_when_key_is_exact():
    with pytest.raises(SemanticGroundingError, match="aliases"):
        validate_grounded_semantic_packet(
            packet(aliases=["Display Size"]),
            catalog(),
            grounding(),
            expected_identity=ProductIdentity(model_number="L11"),
        )


def test_unknown_source_reference_is_rejected():
    with pytest.raises(SemanticGroundingError, match="未提供"):
        validate_grounded_semantic_packet(
            packet(source_reference="supplier:999:text:9999"),
            catalog(),
            grounding(),
            expected_identity=ProductIdentity(model_number="L11"),
        )


def test_paraphrased_or_hallucinated_text_evidence_is_rejected():
    with pytest.raises(SemanticGroundingError, match="逐字片段"):
        validate_grounded_semantic_packet(
            packet(evidence="The product has a three inch screen."),
            catalog(),
            grounding(),
            expected_identity=ProductIdentity(model_number="L11"),
        )


def test_source_type_cannot_impersonate_manufacturer():
    with pytest.raises(SemanticGroundingError, match="不一致"):
        validate_grounded_semantic_packet(
            packet(source_type="manufacturer_doc"),
            catalog(),
            grounding(),
            expected_identity=ProductIdentity(model_number="L11"),
        )


def test_ai_synthesis_may_cite_real_source_but_remains_labeled_ai():
    validated = validate_grounded_semantic_packet(
        packet(source_type="ai_synthesis"),
        catalog(),
        grounding(),
        expected_identity=ProductIdentity(model_number="L11"),
    )
    assert validated.facts[0].source_type == "ai_synthesis"


def test_image_fact_requires_precise_visual_evidence_description():
    image_packet = {
        "extractor": "vision-stub",
        "product_identity": {"model_number": "L11"},
        "facts": [
            {
                "key": "Image Resolution",
                "aliases": [],
                "value": "1080P",
                "source_type": "product_image",
                "source_reference": "image:001",
                "confidence": 0.92,
                "evidence_text": "The product label visibly reads '1080P'.",
            }
        ],
    }
    validated = validate_grounded_semantic_packet(
        image_packet,
        catalog(),
        grounding(),
        expected_identity=ProductIdentity(model_number="L11"),
    )
    assert validated.facts[0].source_reference == "image:001"


def test_image_direct_answer_must_match_visual_evidence_description():
    image_packet = {
        "extractor": "vision-stub",
        "product_identity": {"model_number": "L11"},
        "facts": [
            {
                "key": "Image Resolution",
                "aliases": [],
                "value": "720P",
                "source_type": "product_image",
                "source_reference": "image:001",
                "confidence": 0.92,
                "evidence_text": "The product label visibly reads '1080P'.",
            }
        ],
    }
    with pytest.raises(SemanticGroundingError, match="未机械出现在"):
        validate_grounded_semantic_packet(
            image_packet,
            catalog(),
            grounding(),
            expected_identity=ProductIdentity(model_number="L11"),
        )


def test_business_question_is_rejected_even_with_real_source():
    business_packet = {
        "extractor": "stub",
        "product_identity": {"model_number": "L11"},
        "facts": [
            {
                "key": "Selling Price",
                "aliases": [],
                "value": "999",
                "source_type": "supplier_web",
                "source_reference": "supplier:001:text:0001",
                "confidence": 0.9,
                "evidence_text": "Screen Size: 3.0 inch.",
            }
        ],
    }
    with pytest.raises(Exception, match="经营字段"):
        validate_grounded_semantic_packet(
            business_packet,
            catalog(),
            grounding(),
            expected_identity=ProductIdentity(model_number="L11"),
        )


class StubProvider:
    name = "stub-provider"

    def extract_json(self, request_payload):
        assert request_payload["grounded_sources"]
        return packet()


def test_provider_output_is_validated_before_returning():
    result = run_grounded_semantic_extraction(
        StubProvider(),
        catalog(),
        grounding(),
        expected_identity=ProductIdentity(model_number="L11"),
    )
    assert result.provider_name == "stub-provider"
    assert result.packet.facts[0].value == "3.0 inch"


def _cjk_catalog(question_name: str) -> QuestionCatalog:
    return QuestionCatalog(
        source_path="qa.xlsx",
        sheet_name="Sheet1",
        header_row=3,
        questions=[QuestionRecord(number="1", question=question_name)],
    )


def _cjk_grounding() -> GroundingCatalog:
    return GroundingCatalog(
        sources=[
            GroundedSource(
                source_id="supplier:001:text:0001",
                source_type="supplier_web",
                kind=TEXT_KIND,
                origin="https://supplier.test/item",
                content="内存容量\t\n无",
            )
        ]
    )


def test_single_cjk_character_direct_value_is_accepted():
    validated = validate_grounded_semantic_packet(
        packet(
            key="Storage Capacity",
            value="无",
            evidence="内存容量\t\n无",
        ),
        _cjk_catalog("Storage Capacity"),
        _cjk_grounding(),
        expected_identity=ProductIdentity(model_number="L11"),
    )
    assert validated.facts[0].value == "无"


def test_single_cjk_character_direct_value_is_rejected_when_absent():
    with pytest.raises(SemanticGroundingError, match="未机械出现在"):
        validate_grounded_semantic_packet(
            packet(
                key="Storage Capacity",
                value="有",
                evidence="内存容量\t\n无",
            ),
            _cjk_catalog("Storage Capacity"),
            _cjk_grounding(),
            expected_identity=ProductIdentity(model_number="L11"),
        )
