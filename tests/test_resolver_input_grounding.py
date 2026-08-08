from __future__ import annotations

import json

import pytest

from app.qa_catalog import QuestionCatalog, QuestionRecord
from app.resolver_inputs import ResolutionInputSpec, build_resolution_inputs
from app.semantic_extraction import SemanticGroundingError
from app.semantic_grounding import build_grounding_catalog


def _catalog() -> QuestionCatalog:
    return QuestionCatalog(
        source_path="qa.xlsx",
        sheet_name="Sheet1",
        header_row=3,
        questions=[QuestionRecord(number="1", question="Model Number")],
    )


def _packet_for_image(tmp_path, image_path):
    grounding = build_grounding_catalog(image_paths=[str(image_path)])
    source_id = grounding.sources[0].source_id
    packet = tmp_path / "semantic.json"
    packet.write_text(
        json.dumps(
            {
                "extractor": "test",
                "product_identity": {"sku": "SKU-1", "model_number": "", "brand": ""},
                "facts": [
                    {
                        "key": "Model Number",
                        "value": "M8",
                        "source_type": "product_image",
                        "source_reference": source_id,
                        "confidence": 0.95,
                        "evidence_text": "Model Number M8 is visibly printed",
                        "aliases": [],
                    }
                ],
                "warnings": [],
            }
        ),
        encoding="utf-8",
    )
    return packet


def test_grounded_semantic_packet_is_rebound_to_current_image(tmp_path):
    image = tmp_path / "product.png"
    image.write_bytes(b"image-version-one")
    packet = _packet_for_image(tmp_path, image)

    result = build_resolution_inputs(
        _catalog(),
        ResolutionInputSpec(
            sku="SKU-1",
            evidence_packets=(str(packet),),
            image_paths=(str(image),),
        ),
    )

    assert result.grounded_packet_files == [str(packet.resolve())]
    assert any("rebound to current source universe" in item for item in result.warnings)
    facts = result.bundle.candidates(("Model Number",))
    assert any(item.value == "M8" for item in facts)


def test_grounded_semantic_packet_fails_after_source_image_changes(tmp_path):
    image = tmp_path / "product.png"
    image.write_bytes(b"image-version-one")
    packet = _packet_for_image(tmp_path, image)

    image.write_bytes(b"image-version-two")

    with pytest.raises(SemanticGroundingError, match="未提供的 source_reference"):
        build_resolution_inputs(
            _catalog(),
            ResolutionInputSpec(
                sku="SKU-1",
                evidence_packets=(str(packet),),
                image_paths=(str(image),),
            ),
        )
