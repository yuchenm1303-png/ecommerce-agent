from __future__ import annotations

import json

from app.ai_decisions import (
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
from app.product_profile import ProductFact, ProductProfile, ProfileCandidate
from app.providers.dashscope_web_search import WebSearchJSONResult, WebSearchSource
from app.semantic_grounding import GroundedSource, GroundingCatalog, TEXT_KIND
from app.web_enrichment import run_web_enrichment, write_enriched_ai_decision_packet


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


def profile() -> ProductProfile:
    sources = grounding()
    return ProductProfile(
        identity=ProductIdentity(sku="SKU-1", model_number="M8"),
        source_manifest_sha256=source_manifest_digest(sources),
        facts=[
            ProductFact(
                name="selected_variant",
                scope="selected_variant",
                status="supported",
                candidates=(
                    ProfileCandidate(
                        value="M8 WiFi dual camera 64GB",
                        citations=(
                            DecisionCitation(
                                "customer-text:001:text:0001:abc",
                                "Selected variant: M8 WiFi dual camera 64GB",
                            ),
                        ),
                    ),
                ),
            )
        ],
        summary="M8 selected variant",
        extractor="profile-ai",
    )


def packet(fields, decisions):
    sources = grounding()
    return AIDecisionPacket(
        identity=ProductIdentity(sku="SKU-1", model_number="M8"),
        schema_sha256=schema_digest(fields),
        source_manifest_sha256=source_manifest_digest(sources),
        decisions=decisions,
        model_summary="local mapping",
        extractor="field-map",
    )


class FakeWebProvider:
    name = "fake-sourced-web"
    model = "qwen3.7-max"

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
    model = "qwen3.7-max"

    def __init__(self):
        self.calls = 0

    def search_json(self, prompt):
        self.calls += 1
        raise RuntimeError("network unavailable")


class FakeFinalProvider:
    name = "fake-final"

    def __init__(self):
        self.calls = 0
        self.requests = []

    def extract_json(self, request):
        self.calls += 1
        self.requests.append(request)
        web_payload = json.loads(request["grounded_sources"][1]["content"])
        decisions = []
        for target in request["target_fields"]:
            evidence = web_payload[target["field_id"]][0]
            decisions.append(
                {
                    "field_id": target["field_id"],
                    "status": "ready",
                    "values": ["GC2053"],
                    "citations": [
                        {
                            "source_reference": evidence["source_reference"],
                            "evidence_text": evidence["evidence_text"],
                        }
                    ],
                }
            )
        return {"decisions": decisions}


def test_unresolved_fields_are_researched_then_finalized_without_touching_ready(tmp_path):
    colour = field("colour", "Colour")
    sensor = field("image_sensor", "Image Sensor")
    fields = [colour, sensor]
    initial = packet(
        fields,
        [
            FieldDecision(
                field_id=field_id(colour),
                status=READY,
                values=["Black"],
                citations=[
                    DecisionCitation(
                        "customer-text:001:text:0001:abc",
                        "Selected variant: M8 WiFi dual camera 64GB",
                    )
                ],
            ),
            FieldDecision(field_id=field_id(sensor), status=MISSING),
        ],
    )
    url = "https://example.test/m8-manual"
    search = FakeWebProvider(
        {
            "evidence": [
                {
                    "field_id": field_id(sensor),
                    "items": [{"source_url": url, "evidence_text": "Image sensor: GC2053"}],
                }
            ]
        },
        [WebSearchSource(index="1", title="M8 manual", url=url)],
    )
    final = FakeFinalProvider()

    result = run_web_enrichment(
        search,
        final,
        initial,
        fields,
        grounding(),
        profile(),
        cache_dir=tmp_path / "cache",
    )

    assert search.calls == 1
    assert final.calls == 1
    assert result.search_model_calls == 1
    assert result.final_model_calls == 1
    assert result.target_field_count == 1
    assert result.packet.decisions[0].values == ["Black"]
    assert result.packet.decisions[1].status == READY
    assert result.packet.decisions[1].values == ["GC2053"]
    assert len(result.web_sources) == 1
    assert field_id(colour) not in search.prompts[0]


def test_invented_web_url_is_dropped_before_final_resolve():
    sensor = field("image_sensor", "Image Sensor")
    fields = [sensor]
    initial = packet(fields, [FieldDecision(field_id=field_id(sensor), status=MISSING)])
    search = FakeWebProvider(
        {
            "evidence": [
                {
                    "field_id": field_id(sensor),
                    "items": [
                        {
                            "source_url": "https://invented.test/not-returned",
                            "evidence_text": "Image sensor: GC2053",
                        }
                    ],
                }
            ]
        },
        [WebSearchSource(index="1", title="Real", url="https://example.test/real")],
    )
    final = FakeFinalProvider()
    result = run_web_enrichment(search, final, initial, fields, grounding(), profile())
    assert result.evidence == []
    assert result.web_sources == []
    assert final.calls == 0
    assert result.packet.decisions[0].status == MISSING


def test_parallel_web_batch_cache_and_final_cache_replay_with_zero_calls(tmp_path):
    sensor = field("image_sensor", "Image Sensor")
    fields = [sensor]
    initial = packet(fields, [FieldDecision(field_id=field_id(sensor), status=MISSING)])
    url = "https://example.test/m8"
    search = FakeWebProvider(
        {
            "evidence": [
                {
                    "field_id": field_id(sensor),
                    "items": [{"source_url": url, "evidence_text": "Image sensor: GC2053"}],
                }
            ]
        },
        [WebSearchSource(index="1", title="M8", url=url)],
    )
    final = FakeFinalProvider()
    cache = tmp_path / "cache"
    first = run_web_enrichment(
        search,
        final,
        initial,
        fields,
        grounding(),
        profile(),
        cache_dir=cache,
        final_cache_namespace="model",
    )
    second = run_web_enrichment(
        search,
        final,
        initial,
        fields,
        grounding(),
        profile(),
        cache_dir=cache,
        final_cache_namespace="model",
    )
    assert first.search_model_calls == 1
    assert first.final_model_calls == 1
    assert second.search_model_calls == 0
    assert second.final_model_calls == 0
    assert second.cache_hit is True
    assert search.calls == 1
    assert final.calls == 1


def test_search_failure_preserves_local_packet_and_does_not_call_final():
    sensor = field("image_sensor", "Image Sensor")
    fields = [sensor]
    initial = packet(fields, [FieldDecision(field_id=field_id(sensor), status=MISSING)])
    search = FailingWebProvider()
    final = FakeFinalProvider()
    result = run_web_enrichment(search, final, initial, fields, grounding(), profile())
    assert search.calls == 1
    assert final.calls == 0
    assert result.packet.decisions[0].status == MISSING
    assert result.search_failed_batches == 1
    assert result.warnings


def test_embedded_web_sources_reload_through_unified_decision_loader(tmp_path):
    sensor = field("image_sensor", "Image Sensor")
    fields = [sensor]
    initial = packet(fields, [FieldDecision(field_id=field_id(sensor), status=MISSING)])
    url = "https://example.test/m8"
    search = FakeWebProvider(
        {
            "evidence": [
                {
                    "field_id": field_id(sensor),
                    "items": [{"source_url": url, "evidence_text": "Image sensor: GC2053"}],
                }
            ]
        },
        [WebSearchSource(index="1", title="M8", url=url)],
    )
    result = run_web_enrichment(
        search,
        FakeFinalProvider(),
        initial,
        fields,
        grounding(),
        profile(),
    )
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
    assert loaded.decisions[0].citations[0].source_reference.startswith("web-search:")
