from __future__ import annotations

from app.ai_decisions import field_id
from app.compact_evidence import build_compact_evidence
from app.image_evidence import ImageFactObservation, ImageObservation
from app.live_schema import live_schema_payload, schema_field_signature
from app.makro.fields import _merge_semantic_field
from app.semantic_grounding import GroundedSource, GroundingCatalog, TEXT_KIND


def _text_source(
    source_id: str,
    *,
    source_type: str,
    origin: str,
    text: str,
    sha: str,
) -> GroundedSource:
    return GroundedSource(
        source_id=source_id,
        source_type=source_type,
        kind=TEXT_KIND,
        origin=origin,
        content="Rendered page text:\n" + text,
        sha256=sha * 64,
    )


def _value_control(
    attribute_key: str,
    label: str,
    section: str,
    *,
    index: int = 0,
    has_add_value_control: bool = False,
    required: bool = False,
) -> dict[str, object]:
    return {
        "id": attribute_key,
        "name": f"{attribute_key}_{index}_value",
        "field_kind": "input",
        "label": label,
        "section_heading": section,
        "required": required,
        "has_add_value_control": has_add_value_control,
        "options": [],
    }


def test_compact_evidence_keeps_later_customer_document_chunks() -> None:
    grounding = GroundingCatalog(
        [
            _text_source(
                "customer-file:001:text:0001:a",
                source_type="customer_file",
                origin="file:///spec.docx#evidence=visible-text",
                text="Product overview and material information.",
                sha="a",
            ),
            _text_source(
                "customer-file:001:text:0002:b",
                source_type="customer_file",
                origin="file:///spec.docx#evidence=visible-text",
                text="Sales Package: 1 x Hardwire Kit; 1 x User Manual.",
                sha="b",
            ),
            _text_source(
                "customer-file:002:text:0001:c",
                source_type="customer_file",
                origin="file:///customer-note.txt#evidence=visible-text",
                text="Supported use case: vehicle dash camera installation.",
                sha="c",
            ),
            _text_source(
                "supplier:001:text:0001:d",
                source_type="supplier_web",
                origin="https://supplier.test/item#evidence=visible-text",
                text="Supplier first storefront chunk.",
                sha="d",
            ),
            _text_source(
                "supplier:001:text:0002:e",
                source_type="supplier_web",
                origin="https://supplier.test/item#evidence=visible-text",
                text="Supplier duplicate storefront tail.",
                sha="e",
            ),
        ]
    )

    compact = build_compact_evidence(grounding, [])

    assert "Product overview and material information" in compact.web_text
    assert "Sales Package: 1 x Hardwire Kit; 1 x User Manual" in compact.web_text
    assert "Supported use case: vehicle dash camera installation" in compact.web_text
    assert "Supplier first storefront chunk" in compact.web_text
    assert "Supplier duplicate storefront tail" not in compact.web_text


def test_compact_evidence_preserves_image_ocr_without_reinflating_model_notes() -> None:
    observation = ImageObservation(
        image_id="image:001:abc",
        origin="package.jpg",
        sha256="f" * 64,
        visible_text="BOX CONTENTS 1 x HARDWIRE KIT 1 x USER MANUAL",
        facts=(
            ImageFactObservation(
                name="included items",
                scope="packaging",
                value="1 x Hardwire Kit; 1 x User Manual",
                evidence_text="The package graphic explicitly labels both delivered items.",
            ),
        ),
        notes="Both labels point to objects shown inside the package contents panel.",
    )

    compact = build_compact_evidence(GroundingCatalog([]), [observation])

    assert "visible_text=BOX CONTENTS 1 x HARDWIRE KIT 1 x USER MANUAL" in compact.image_facts
    assert "included items(packaging)=1 x Hardwire Kit; 1 x User Manual" in compact.image_facts
    assert "The package graphic explicitly labels both delivered items" not in compact.image_facts
    assert "Both labels point to objects shown inside the package contents panel" not in compact.image_facts
    assert compact.image_fact_count == 1


def test_indexed_single_slot_without_add_control_stays_single_and_keeps_field_id() -> None:
    raw = _merge_semantic_field(
        "model_number",
        [
            _value_control(
                "model_number",
                "Model Number",
                "Product Description",
            )
        ],
    )
    planned = live_schema_payload([raw])["fields"][0]

    assert raw["multi_value"] is False
    assert planned["multi_value"] is False
    assert field_id(raw) == field_id(planned)


def test_single_slot_with_real_add_control_is_repeatable_and_keeps_field_id() -> None:
    raw = _merge_semantic_field(
        "sales_package",
        [
            _value_control(
                "sales_package",
                "Sales Package",
                "Product Description",
                has_add_value_control=True,
                required=True,
            )
        ],
    )
    planned = live_schema_payload([raw])["fields"][0]

    assert raw["has_add_value_control"] is True
    assert raw["multi_value"] is True
    assert planned["multi_value"] is True
    assert schema_field_signature(raw) == schema_field_signature(planned)
    assert field_id(raw) == field_id(planned)


def test_two_rendered_value_slots_remain_repeatable_without_add_marker() -> None:
    raw = _merge_semantic_field(
        "keywords",
        [
            _value_control("keywords", "Keywords", "Additional Description", index=0),
            _value_control("keywords", "Keywords", "Additional Description", index=1),
        ],
    )
    planned = live_schema_payload([raw])["fields"][0]

    assert raw["multi_value"] is True
    assert planned["multi_value"] is True
    assert schema_field_signature(raw) == schema_field_signature(planned)
    assert field_id(raw) == field_id(planned)


def test_indexed_name_alone_is_not_repeatability_evidence() -> None:
    current = {
        "attribute_key": "processor",
        "label": "Processor",
        "section_heading": "Additional Description",
        "required": False,
        "multi_value": False,
        "controls": [
            {
                "name": "processor_0_value",
                "field_kind": "input",
                "has_add_value_control": False,
            }
        ],
    }
    planned = live_schema_payload([current])["fields"][0]

    assert planned["multi_value"] is False
    assert schema_field_signature(current) == schema_field_signature(planned)
    assert field_id(current) == field_id(planned)
