from __future__ import annotations

from pathlib import Path

from app.ai_decisions import (
    BUSINESS_LOCKED,
    CONFLICT,
    MISSING,
    READY,
    REVIEW,
    AIDecisionPacket,
    DecisionAlternative,
    DecisionCitation,
    FieldDecision,
    build_ai_resolution_request,
    field_id,
    run_ai_resolution,
    schema_digest,
    source_manifest_digest,
    validate_ai_decision_packet,
)
from app.evidence_contract import ProductIdentity
from app.semantic_grounding import GroundedSource, GroundingCatalog, IMAGE_KIND, TEXT_KIND


def field(key: str, label: str, *, options=(), section="Product Description"):
    return {
        "attribute_key": key,
        "label": label,
        "section_heading": section,
        "required": True,
        "multi_value": False,
        "options": [{"text": item, "value": item} for item in options],
        "controls": [],
        "help_text": "",
    }


def grounding(tmp_path: Path) -> GroundingCatalog:
    image = tmp_path / "product.png"
    image.write_bytes(b"fake-image")
    return GroundingCatalog(
        sources=[
            GroundedSource(
                source_id="image:001",
                source_type="product_image",
                kind=IMAGE_KIND,
                origin=str(image),
                image_path=str(image),
                sha256="a" * 64,
            ),
            GroundedSource(
                source_id="supplier:001:text:0001:abc",
                source_type="supplier_web",
                kind=TEXT_KIND,
                origin="https://supplier.test/item",
                content="Colour: Black\nResolution: 720p\nModel: M8",
                sha256="b" * 64,
            ),
            GroundedSource(
                source_id="customer-text:001:text:0001:def",
                source_type="customer_file",
                kind=TEXT_KIND,
                origin="supplemental_text",
                content="Selected variant: M8 WiFi dual camera 64GB",
                sha256="c" * 64,
            ),
        ]
    )


def decision_payload(request, *, conflict=False):
    decisions = []
    for target in request["target_fields"]:
        if target["business_locked"]:
            decisions.append(
                {
                    "field_id": target["field_id"],
                    "status": "ready",
                    "values": ["999"],
                    "qualifier": "",
                    "confidence": 0.99,
                    "citations": [
                        {
                            "source_reference": "supplier:001:text:0001:abc",
                            "evidence_text": "Colour: Black",
                        }
                    ],
                    "alternatives": [],
                    "reason": "model incorrectly tried business guess",
                    "search_queries": [],
                }
            )
            continue
        if conflict and target["label"] == "Recording Resolution":
            decisions.append(
                {
                    "field_id": target["field_id"],
                    "status": "conflict",
                    "values": [],
                    "qualifier": "",
                    "confidence": 0.8,
                    "citations": [],
                    "alternatives": [
                        {
                            "values": ["720p"],
                            "qualifier": "",
                            "citations": [
                                {
                                    "source_reference": "supplier:001:text:0001:abc",
                                    "evidence_text": "Resolution: 720p",
                                }
                            ],
                            "reason": "supplier value",
                        },
                        {
                            "values": ["1080p"],
                            "qualifier": "",
                            "citations": [
                                {
                                    "source_reference": "image:001",
                                    "evidence_text": "visible FHD 1080P marking",
                                }
                            ],
                            "reason": "image value",
                        },
                    ],
                    "reason": "credible sources disagree",
                    "search_queries": [],
                }
            )
            continue
        decisions.append(
            {
                "field_id": target["field_id"],
                "status": "ready",
                "values": ["Black"],
                "qualifier": "",
                "confidence": 0.94,
                "citations": [
                    {
                        "source_reference": "supplier:001:text:0001:abc",
                        "evidence_text": "Colour: Black",
                    }
                ],
                "alternatives": [],
                "reason": "directly supported",
                "search_queries": [],
            }
        )
    return {
        "contract_version": 1,
        "product_identity": request["product_identity"],
        "schema_sha256": request["schema_sha256"],
        "source_manifest_sha256": request["source_manifest_sha256"],
        "decisions": decisions,
        "model_summary": "resolved product",
        "warnings": [],
    }


class FakeProvider:
    name = "fake-ai-first-provider"

    def __init__(self, *, conflict=False):
        self.calls = 0
        self.requests = []
        self.conflict = conflict

    def extract_json(self, request_payload):
        self.calls += 1
        self.requests.append(request_payload)
        return decision_payload(request_payload, conflict=self.conflict)


def test_request_gives_ai_all_fields_and_all_sources_in_one_product_task(tmp_path):
    fields = [
        field("colour", "Colour", options=("Black", "White")),
        field("recording_resolution", "Recording Resolution"),
    ]
    sources = grounding(tmp_path)
    request = build_ai_resolution_request(
        fields,
        sources,
        identity=ProductIdentity(sku="SKU-1"),
    )

    assert request["task"] == "resolve_all_live_marketplace_fields_from_product_sources"
    assert len(request["target_fields"]) == 2
    assert len(request["grounded_sources"]) == 3
    assert request["schema_sha256"] == schema_digest(fields)
    assert request["source_manifest_sha256"] == source_manifest_digest(sources)
    assert "Natural-language translation" in request["system_instruction"]
    assert all("field_id" in item for item in request["target_fields"])


def test_normal_path_is_exactly_one_model_call_for_many_sources(tmp_path):
    fields = [
        field("colour", "Colour", options=("Black", "White")),
        field("model_name", "Model Name"),
    ]
    provider = FakeProvider()
    result = run_ai_resolution(
        provider,
        fields,
        grounding(tmp_path),
        expected_identity=ProductIdentity(sku="SKU-1"),
        cache_dir=None,
    )

    assert provider.calls == 1
    assert result.model_calls == 1
    assert result.cache_hit is False
    assert len(result.packet.decisions) == 2


def test_whole_product_cache_replays_with_zero_model_calls(tmp_path):
    fields = [field("colour", "Colour", options=("Black", "White"))]
    sources = grounding(tmp_path)
    provider = FakeProvider()
    cache_dir = tmp_path / "cache"

    first = run_ai_resolution(
        provider,
        fields,
        sources,
        expected_identity=ProductIdentity(sku="SKU-1"),
        cache_dir=cache_dir,
        cache_namespace="model-config",
    )
    second = run_ai_resolution(
        provider,
        fields,
        sources,
        expected_identity=ProductIdentity(sku="SKU-1"),
        cache_dir=cache_dir,
        cache_namespace="model-config",
    )

    assert first.model_calls == 1
    assert second.model_calls == 0
    assert second.cache_hit is True
    assert provider.calls == 1


def test_schema_or_source_change_invalidates_whole_product_cache(tmp_path):
    fields = [field("colour", "Colour", options=("Black", "White"))]
    provider = FakeProvider()
    cache_dir = tmp_path / "cache"
    sources = grounding(tmp_path)

    run_ai_resolution(
        provider,
        fields,
        sources,
        expected_identity=ProductIdentity(sku="SKU-1"),
        cache_dir=cache_dir,
        cache_namespace="model-config",
    )

    changed_fields = [field("colour", "Colour", options=("Black", "White", "Red"))]
    run_ai_resolution(
        provider,
        changed_fields,
        sources,
        expected_identity=ProductIdentity(sku="SKU-1"),
        cache_dir=cache_dir,
        cache_namespace="model-config",
    )

    changed_sources = GroundingCatalog(sources=list(sources.sources))
    original = changed_sources.sources[1]
    changed_sources.sources[1] = GroundedSource(
        source_id=original.source_id,
        source_type=original.source_type,
        kind=original.kind,
        origin=original.origin,
        content="Colour: White",
        sha256="d" * 64,
    )
    run_ai_resolution(
        provider,
        changed_fields,
        changed_sources,
        expected_identity=ProductIdentity(sku="SKU-1"),
        cache_dir=cache_dir,
        cache_namespace="model-config",
    )

    assert provider.calls == 3


def test_ungrounded_text_citation_cannot_authorize_ready(tmp_path):
    colour = field("colour", "Colour", options=("Black", "White"))
    sources = grounding(tmp_path)
    packet = AIDecisionPacket(
        identity=ProductIdentity(sku="SKU-1"),
        schema_sha256=schema_digest([colour]),
        source_manifest_sha256=source_manifest_digest(sources),
        decisions=[
            FieldDecision(
                field_id=field_id(colour),
                status=READY,
                values=["Black"],
                confidence=0.95,
                citations=[
                    DecisionCitation(
                        source_reference="supplier:001:text:0001:abc",
                        evidence_text="This text does not exist in the supplier source",
                    )
                ],
            )
        ],
    )

    validated = validate_ai_decision_packet(
        packet,
        [colour],
        sources,
        expected_identity=ProductIdentity(sku="SKU-1"),
    )

    assert validated.decisions[0].status == REVIEW
    assert validated.decisions[0].citations == []
    assert any("ungrounded citation" in warning for warning in validated.warnings)


def test_business_field_is_forced_locked_even_if_model_returns_ready(tmp_path):
    selling = field(
        "flipkart_selling_price",
        "Your selling price",
        section="Price, Stock and Shipping Information",
    )
    sources = grounding(tmp_path)
    request = build_ai_resolution_request(
        [selling],
        sources,
        identity=ProductIdentity(sku="SKU-1"),
    )
    raw = decision_payload(request)
    validated = validate_ai_decision_packet(
        AIDecisionPacket.from_mapping(raw),
        [selling],
        sources,
        expected_identity=ProductIdentity(sku="SKU-1"),
    )

    decision = validated.decisions[0]
    assert decision.status == BUSINESS_LOCKED
    assert decision.values == []
    assert decision.citations == []


def test_real_cross_source_conflict_is_preserved_for_ai_to_express(tmp_path):
    resolution = field("recording_resolution", "Recording Resolution")
    sources = grounding(tmp_path)
    provider = FakeProvider(conflict=True)
    result = run_ai_resolution(
        provider,
        [resolution],
        sources,
        expected_identity=ProductIdentity(sku="SKU-1"),
    )

    decision = result.packet.decisions[0]
    assert decision.status == CONFLICT
    assert len(decision.alternatives) == 2
    assert {alternative.values[0] for alternative in decision.alternatives} == {"720p", "1080p"}


def test_model_omitted_field_becomes_missing_instead_of_local_guess(tmp_path):
    colour = field("colour", "Colour")
    model = field("model_name", "Model Name")
    sources = grounding(tmp_path)
    packet = AIDecisionPacket(
        identity=ProductIdentity(sku="SKU-1"),
        schema_sha256=schema_digest([colour, model]),
        source_manifest_sha256=source_manifest_digest(sources),
        decisions=[
            FieldDecision(
                field_id=field_id(colour),
                status=READY,
                values=["Black"],
                confidence=0.95,
                citations=[
                    DecisionCitation(
                        source_reference="supplier:001:text:0001:abc",
                        evidence_text="Colour: Black",
                    )
                ],
            )
        ],
    )

    validated = validate_ai_decision_packet(
        packet,
        [colour, model],
        sources,
        expected_identity=ProductIdentity(sku="SKU-1"),
    )
    assert [item.status for item in validated.decisions] == [READY, MISSING]
