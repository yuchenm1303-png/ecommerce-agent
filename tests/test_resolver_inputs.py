from __future__ import annotations

import json

import pytest

from app.evidence_contract import IdentityMismatchError
from app.evidence_validation import EvidenceValidationError
from app.qa_catalog import QuestionCatalog, QuestionRecord
from app.resolver_inputs import (
    ResolutionInputSpec,
    build_resolution_inputs,
    customer_context_for_resolution,
)


def catalog() -> QuestionCatalog:
    return QuestionCatalog(
        source_path="qa.xlsx",
        sheet_name="Sheet1",
        header_row=3,
        questions=[QuestionRecord(number="1", question="Image Resolution")],
    )


def catalog_with_identity() -> QuestionCatalog:
    return QuestionCatalog(
        source_path="qa.xlsx",
        sheet_name="Sheet1",
        header_row=3,
        questions=[
            QuestionRecord(number="1", question="Model Number", answer="L11", source_reference="qa.xlsx:row=4"),
            QuestionRecord(number="2", question="Brand", answer="SHANMING", source_reference="qa.xlsx:row=5"),
            QuestionRecord(number="3", question="Image Resolution"),
        ],
    )


def test_packet_is_loaded_through_catalog_validation(tmp_path):
    packet = tmp_path / "packet.json"
    packet.write_text(
        json.dumps(
            {
                "extractor": "vision",
                "product_identity": {"model_number": "L11", "brand": "SHANMING"},
                "facts": [
                    {
                        "key": "Video Resolution",
                        "aliases": ["Image Resolution"],
                        "value": "1920x1080",
                        "source_type": "product_image",
                        "source_reference": "front.jpg:spec",
                        "confidence": 0.96,
                        "evidence_text": "1080P",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = build_resolution_inputs(
        catalog(),
        ResolutionInputSpec(
            expected_model="L11",
            expected_brand="shanming",
            evidence_packets=(str(packet),),
        ),
    )

    candidates = result.bundle.candidates(["Image Resolution"])
    assert len(candidates) == 1
    assert candidates[0].value == "1920x1080"
    assert result.evidence_packet_files == [str(packet.resolve())]


def test_packet_cannot_inject_question_not_in_catalog(tmp_path):
    packet = tmp_path / "packet.json"
    packet.write_text(
        json.dumps(
            {
                "facts": [
                    {
                        "key": "Sensor Vendor",
                        "value": "Example",
                        "source_type": "supplier_web",
                        "source_reference": "https://example.test/item",
                        "confidence": 0.9,
                        "evidence_text": "Sensor: Example",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(EvidenceValidationError):
        build_resolution_inputs(
            catalog(),
            ResolutionInputSpec(evidence_packets=(str(packet),)),
        )


def test_expected_identity_is_automatically_derived_from_explicit_qa_answers():
    result = build_resolution_inputs(catalog_with_identity(), ResolutionInputSpec())

    assert result.expected_identity.model_number == "L11"
    assert result.expected_identity.brand == "SHANMING"


def test_derived_identity_blocks_wrong_product_packet(tmp_path):
    packet = tmp_path / "wrong-product.json"
    packet.write_text(
        json.dumps(
            {
                "product_identity": {"model_number": "L12", "brand": "SHANMING"},
                "facts": [
                    {
                        "key": "Image Resolution",
                        "value": "1920x1080",
                        "source_type": "product_image",
                        "source_reference": "wrong.jpg:spec",
                        "confidence": 0.95,
                        "evidence_text": "1080P",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(IdentityMismatchError):
        build_resolution_inputs(
            catalog_with_identity(),
            ResolutionInputSpec(evidence_packets=(str(packet),)),
        )


def test_explicit_identity_conflicting_with_trusted_qa_is_rejected():
    with pytest.raises(ValueError, match="显式 Model Number"):
        build_resolution_inputs(
            catalog_with_identity(),
            ResolutionInputSpec(expected_model="L99"),
        )


def test_customer_context_is_retained_exactly_once_for_grounding_and_rebind():
    preamble = (
        "Selected Variant: M8 dual camera + 64GB card\n"
        "Supplier URL: https://supplier.test/item/850845635717"
    )
    qa = QuestionCatalog(
        source_path="qa.xlsx",
        sheet_name="Sheet1",
        header_row=4,
        preamble_text=preamble,
        questions=[QuestionRecord(number="1", question="Image Resolution")],
    )
    spec = ResolutionInputSpec()

    result = build_resolution_inputs(qa, spec)
    canonical = customer_context_for_resolution(qa, spec)

    assert canonical == preamble
    assert result.bundle.supplemental_text == canonical
    assert result.bundle.supplemental_text.count("Selected Variant") == 1
    assert result.bundle.supplemental_text.count("Supplier URL") == 1
    # Key/value parsing may add deterministic evidence, but must never append a
    # second copy of the same customer source and therefore change its hash.
    assert result.bundle.candidates(["Selected Variant"])[0].value == "M8 dual camera + 64GB card"
