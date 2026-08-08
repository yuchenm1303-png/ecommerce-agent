from __future__ import annotations

import json

from app.qa_catalog import QuestionCatalog, QuestionRecord
from app.resolver_inputs import ResolutionInputSpec, build_resolution_inputs


def catalog() -> QuestionCatalog:
    return QuestionCatalog(
        source_path="qa.xlsx",
        sheet_name="Sheet1",
        header_row=3,
        questions=[
            QuestionRecord(number="1", question="Image Resolution"),
            QuestionRecord(number="2", question="Selling Price"),
        ],
    )


def test_supplier_snapshot_is_captured_as_source_without_business_leak(tmp_path):
    path = tmp_path / "snapshot.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "requested_url": "https://supplier.test/item",
                "final_url": "https://supplier.test/item/123",
                "title": "L11",
                "captured_at": "2026-08-08T00:00:00+00:00",
                "visible_text": "",
                "table_rows": [
                    {
                        "key": "Image Resolution",
                        "value": "1920x1080",
                        "table_index": 1,
                        "row_index": 1,
                    },
                    {
                        "key": "Selling Price",
                        "value": "999",
                        "table_index": 1,
                        "row_index": 2,
                    },
                ],
                "json_ld": [],
                "meta": {},
                "warnings": [],
            }
        ),
        encoding="utf-8",
    )

    inputs = build_resolution_inputs(
        catalog(),
        ResolutionInputSpec(supplier_snapshots=(str(path),)),
    )

    image_resolution = [
        item for item in inputs.bundle.evidence if item.key == "Image Resolution"
    ]
    selling_price = [
        item for item in inputs.bundle.evidence if item.key == "Selling Price"
    ]
    assert len(image_resolution) == 1
    assert image_resolution[0].value == "1920x1080"
    assert image_resolution[0].source_type == "supplier_web"
    assert image_resolution[0].evidence_text == "Image Resolution: 1920x1080"
    assert selling_price == []
    assert inputs.source_snapshot_files == [str(path.resolve())]
    assert any("business field ignored" in warning for warning in inputs.warnings)
