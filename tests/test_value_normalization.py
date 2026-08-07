from __future__ import annotations

from app.answer_resolver import CONFLICT, RESOLVED, resolve_field
from app.source_bundle import ProductSourceBundle
from app.value_normalization import canonical_scalar_for_field


def field(label: str):
    return {"attribute_key": label.casefold().replace(" ", "_"), "label": label, "controls": []}


def add(bundle, key, value, source, priority):
    bundle.add_evidence(
        key=key,
        value=value,
        source_type=source,
        source_reference=f"{source}:spec",
        priority=priority,
        confidence=0.95,
    )


def test_numeric_formatting_does_not_create_false_conflict():
    bundle = ProductSourceBundle()
    add(bundle, "Screen Size", "3 inch", "supplier_doc", 25)
    add(bundle, "Screen Size", "3.0 inches", "product_image", 30)

    answer = resolve_field(field("Screen Size"), bundle)

    assert answer.status == RESOLVED
    assert answer.answer == "3 inch"


def test_resolution_multiplication_glyph_does_not_create_false_conflict():
    bundle = ProductSourceBundle()
    add(bundle, "Image Resolution", "1920 x 1080", "supplier_doc", 25)
    add(bundle, "Image Resolution", "1920×1080", "product_image", 30)

    answer = resolve_field(field("Image Resolution"), bundle)

    assert answer.status == RESOLVED
    assert answer.answer == "1920 x 1080"


def test_1080p_is_not_silently_declared_equal_to_1920x1080():
    bundle = ProductSourceBundle()
    add(bundle, "Image Resolution", "1080P", "supplier_doc", 25)
    add(bundle, "Image Resolution", "1920x1080", "product_image", 30)

    answer = resolve_field(field("Image Resolution"), bundle)

    assert answer.status == CONFLICT


def test_real_numeric_difference_remains_conflict():
    bundle = ProductSourceBundle()
    add(bundle, "Screen Size", "3.0 inch", "supplier_doc", 25)
    add(bundle, "Screen Size", "3.16 inch", "product_image", 30)

    answer = resolve_field(field("Screen Size"), bundle)

    assert answer.status == CONFLICT


def test_canonicalizer_does_not_expand_marketing_resolution_names():
    semantic = field("Image Resolution")
    assert canonical_scalar_for_field(semantic, "1080P") != canonical_scalar_for_field(
        semantic, "1920x1080"
    )
