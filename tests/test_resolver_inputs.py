from __future__ import annotations

import pytest

from app.qa_catalog import QuestionCatalog, QuestionRecord
from app.resolver_inputs import (
    ResolutionInputSpec,
    build_resolution_inputs,
    customer_context_for_resolution,
)


def catalog_with_identity() -> QuestionCatalog:
    return QuestionCatalog(
        source_path="qa.xlsx",
        sheet_name="Sheet1",
        header_row=3,
        questions=[
            QuestionRecord(
                number="1",
                question="Model Number",
                answer="L11",
                source_reference="qa.xlsx:row=4",
            ),
            QuestionRecord(
                number="2",
                question="Brand",
                answer="SHANMING",
                source_reference="qa.xlsx:row=5",
            ),
            QuestionRecord(number="3", question="Image Resolution"),
        ],
    )


def test_expected_identity_is_derived_only_from_trusted_explicit_inputs():
    result = build_resolution_inputs(catalog_with_identity(), ResolutionInputSpec())

    assert result.expected_identity.model_number == "L11"
    assert result.expected_identity.brand == "SHANMING"


def test_explicit_identity_conflicting_with_trusted_qa_is_rejected():
    with pytest.raises(ValueError, match="显式 Model Number"):
        build_resolution_inputs(
            catalog_with_identity(),
            ResolutionInputSpec(expected_model="L99"),
        )


def test_explicit_sku_is_preserved_as_seller_controlled_business_data():
    result = build_resolution_inputs(
        catalog_with_identity(),
        ResolutionInputSpec(sku="SKU-1"),
    )

    candidates = result.bundle.candidates(["SKU"])
    business = [item for item in candidates if item.source_type == "business"]
    assert len(business) == 1
    assert business[0].value == "SKU-1"
    assert result.expected_identity.sku == "SKU-1"


def test_customer_context_is_retained_exactly_once_for_ai_grounding_and_rebind():
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
    assert result.bundle.candidates(["Selected Variant"])[0].value == "M8 dual camera + 64GB card"


def test_supplier_and_image_inputs_are_not_locally_interpreted_into_product_facts():
    result = build_resolution_inputs(
        catalog_with_identity(),
        ResolutionInputSpec(
            supplier_snapshots=("supplier.json",),
            image_paths=("product.png",),
        ),
    )

    # The explicit QA identity remains, but supplier/image semantics are owned by
    # the AI source pack and therefore never become local Image Resolution facts.
    assert result.bundle.candidates(["Image Resolution"]) == []
