from __future__ import annotations

import json

import pytest

from app.evidence_validation import EvidenceValidationError
from app.qa_catalog import QuestionCatalog, QuestionRecord
from app.resolver_inputs import ResolutionInputSpec, build_resolution_inputs


def catalog() -> QuestionCatalog:
    return QuestionCatalog(
        source_path="qa.xlsx",
        sheet_name="Sheet1",
        header_row=3,
        questions=[QuestionRecord(number="1", question="Image Resolution")],
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
