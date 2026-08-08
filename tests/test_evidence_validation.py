from __future__ import annotations

import pytest

from app.evidence_contract import EvidenceContractError, EvidencePacket, ProductIdentity
from app.evidence_validation import validate_evidence_packet
from app.qa_catalog import QuestionCatalog, QuestionRecord


def catalog():
    return QuestionCatalog(
        source_path="qa.xlsx",
        sheet_name="Sheet1",
        header_row=3,
        questions=[
            QuestionRecord(number="1", question="Image Resolution"),
            QuestionRecord(number="2", question="Selling Price"),
            QuestionRecord(number="3", question="Stock"),
        ],
    )


def packet(facts, *, identity=None):
    return EvidencePacket.from_mapping(
        {
            "extractor": "fixture",
            "product_identity": identity or {},
            "facts": facts,
        }
    )


def fact(key, value="1920x1080"):
    return {
        "key": key,
        "value": value,
        "source_type": "product_image",
        "source_reference": "image:front.png",
        "confidence": 0.95,
        "evidence_text": f"visible: {value}",
    }


def test_unknown_fact_key_is_rejected():
    with pytest.raises(EvidenceContractError, match="无法唯一对应"):
        validate_evidence_packet(packet([fact("Unknown Trait")]), catalog())


def test_business_fact_is_rejected_even_if_question_exists():
    with pytest.raises(EvidenceContractError, match="经营字段"):
        validate_evidence_packet(packet([fact("Selling Price", "999")]), catalog())


def test_stock_is_business_locked_from_semantic_packet():
    with pytest.raises(EvidenceContractError, match="经营字段"):
        validate_evidence_packet(packet([fact("Stock", "20")]), catalog())


def test_model_alias_can_map_to_single_allowed_question():
    mapped = fact("Resolution")
    mapped["aliases"] = ["Image Resolution"]

    result = validate_evidence_packet(packet([mapped]), catalog())

    assert result.packet.facts[0].key == "Image Resolution"
    assert result.packet.facts[0].aliases == ()


def test_identity_mismatch_fails_closed():
    source = packet(
        [fact("Image Resolution")],
        identity={"sku": "SKU-OTHER"},
    )

    with pytest.raises(EvidenceContractError, match="商品身份不一致"):
        validate_evidence_packet(
            source,
            catalog(),
            expected_identity=ProductIdentity(sku="SKU-EXPECTED"),
        )
