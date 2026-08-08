from __future__ import annotations

from pathlib import Path

from app.ai_decisions import (
    BUSINESS_LOCKED,
    MISSING,
    READY,
    REVIEW,
    AIDecisionPacket,
    DecisionCitation,
    FieldDecision,
    field_id,
    load_ai_decision_packet,
    schema_digest,
    source_manifest_digest,
)
from app.evidence_contract import ProductIdentity
from app.providers.dashscope_web_search import WebSearchJSONResult, WebSearchSource
from app.semantic_grounding import GroundedSource, GroundingCatalog, TEXT_KIND
from app.web_enrichment import (
    run_web_enrichment,
    write_enriched_ai_decision_packet,
)


def field(key: str, label: str, *, section="Product Description"):
    return {
        "attribute_key": key,
        "label": label,
        "section_heading": section,
        "required": True,
        "multi_value": False,
        "options": [],
        "controls": [],
        "help_text": "",
    }


def grounding() -> GroundingCatalog:
    return GroundingCatalog(
        sources=[
            GroundedSource(
                source_id="customer-text:001:text:0001:abc",
                source_type="customer_file",
                kind=TEXT_KIND,
                origin="supplemental_text",
                content="Selected variant: M8 WiFi dual camera 64GB",
                sha256="a" * 64,
            )
        ]
    )


def packet(fields, decisions):
    sources = grounding()
    return AIDecisionPacket(
        identity=ProductIdentity(sku="SKU-1", model_number="M8"),
        schema_sha256=schema_digest(fields),
        source_manifest_sha256=source_manifest_digest(sources),
        decisions=decisions,
        model_summary="local pass",
        extractor="local-ai",
    )


class FakeWebProvider:
    name = "fake-sourced-web"
    model = "qwen3.5-omni-plus"

    def __init__(self, payload, sources):
        self.payload = payload
        self.sources = sources
        self.calls = 0
        self.prompts = []

    def search_json(self, prompt):
        self.calls += 1
        self.prompts.append(prompt)
        return WebSearchJSONResult(
            payload=self.payload,
            sources=self.sources,
            request_id="req-web-1",
        )


class FailingWebProvider:
    name = "failing-web"
    model = "qwen3.5-omni-plus"

    def __init__(self):
        self.calls = 0

    def search_json(self, prompt):
        self.calls += 1
        raise RuntimeError("network unavailable")


def test_multiple_unresolved_fields_use_one_web_call_and_freeze_ready_fields(tmp_path):
    colour = field("colour", "Colour")
    sensor = field("image_sensor", "Image Sensor")
    temperature = field("operating_temperature", "Operating Temperature")
    fields = [colour, sensor, temperature]
    initial = packet(
        fields,
        [
            FieldDecision(
                field_id=field_id(colour),
                status=READY,
                values=["Black"],
                confidence=0.96,
                citations=[
                    DecisionCitation(
                        source_reference="customer-text:001:text:0001:abc",
                        evidence_text="M8 WiFi dual camera 64GB",
                    )
                ],
            ),
            FieldDecision(
                field_id=field_id(sensor),
                status=MISSING,
                reason="not in local sources",
                search_queries=["M8 dash cam image sensor"],
            ),
            FieldDecision(
                field_id=field_id(temperature),
                status=REVIEW,
                reason="needs manufacturer specification",
                search_queries=["M8 dash cam operating temperature manual"],
            ),
        ],
    )
    sources = [
        WebSearchSource(
            index="1",
            title="M8 camera manual",
            url="https://example.test/m8-manual",
            site_name="Example",
        )
    ]
    provider = FakeWebProvider(
        {
            "decisions": [
                {
                    "field_id": field_id(sensor),
                    "status": "ready",
                    "values": ["GC2053"],
                    "qualifier": "",
                    "confidence": 0.93,
                    "citations": [
                        {
                            "source_url": "https://example.test/m8-manual",
                            "evidence_text": "Image sensor: GC2053",
                        }
                    ],
                    "alternatives": [],
                    "reason": "manual specification",
                },
                {
                    "field_id": field_id(temperature),
                    "status": "missing",
                    "values": [],
                    "qualifier": "",
                    "confidence": 0.0,
                    "citations": [],
                    "alternatives": [],
                    "reason": "not found",
                },
            ],
            "summary": "searched both gaps",
        },
        sources,
    )

    result = run_web_enrichment(
        provider,
        initial,
        fields,
        grounding(),
        cache_dir=tmp_path / "cache",
    )

    assert provider.calls == 1
    assert result.model_calls == 1
    assert result.target_field_count == 2
    assert [item.status for item in result.packet.decisions] == [READY, READY, MISSING]
    assert result.packet.decisions[0].values == ["Black"]
    assert result.packet.decisions[1].values == ["GC2053"]
    assert len(result.web_sources) == 1
    assert field_id(colour) not in provider.prompts[0]


def test_invented_web_url_cannot_authorize_ready():
    sensor = field("image_sensor", "Image Sensor")
    fields = [sensor]
    initial = packet(
        fields,
        [
            FieldDecision(
                field_id=field_id(sensor),
                status=MISSING,
                search_queries=["M8 dash cam image sensor"],
            )
        ],
    )
    provider = FakeWebProvider(
        {
            "decisions": [
                {
                    "field_id": field_id(sensor),
                    "status": "ready",
                    "values": ["GC2053"],
                    "qualifier": "",
                    "confidence": 0.99,
                    "citations": [
                        {
                            "source_url": "https://invented.test/not-returned",
                            "evidence_text": "Image sensor: GC2053",
                        }
                    ],
                    "alternatives": [],
                    "reason": "invented source",
                }
            ],
            "summary": "",
        },
        [
            WebSearchSource(
                index="1",
                title="Real result",
                url="https://example.test/real",
            )
        ],
    )

    result = run_web_enrichment(provider, initial, fields, grounding())

    assert result.packet.decisions[0].status == REVIEW
    assert result.packet.decisions[0].citations == []
    assert result.web_sources == []


def test_web_enrichment_cache_replays_with_zero_search_calls(tmp_path):
    sensor = field("image_sensor", "Image Sensor")
    fields = [sensor]
    initial = packet(
        fields,
        [
            FieldDecision(
                field_id=field_id(sensor),
                status=MISSING,
                search_queries=["M8 dash cam image sensor"],
            )
        ],
    )
    provider = FakeWebProvider(
        {
            "decisions": [
                {
                    "field_id": field_id(sensor),
                    "status": "ready",
                    "values": ["GC2053"],
                    "qualifier": "",
                    "confidence": 0.95,
                    "citations": [
                        {
                            "source_url": "https://example.test/m8",
                            "evidence_text": "Image sensor: GC2053",
                        }
                    ],
                    "alternatives": [],
                    "reason": "supported",
                }
            ],
            "summary": "ok",
        },
        [WebSearchSource(index="1", title="M8", url="https://example.test/m8")],
    )
    cache = tmp_path / "cache"

    first = run_web_enrichment(provider, initial, fields, grounding(), cache_dir=cache)
    second = run_web_enrichment(provider, initial, fields, grounding(), cache_dir=cache)

    assert first.model_calls == 1
    assert second.model_calls == 0
    assert second.cache_hit is True
    assert provider.calls == 1


def test_optional_web_failure_preserves_valid_local_packet():
    sensor = field("image_sensor", "Image Sensor")
    fields = [sensor]
    initial = packet(
        fields,
        [
            FieldDecision(
                field_id=field_id(sensor),
                status=MISSING,
                search_queries=["M8 dash cam image sensor"],
            )
        ],
    )
    provider = FailingWebProvider()

    result = run_web_enrichment(provider, initial, fields, grounding())

    assert provider.calls == 1
    assert result.packet.decisions[0].status == MISSING
    assert result.warning.startswith("web enrichment failed")


def test_embedded_web_sources_reload_through_unified_decision_loader(tmp_path):
    sensor = field("image_sensor", "Image Sensor")
    fields = [sensor]
    initial = packet(
        fields,
        [
            FieldDecision(
                field_id=field_id(sensor),
                status=MISSING,
                search_queries=["M8 dash cam image sensor"],
            )
        ],
    )
    provider = FakeWebProvider(
        {
            "decisions": [
                {
                    "field_id": field_id(sensor),
                    "status": "ready",
                    "values": ["GC2053"],
                    "qualifier": "",
                    "confidence": 0.95,
                    "citations": [
                        {
                            "source_url": "https://example.test/m8",
                            "evidence_text": "Image sensor: GC2053",
                        }
                    ],
                    "alternatives": [],
                    "reason": "supported",
                }
            ],
            "summary": "ok",
        },
        [WebSearchSource(index="1", title="M8", url="https://example.test/m8")],
    )
    result = run_web_enrichment(provider, initial, fields, grounding())
    path = write_enriched_ai_decision_packet(
        result.packet,
        result.web_sources,
        tmp_path / "ai-decisions.json",
    )

    loaded = load_ai_decision_packet(
        path,
        fields,
        grounding(),
        expected_identity=ProductIdentity(sku="SKU-1", model_number="M8"),
    )

    assert loaded.decisions[0].status == READY
    assert loaded.decisions[0].values == ["GC2053"]
    assert loaded.decisions[0].citations[0].source_reference.startswith("web-search:")
