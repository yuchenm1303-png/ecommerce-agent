from __future__ import annotations

from app.compact_evidence import build_compact_evidence
from app.image_evidence import ImageFactObservation, ImageObservation
from app.live_schema import live_schema_payload, schema_field_signature
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


def test_indexed_single_slot_is_serialized_as_repeatable() -> None:
    raw = {
        "attribute_key": "sales_package",
        "label": "Sales Package",
        "section_heading": "Product Description",
        "required": True,
        "multi_value": False,
        "controls": [
            {
                "name": "sales_package_0_value",
                "field_kind": "input",
            }
        ],
    }

    field = live_schema_payload([raw])["fields"][0]

    assert field["multi_value"] is True


def test_repeatable_normalization_is_stable_across_schema_drift_gate() -> None:
    current = {
        "attribute_key": "keywords",
        "label": "Keywords",
        "section_heading": "Additional Description",
        "required": False,
        "multi_value": False,
        "controls": [{"name": "keywords_0_value", "field_kind": "input"}],
    }
    planned = {
        "attribute_key": "keywords",
        "label": "Keywords",
        "section_heading": "Additional Description",
        "required": False,
        "multi_value": True,
        "options": [],
        "qualifier_options": [],
    }

    assert schema_field_signature(current) == schema_field_signature(planned)


def test_plain_single_input_remains_single_value() -> None:
    raw = {
        "attribute_key": "model_number",
        "label": "Model Number",
        "section_heading": "Product Description",
        "required": False,
        "multi_value": False,
        "controls": [{"name": "model_number", "field_kind": "input"}],
    }

    field = live_schema_payload([raw])["fields"][0]

    assert field["multi_value"] is False
