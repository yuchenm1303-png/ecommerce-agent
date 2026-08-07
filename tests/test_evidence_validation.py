from __future__ import annotations

import pytest

from app.evidence_contract import EvidencePacket, ProductIdentity
from app.evidence_validation import EvidenceValidationError, validate_evidence_packet
from app.qa_catalog import QuestionCatalog, QuestionRecord


def catalog() -> QuestionCatalog:
    return QuestionCatalog(
        source_path="qa.xlsx",
        sheet_name="Sheet1",
        header_row=3,
        questions=[
            QuestionRecord(number="1", question="Image Resolution"),
            QuestionRecord(number="2", question="Selling Price"),
        ],
    )


def packet_for(facts):
    return EvidencePacket.from_mapping(
        {
            "extractor": "vision-test",
            "product_identity": {"model_number": "L11", "brand": "SHANMING"},
            "facts": facts,
        }
    )


def fact(key: str, value: str = "1920x1080", source_type: str = "product_image"):
    return {
        "key": key,
        "value": value,
        "source_type": source_type,
        "source_reference": "front.jpg:spec-table",
        "confidence": 0.96,
        "evidence_text": "1080P",
    }


def test_packet_fact_is_canonicalized_to_exact_qa_question():
    item = fact("video resolution")
    item["aliases"] = ["Image Resolution"]
    result = validate_evidence_packet(
        packet_for([item]),
        catalog(),
        expected_identity=ProductIdentity(model_number="L11", brand="shanming"),
    )

    assert result.normalized_fact_count == 1
    assert result.packet.facts[0].key == "Image Resolution"
    assert "video resolution" in result.packet.facts[0].aliases


def test_unrequested_generic_fact_is_rejected():
    with pytest.raises(EvidenceValidationError, match="不属于当前 QA"):
        validate_evidence_packet(packet_for([fact("Sensor Vendor")]), catalog())


def test_business_question_cannot_arrive_from_image_or_ai_packet():
    with pytest.raises(EvidenceValidationError, match="经营字段"):
        validate_evidence_packet(
            packet_for([fact("Selling Price", "999", "product_image")]),
            catalog(),
        )


def test_external_packet_cannot_claim_structured_source_type():
    with pytest.raises(EvidenceValidationError, match="不能伪装"):
        validate_evidence_packet(
            packet_for([fact("Image Resolution", source_type="structured")]),
            catalog(),
        )


def test_exact_duplicate_fact_is_deduplicated_with_warning():
    item = fact("Image Resolution")
    result = validate_evidence_packet(packet_for([item, dict(item)]), catalog())

    assert result.normalized_fact_count == 1
    assert any("duplicate fact ignored" in warning for warning in result.warnings)
