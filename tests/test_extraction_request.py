from __future__ import annotations

from app.evidence_contract import ProductIdentity
from app.extraction_request import build_extraction_request_payload
from app.qa_catalog import QuestionCatalog, QuestionRecord


def test_extraction_request_preserves_questions_and_locks_business_fields():
    catalog = QuestionCatalog(
        source_path="qa.xlsx",
        sheet_name="Sheet1",
        header_row=3,
        questions=[
            QuestionRecord(number="1", question="Image Resolution", options=("720p", "1080p")),
            QuestionRecord(number="2", question="Selling Price"),
        ],
    )

    payload = build_extraction_request_payload(
        catalog,
        identity=ProductIdentity(model_number="L11", brand="SHANMING"),
        image_paths=("front.jpg", "back.jpg"),
        product_url="https://example.com/item",
    )

    assert payload["product_identity"]["model_number"] == "L11"
    assert payload["questions"][0]["options"] == ["720p", "1080p"]
    assert payload["questions"][0]["business_locked"] is False
    assert payload["questions"][1]["business_locked"] is True
    assert len(payload["sources"]["images"]) == 2
    assert any("Do not guess" in rule for rule in payload["rules"])
