from __future__ import annotations

import json

from app.ai_decisions import MISSING, READY, AIDecisionPacket, FieldDecision, field_id, schema_digest, source_manifest_digest
from app.evidence_contract import ProductIdentity
from app.field_mapping import run_field_mapping
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


class RepairingProvider:
    name = "repairing-provider"

    def __init__(self):
        self.calls = 0
        self.requests = []

    def extract_json(self, request):
        self.calls += 1
        self.requests.append(request)
        first = request["target_fields"][0]["field_id"]
        second = request["target_fields"][1]["field_id"]
        if self.calls == 1:
            return {
                "ready": [
                    {
                        "field_id": first,
                        "values": ["Black"],
                        "qualifier": "",
                        "confidence": 1.0,
                        "citations": [
                            {
                                "source_reference": "supplier:001:text:0001:abc",
                                "evidence_text": "Colour: Black",
                            }
                        ],
                    }
                ],
                "conflicts": [],
                "missing": [
                    {"field_id": first, "search_queries": []},
                    {"field_id": second, "search_queries": []},
                ],
                "model_summary": "duplicate first response",
            }
        return {
            "ready": [
                {
                    "field_id": first,
                    "values": ["Black"],
                    "qualifier": "",
                    "confidence": 1.0,
                    "citations": [
                        {
                            "source_reference": "supplier:001:text:0001:abc",
                            "evidence_text": "Colour: Black",
                        }
                    ],
                }
            ],
            "conflicts": [],
            "missing": [{"field_id": second, "search_queries": []}],
            "model_summary": "repaired response",
        }


def test_duplicate_field_id_gets_one_structural_repair_and_then_caches(tmp_path):
    fields = [_field("colour", "Colour"), _field("sensor", "Image Sensor")]
    provider = RepairingProvider()
    cache = tmp_path / "cache"

    first = run_field_mapping(
        provider,
        fields,
        _grounding(),
        product_url=PRODUCT_URL,
        batch_size=2,
        concurrency=1,
        cache_dir=cache,
        cache_namespace="test",
    )
    second = run_field_mapping(
        provider,
        fields,
        _grounding(),
        product_url=PRODUCT_URL,
        batch_size=2,
        concurrency=1,
        cache_dir=cache,
        cache_namespace="test",
    )

    assert first.failed_batches == 0
    assert first.model_calls == 2
    assert first.packet.decisions[0].status == READY
    assert first.packet.decisions[1].status == MISSING
    assert "validation_error" in provider.requests[1]
    assert second.model_calls == 0
    assert second.cache_hits == 1
    assert provider.calls == 2


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


class UnsafeInferenceWebProvider:
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
                        "match": "same_product",
                        "reason": "same product",
                        "identity_evidence": [],
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
                                "evidence_text": "No vehicle restriction was listed",
                            }
                        ],
                    }
                ],
            },
            sources=[WebSearchSource(index="1", title="Mirror", url=url)],
            request_id="req-1",
        )


def test_same_product_without_identity_evidence_cannot_supply_field_value():
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
    provider = UnsafeInferenceWebProvider()
    result = run_web_enrichment(
        provider,
        initial,
        [target],
        grounding,
        product_url=PRODUCT_URL,
    )

    assert result.packet.decisions[0].status == MISSING
    assert result.web_sources == []
    assert "direct target-specific evidence" in provider.prompt
