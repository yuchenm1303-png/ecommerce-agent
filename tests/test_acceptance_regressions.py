from __future__ import annotations

import json

from app.ai_decisions import MISSING, READY, AIDecisionPacket, FieldDecision, field_id, schema_digest, source_manifest_digest
from app.evidence_contract import ProductIdentity
from app.providers.dashscope_web_search import WebSearchJSONResult, WebSearchSource
from app.semantic_grounding import GroundedSource, GroundingCatalog, TEXT_KIND, build_grounding_catalog
from app.web_enrichment import run_web_enrichment


PRODUCT_URL = "https://detail.1688.com/offer/850845635717.html"


def _field(key: str, label: str):
    return {
        "attribute_key": key,
        "label": label,
        "section_heading": "Additional Description",
        "required": False,
        "multi_value": False,
        "options": [],
        "qualifier_options": [],
        "controls": [],
        "help_text": "",
        "context_text": "",
    }


def _grounding() -> GroundingCatalog:
    return GroundingCatalog(
        sources=[
            GroundedSource(
                source_id="supplier:001:text:0001:abc",
                source_type="supplier_web",
                kind=TEXT_KIND,
                origin=PRODUCT_URL,
                content="Colour: Black",
                sha256="a" * 64,
            )
        ]
    )


def test_structured_dimension_rows_are_atomic_evidence_units(tmp_path):
    snapshot = tmp_path / "supplier.json"
    snapshot.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "requested_url": PRODUCT_URL,
                "final_url": PRODUCT_URL,
                "title": "M8",
                "captured_at": "2026-08-09T00:00:00+00:00",
                "visible_text": "M8 camera",
                "table_rows": [
                    {"key": "Length", "value": "16 cm", "table_index": 1, "row_index": 1},
                    {"key": "Width", "value": "11 cm", "table_index": 1, "row_index": 2},
                    {"key": "Height", "value": "7 cm", "table_index": 1, "row_index": 3},
                ],
                "json_ld": [],
                "embedded_data": [],
                "image_urls": [],
                "meta": {},
            }
        ),
        encoding="utf-8",
    )
    catalog = build_grounding_catalog(supplier_snapshots=[str(snapshot)])
    rows = [source for source in catalog.sources if ":text:row-" in source.source_id]

    assert len(rows) == 3
    assert '"key":"Length","value":"16 cm"' in rows[0].content
    assert '"key":"Width","value":"11 cm"' in rows[1].content
    assert '"key":"Height","value":"7 cm"' in rows[2].content
    assert all(row.logical_source_id == "supplier:001" for row in rows)


class SimilarProductWebProvider:
    name = "unsafe-inference-web"
    model = "qwen3.7-max"

    def __init__(self):
        self.calls = 0
        self.prompt = ""

    def search_json(self, prompt):
        self.calls += 1
        self.prompt = prompt
        payload = json.loads(prompt.split("\n\n", 1)[1])
        target = payload["target_fields"][0]["field_id"]
        url = "https://example.test/exact-mirror"
        return WebSearchJSONResult(
            payload={
                "source_matches": [
                    {
                        "source_url": url,
                        "match": "similar_product",
                        "identity_basis": "generic_model_or_similarity",
                        "reason": "same product type and compatible design",
                        "identity_evidence": ["same dash-camera form factor and vehicle compatibility"],
                    }
                ],
                "decisions": [
                    {
                        "field_id": target,
                        "status": "ready",
                        "values": ["Universal"],
                        "citations": [
                            {
                                "source_url": url,
                                "evidence_text": "Compatible vehicle model: Universal",
                            }
                        ],
                    }
                ],
            },
            sources=[WebSearchSource(index="1", title="Mirror", url=url)],
            request_id="req-1",
        )


def test_similar_product_can_supply_direct_field_value_without_exact_identity():
    target = _field("vehicle_model", "Vehicle Model")
    grounding = _grounding()
    initial = AIDecisionPacket(
        identity=ProductIdentity(),
        schema_sha256=schema_digest([target]),
        source_manifest_sha256=source_manifest_digest(grounding),
        decisions=[FieldDecision(field_id=field_id(target), status=MISSING)],
        model_summary="local",
        extractor="local",
    )
    provider = SimilarProductWebProvider()
    result = run_web_enrichment(
        provider,
        initial,
        [target],
        grounding,
        product_url=PRODUCT_URL,
    )

    assert result.packet.decisions[0].status == READY
    assert result.packet.decisions[0].values == ["Universal"]
    assert len(result.web_sources) == 1
    assert "real returned pages" in provider.prompt
