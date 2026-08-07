from __future__ import annotations

from app.qa_catalog import QuestionCatalog, QuestionRecord
from app.snapshot_evidence import extract_snapshot_evidence
from app.source_snapshot import SourceSnapshot, SnapshotTableRow


def catalog() -> QuestionCatalog:
    return QuestionCatalog(
        source_path="qa.xlsx",
        sheet_name="Sheet1",
        header_row=3,
        questions=[
            QuestionRecord(number="1", question="Image Resolution"),
            QuestionRecord(number="2", question="Screen Size"),
            QuestionRecord(number="3", question="Selling Price"),
        ],
    )


def snapshot() -> SourceSnapshot:
    return SourceSnapshot(
        requested_url="https://supplier.test/item",
        final_url="https://supplier.test/item/123",
        title="L11 Dash Camera",
        captured_at="2026-08-08T00:00:00+00:00",
        table_rows=[
            SnapshotTableRow("Image Resolution", "1920x1080", 1, 1),
            SnapshotTableRow("Screen Size", "3.0 inch", 1, 2),
            SnapshotTableRow("Selling Price", "R999", 1, 3),
            SnapshotTableRow("Unrequested Marketing Name", "Super Cam", 1, 4),
        ],
        json_ld=[
            {
                "@type": "Product",
                "sku": "SKU-L11",
                "model": "L11",
                "brand": {"@type": "Brand", "name": "SHANMING"},
            }
        ],
    )


def test_exact_snapshot_rows_become_traceable_evidence_packet():
    result = extract_snapshot_evidence(snapshot(), catalog())

    assert result.matched_rows == 2
    assert result.ignored_rows == 2
    assert [fact.key for fact in result.packet.facts] == ["Image Resolution", "Screen Size"]
    assert result.packet.facts[0].source_type == "supplier_web"
    assert "#table-1-row-1" in result.packet.facts[0].source_reference
    assert result.packet.identity.model_number == "L11"
    assert result.packet.identity.brand == "SHANMING"
    assert any("business field ignored" in warning for warning in result.warnings)


def test_snapshot_does_not_parse_free_prose_into_facts():
    item = snapshot()
    item.table_rows = []
    item.visible_text = "Image Resolution: 4K. Screen Size: 3.16 inch."

    result = extract_snapshot_evidence(item, catalog())

    assert result.packet.facts == []
