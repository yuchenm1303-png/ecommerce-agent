from __future__ import annotations

from app.compact_evidence import build_compact_evidence
from app.image_evidence import ImageFactObservation, ImageObservation
from app.semantic_grounding import GroundedSource, GroundingCatalog, IMAGE_KIND, TEXT_KIND


def test_compact_evidence_keeps_values_and_source_ids_without_transport_metadata():
    grounding = GroundingCatalog(
        [
            GroundedSource(
                source_id="supplier:001:text:row-0001:abc",
                source_type="supplier_web",
                kind=TEXT_KIND,
                origin="https://supplier.test/item#table=1&row=1",
                content=(
                    'Structured page row; preserve key/value meaning exactly: '
                    '{"key":"包装尺寸","value":"16×11×7 cm","table_index":1,"row_index":1}'
                ),
                sha256="a" * 64,
            ),
            GroundedSource(
                source_id="image:001:def",
                source_type="product_image",
                kind=IMAGE_KIND,
                origin="C:/very/long/private/path/image.jpg",
                image_path="C:/very/long/private/path/image.jpg",
                sha256="b" * 64,
            ),
        ]
    )
    observations = [
        ImageObservation(
            image_id="image:001:def",
            origin="C:/very/long/private/path/image.jpg",
            sha256="b" * 64,
            visible_text="PACKAGE SIZE 16×11×7 CM",
            facts=(
                ImageFactObservation(
                    name="package dimensions",
                    scope="packaging",
                    value="16×11×7",
                    qualifier="cm",
                    evidence_text="a long explanatory sentence that is retained locally only",
                ),
            ),
        )
    ]

    compact = build_compact_evidence(grounding, observations)

    assert "[s1] 包装尺寸=16×11×7 cm" in compact.web_text
    assert "[i1] package dimensions(packaging)=16×11×7 cm" in compact.image_facts
    assert compact.citation_aliases == {
        "s1": "supplier:001:text:row-0001:abc",
        "i1": "image:001:def",
    }
    assert "C:/very/long" not in compact.image_facts
    assert "b" * 64 not in compact.image_facts
    assert "long explanatory sentence" not in compact.image_facts
    assert compact.image_fact_count == 1
    assert len(compact.request_sources()) == 2


def test_compact_evidence_rejoins_dimension_header_and_values_with_packaging_scope():
    sources = []
    for source_id, key, value, row_index in (
        ("supplier:header", "Colour", "Variant | Length(cm) | Width(cm) | Height(cm) | Weight(g)", 1),
        ("supplier:data", "Black", "M8 | 16 | 11 | 7 | 285", 2),
    ):
        sources.append(
            GroundedSource(
                source_id=source_id,
                source_type="supplier_web",
                kind=TEXT_KIND,
                origin=f"https://supplier.test#row={row_index}",
                content=(
                    "Structured page row; preserve key/value meaning exactly: "
                    f'{{"key":"{key}","value":"{value}","table_index":5,"row_index":{row_index}}}'
                ),
                sha256=str(row_index) * 64,
            )
        )

    compact = build_compact_evidence(GroundingCatalog(sources), [])

    assert "scope=packaging" in compact.web_text
    assert "length=16 cm" in compact.web_text
    assert "breadth=11 cm" in compact.web_text
    assert "height=7 cm" in compact.web_text
    assert "weight=285 g" in compact.web_text
