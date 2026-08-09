from __future__ import annotations

import json

from app.ai_decisions import (
    CONFLICT,
    MISSING,
    READY,
    AIDecisionPacket,
    DecisionAlternative,
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
from app.web_enrichment import run_web_enrichment, write_enriched_ai_decision_packet


PRODUCT_URL = "https://detail.1688.com/offer/850845635717.html"


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
                source_id="supplier:001:text:0001:abc",
                source_type="supplier_web",
                kind=TEXT_KIND,
                origin=PRODUCT_URL,
                content="Selected variant: M8 WiFi dual camera front+cabin",
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
        model_summary="local fill",
        extractor="local-fill",
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
        return WebSearchJSONResult(payload=self.payload, sources=self.sources, request_id="req-web-1")


class DynamicWebProvider:
    name = "dynamic-sourced-web"
    model = "qwen3.7-max"

    def __init__(self):
        self.calls = 0

    def search_json(self, prompt):
        self.calls += 1
        payload = json.loads(prompt.split("\n\n", 1)[1])
        url = "https://example.test/exact-m8"
        decisions = [
            {
                "field_id": target["field_id"],
                "status": "ready",
                "values": ["GC2053"],
                "citations": [
                    {
                        "source_url": url,
                        "evidence_text": f"Image sensor: GC2053 for {target['field_id']}",
                    }
                ],
            }
            for target in payload["target_fields"]
        ]
        return WebSearchJSONResult(
            payload={
                "source_matches": [
                    {
                        "source_url": url,
                        "match": "same_product",
                        "identity_basis": "explicit_cross_reference",
                        "reason": "same exact product",
                        "identity_evidence": ["candidate explicitly cross-references the exact supplier item"],
                    }
                ],
                "decisions": decisions,
            },
            sources=[WebSearchSource(index="1", title="Exact M8", url=url)],
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


def same_product_payload(url: str, decisions):
    return {
        "source_matches": [
            {
                "source_url": url,
                "match": "same_product",
                "identity_basis": "explicit_cross_reference",
                "reason": "same exact product",
                "identity_evidence": ["explicit exact-product cross-reference"],
            }
        ],
        "decisions": decisions,
    }


def test_missing_field_is_searched_and_ready_field_is_frozen(tmp_path):
    colour = field("colour", "Colour")
    sensor = field("image_sensor", "Image Sensor")
    fields = [colour, sensor]
    citation = DecisionCitation("supplier:001:text:0001:abc", "Selected variant: M8 WiFi dual camera front+cabin")
    initial = packet(
        fields,
        [
            FieldDecision(field_id=field_id(colour), status=READY, values=["Black"], citations=[citation]),
            FieldDecision(field_id=field_id(sensor), status=MISSING),
        ],
    )
    url = "https://example.test/m8-manual"
    search = FakeWebProvider(
        same_product_payload(
            url,
            [
                {
                    "field_id": field_id(sensor),
                    "status": "ready",
                    "values": ["GC2053"],
                    "citations": [{"source_url": url, "evidence_text": "Image sensor: GC2053"}],
                }
            ],
        ),
        [WebSearchSource(index="1", title="M8 manual", url=url)],
    )

    result = run_web_enrichment(
        search,
        initial,
        fields,
        grounding(),
        product_url=PRODUCT_URL,
        cache_dir=tmp_path / "cache",
    )

    assert search.calls == 1
    assert result.target_field_count == 1
    assert result.packet.decisions[0].values == ["Black"]
    assert result.packet.decisions[1].status == READY
    assert result.packet.decisions[1].values == ["GC2053"]
    payload = json.loads(search.prompts[0].split("\n\n", 1)[1])
    assert [item["field_id"] for item in payload["target_fields"]] == [field_id(sensor)]
    assert PRODUCT_URL in search.prompts[0]
    assert '"known_local_fields"' in search.prompts[0]
    assert result.source_matches[0].match == "same_product"
    assert result.source_matches[0].identity_basis == "explicit_cross_reference"


def test_local_conflict_is_frozen_and_never_sent_as_target():
    resolution = field("recording_resolution", "Recording Resolution")
    sensor = field("image_sensor", "Image Sensor")
    fields = [resolution, sensor]
    citation = DecisionCitation("supplier:001:text:0001:abc", "Selected variant: M8 WiFi dual camera front+cabin")
    initial = packet(
        fields,
        [
            FieldDecision(
                field_id=field_id(resolution),
                status=CONFLICT,
                alternatives=[
                    DecisionAlternative(values=("720p",), citations=(citation,)),
                    DecisionAlternative(values=("1080p",), citations=(citation,)),
                ],
            ),
            FieldDecision(field_id=field_id(sensor), status=MISSING),
        ],
    )
    url = "https://example.test/m8"
    search = FakeWebProvider(
        same_product_payload(
            url,
            [
                {
                    "field_id": field_id(sensor),
                    "status": "ready",
                    "values": ["GC2053"],
                    "citations": [{"source_url": url, "evidence_text": "Image sensor: GC2053"}],
                }
            ],
        ),
        [WebSearchSource(index="1", title="M8", url=url)],
    )
    result = run_web_enrichment(search, initial, fields, grounding(), product_url=PRODUCT_URL)
    assert result.packet.decisions[0].status == CONFLICT
    prompt_payload = json.loads(search.prompts[0].split("\n\n", 1)[1])
    assert [item["field_id"] for item in prompt_payload["target_fields"]] == [field_id(sensor)]


def test_web_fill_is_one_product_research_session_and_hot_cached(tmp_path):
    fields = [field(f"field_{index}", f"Field {index}") for index in range(5)]
    initial = packet(fields, [FieldDecision(field_id=field_id(item), status=MISSING) for item in fields])
    search = DynamicWebProvider()
    cache = tmp_path / "cache"

    first = run_web_enrichment(
        search,
        initial,
        fields,
        grounding(),
        product_url=PRODUCT_URL,
        batch_size=2,
        concurrency=3,
        cache_dir=cache,
    )
    second = run_web_enrichment(
        search,
        initial,
        fields,
        grounding(),
        product_url=PRODUCT_URL,
        batch_size=2,
        concurrency=3,
        cache_dir=cache,
    )

    assert first.search_batch_count == 1
    assert first.search_model_calls == 1
    assert second.search_model_calls == 0
    assert second.search_cache_hits == 1
    assert second.cache_hit is True
    assert search.calls == 1
    assert all(item.status == READY for item in second.packet.decisions)


def test_same_named_candidate_marked_uncertain_cannot_replace_local_missing():
    sensor = field("image_sensor", "Image Sensor")
    fields = [sensor]
    initial = packet(fields, [FieldDecision(field_id=field_id(sensor), status=MISSING)])
    url = "https://example.test/another-m8"
    search = FakeWebProvider(
        {
            "source_matches": [
                {
                    "source_url": url,
                    "match": "uncertain",
                    "identity_basis": "generic_model_or_similarity",
                    "reason": "only generic M8 name matches",
                    "identity_evidence": ["model token M8"],
                }
            ],
            "decisions": [
                {
                    "field_id": field_id(sensor),
                    "status": "ready",
                    "values": ["GC2053"],
                    "citations": [{"source_url": url, "evidence_text": "Image sensor: GC2053"}],
                }
            ],
        },
        [WebSearchSource(index="1", title="Another M8", url=url)],
    )
    result = run_web_enrichment(search, initial, fields, grounding(), product_url=PRODUCT_URL)
    assert result.source_matches[0].match == "uncertain"
    assert result.web_sources == []
    assert result.packet.decisions[0].status == MISSING


def test_ai_same_product_with_only_model_and_similar_dimensions_is_downgraded():
    sensor = field("image_sensor", "Image Sensor")
    fields = [sensor]
    initial = packet(fields, [FieldDecision(field_id=field_id(sensor), status=MISSING)])
    url = "https://example.test/lookalike-m8"
    search = FakeWebProvider(
        {
            "source_matches": [
                {
                    "source_url": url,
                    "match": "same_product",
                    "identity_basis": "generic_model_or_similarity",
                    "reason": "M8 name and similar dimensions",
                    "identity_evidence": ["M8; dimensions close to supplier page"],
                }
            ],
            "decisions": [
                {
                    "field_id": field_id(sensor),
                    "status": "ready",
                    "values": ["GC1054"],
                    "citations": [{"source_url": url, "evidence_text": "Image sensor GC1054"}],
                }
            ],
        },
        [WebSearchSource(index="1", title="Lookalike M8", url=url)],
    )
    result = run_web_enrichment(search, initial, fields, grounding(), product_url=PRODUCT_URL)
    assert result.source_matches[0].match == "uncertain"
    assert result.source_matches[0].identity_basis == "generic_model_or_similarity"
    assert result.web_sources == []
    assert result.packet.decisions[0].status == MISSING
    assert any("strong identity anchor" in warning for warning in result.warnings)


def test_invented_web_url_cannot_replace_local_missing():
    sensor = field("image_sensor", "Image Sensor")
    fields = [sensor]
    initial = packet(fields, [FieldDecision(field_id=field_id(sensor), status=MISSING)])
    real_url = "https://example.test/real"
    search = FakeWebProvider(
        same_product_payload(
            real_url,
            [
                {
                    "field_id": field_id(sensor),
                    "status": "ready",
                    "values": ["GC2053"],
                    "citations": [
                        {
                            "source_url": "https://invented.test/not-returned",
                            "evidence_text": "Image sensor: GC2053",
                        }
                    ],
                }
            ],
        ),
        [WebSearchSource(index="1", title="Real", url=real_url)],
    )
    result = run_web_enrichment(search, initial, fields, grounding(), product_url=PRODUCT_URL)
    assert result.web_sources == []
    assert result.packet.decisions[0].status == MISSING


def test_search_failure_preserves_local_packet():
    sensor = field("image_sensor", "Image Sensor")
    fields = [sensor]
    initial = packet(fields, [FieldDecision(field_id=field_id(sensor), status=MISSING)])
    search = FailingWebProvider()
    result = run_web_enrichment(search, initial, fields, grounding(), product_url=PRODUCT_URL)
    assert search.calls == 1
    assert result.packet.decisions[0].status == MISSING
    assert result.search_failed_batches == 1
    assert result.warnings


def test_embedded_web_sources_reload_through_unified_decision_loader(tmp_path):
    sensor = field("image_sensor", "Image Sensor")
    fields = [sensor]
    initial = packet(fields, [FieldDecision(field_id=field_id(sensor), status=MISSING)])
    url = "https://example.test/m8"
    search = FakeWebProvider(
        same_product_payload(
            url,
            [
                {
                    "field_id": field_id(sensor),
                    "status": "ready",
                    "values": ["GC2053"],
                    "citations": [{"source_url": url, "evidence_text": "Image sensor: GC2053"}],
                }
            ],
        ),
        [WebSearchSource(index="1", title="M8", url=url)],
    )
    result = run_web_enrichment(search, initial, fields, grounding(), product_url=PRODUCT_URL)
    path = write_enriched_ai_decision_packet(result.packet, result.web_sources, tmp_path / "ai-decisions.json")
    loaded = load_ai_decision_packet(
        path,
        fields,
        grounding(),
        expected_identity=ProductIdentity(sku="SKU-1", model_number="M8"),
    )
    assert loaded.decisions[0].status == READY
    assert loaded.decisions[0].citations[0].source_reference.startswith("web-search:")
