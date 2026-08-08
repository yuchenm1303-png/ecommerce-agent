from __future__ import annotations

import pytest

from app.evidence_contract import (
    EvidenceContractError,
    EvidencePacket,
    IdentityMismatchError,
    ProductIdentity,
    assert_identity_compatible,
    bundle_from_evidence_packet,
)


def test_evidence_packet_requires_traceable_evidence_text():
    with pytest.raises(EvidenceContractError):
        EvidencePacket.from_mapping(
            {
                "facts": [
                    {
                        "key": "Image Resolution",
                        "value": "1920x1080",
                        "source_type": "product_image",
                        "source_reference": "image:1",
                        "confidence": 0.95,
                    }
                ]
            }
        )


def test_identity_mismatch_fails_closed():
    expected = ProductIdentity(model_number="L11", brand="SHANMING")
    observed = ProductIdentity(model_number="L12", brand="SHANMING")

    with pytest.raises(IdentityMismatchError):
        assert_identity_compatible(expected, observed)


def test_packet_converts_to_bundle_with_provenance():
    packet = EvidencePacket.from_mapping(
        {
            "extractor": "vision-test",
            "product_identity": {"model_number": "L11", "brand": "SHANMING"},
            "facts": [
                {
                    "key": "Video Resolution",
                    "aliases": ["Image Resolution"],
                    "value": "1920x1080",
                    "source_type": "product_image",
                    "source_reference": "image:front:crop=spec-table",
                    "confidence": 0.96,
                    "evidence_text": "1080P",
                    "note": "printed on packaging",
                }
            ],
        }
    )

    bundle = bundle_from_evidence_packet(
        packet,
        expected_identity=ProductIdentity(model_number="L11", brand="shanming"),
    )

    evidence = bundle.candidates(["Image Resolution"])
    assert len(evidence) == 1
    assert evidence[0].value == "1920x1080"
    assert evidence[0].source_reference == "image:front:crop=spec-table"
    assert evidence[0].evidence_text == "1080P"
    assert evidence[0].note == "printed on packaging"


def test_empty_value_placeholder_fact_is_treated_as_omitted():
    packet = EvidencePacket.from_mapping(
        {
            "extractor": "stub",
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
                    "key": "Display Resolution",
                    "aliases": [],
                    "value": [],
                    "source_type": "",
                    "source_reference": "",
                    "confidence": 0.0,
                    "evidence_text": "",
                },
            ],
        }
    )

    assert [fact.key for fact in packet.facts] == ["Screen Size"]
    assert any("empty-value fact ignored: Display Resolution" in w for w in packet.warnings)
