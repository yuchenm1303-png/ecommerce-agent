from __future__ import annotations

from dataclasses import dataclass

from app.evidence_contract import EvidencePacket, ProductIdentity
from app.evidence_extractors import ExtractionRequest, run_extractors
from app.qa_catalog import QuestionCatalog, QuestionRecord


@dataclass
class StubExtractor:
    name: str
    value: str

    def extract(self, request: ExtractionRequest) -> EvidencePacket:
        return EvidencePacket.from_mapping(
            {
                "extractor": self.name,
                "product_identity": {"model_number": "L11"},
                "facts": [
                    {
                        "key": "Screen Size",
                        "value": self.value,
                        "source_type": "supplier_doc",
                        "source_reference": f"{self.name}:spec",
                        "confidence": 0.95,
                        "evidence_text": self.value,
                    }
                ],
            }
        )


def test_composite_keeps_disagreeing_packets_separate():
    catalog = QuestionCatalog(
        source_path="qa.xlsx",
        sheet_name="Sheet1",
        header_row=3,
        questions=[QuestionRecord(number="1", question="Screen Size")],
    )
    request = ExtractionRequest(
        catalog=catalog,
        expected_identity=ProductIdentity(model_number="L11"),
    )

    result = run_extractors(
        request,
        [StubExtractor("supplier", "3.0 inch"), StubExtractor("image", "3.16 inch")],
    )

    assert result.fact_count == 2
    assert result.packets[0].facts[0].value == "3.0 inch"
    assert result.packets[1].facts[0].value == "3.16 inch"
