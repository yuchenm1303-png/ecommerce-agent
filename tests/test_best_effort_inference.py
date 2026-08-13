from __future__ import annotations

from app.ai_decisions import (
    BUSINESS_LOCKED,
    CONFLICT,
    MISSING,
    READY,
    AIDecisionPacket,
    DecisionAlternative,
    DecisionCitation,
    FieldDecision,
    field_id,
    schema_digest,
    source_manifest_digest,
)
from app.best_effort_inference import (
    INFERENCE_REFERENCE,
    build_best_effort_inference_request,
    run_best_effort_inference,
)
from app.evidence_contract import ProductIdentity
from app.semantic_grounding import GroundedSource, GroundingCatalog, TEXT_KIND


def _field(key: str, label: str, *, section: str = "Additional Description"):
    return {
        "attribute_key": key,
        "label": label,
        "section_heading": section,
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
        [
            GroundedSource(
                source_id="supplier:001:text:1",
                source_type="supplier_web",
                kind=TEXT_KIND,
                origin="https://example.test/product",
                content="M8 black dual dash camera",
                sha256="a" * 64,
            )
        ]
    )


class FakeInferenceProvider:
    name = "fake-inference"

    def __init__(self):
        self.calls = 0
        self.requests = []

    def extract_json(self, request):
        self.calls += 1
        self.requests.append(request)
        decisions = []
        for target in request["target_fields"]:
            if target["key"] == "image_sensor":
                decisions.append(
                    {
                        "field_id": target["field_id"],
                        "status": "ready",
                        "values": ["GC2053"],
                        "qualifier": "",
                        "confidence": 0.9,
                        "reason": "common M8 family sensor",
                    }
                )
            else:
                decisions.append(
                    {
                        "field_id": target["field_id"],
                        "status": "missing",
                        "values": [],
                        "qualifier": "",
                        "confidence": 0.0,
                        "reason": "identifier cannot be invented",
                    }
                )
        return {"decisions": decisions, "summary": "best effort"}


def _packet(fields, grounding):
    citation = DecisionCitation("supplier:001:text:1", "M8 black dual dash camera")
    return AIDecisionPacket(
        identity=ProductIdentity(model_number="M8"),
        schema_sha256=schema_digest(fields),
        source_manifest_sha256=source_manifest_digest(grounding),
        decisions=[
            FieldDecision(field_id=field_id(fields[0]), status=READY, values=["Black"], citations=[citation]),
            FieldDecision(
                field_id=field_id(fields[1]),
                status=CONFLICT,
                alternatives=[
                    DecisionAlternative(values=("720p",), citations=(citation,)),
                    DecisionAlternative(values=("1080p",), citations=(citation,)),
                ],
            ),
            FieldDecision(field_id=field_id(fields[2]), status=MISSING),
            FieldDecision(field_id=field_id(fields[3]), status=MISSING),
            FieldDecision(field_id=field_id(fields[4]), status=BUSINESS_LOCKED),
        ],
        extractor="web",
    )


def test_request_targets_only_policy_eligible_missing_non_business_fields():
    fields = [
        _field("colour", "Colour"),
        _field("recording_resolution", "Recording Resolution"),
        _field("image_sensor", "Image Sensor"),
        _field("ean", "EAN"),
        _field("mrp", "Base Price", section="Price, Stock and Shipping Information"),
    ]
    request = build_best_effort_inference_request(
        _packet(fields, _grounding()),
        fields,
        product_fingerprint="M8 dual dash camera",
    )
    assert [item["key"] for item in request["target_fields"]] == ["image_sensor"]
    assert request["grounded_sources"] == []
    assert request["evidence_policy"] == "best_effort"
    assert request["context"]["product_fingerprint"] == "M8 dual dash camera"
    assert request["context"]["web_evidence"] == []
    assert len(str(request)) < 10000


def test_inference_fills_missing_freezes_existing_and_hot_caches(tmp_path):
    fields = [
        _field("colour", "Colour"),
        _field("recording_resolution", "Recording Resolution"),
        _field("image_sensor", "Image Sensor"),
        _field("ean", "EAN"),
        _field("mrp", "Base Price", section="Price, Stock and Shipping Information"),
    ]
    grounding = _grounding()
    initial = _packet(fields, grounding)
    provider = FakeInferenceProvider()
    first = run_best_effort_inference(
        provider,
        initial,
        fields,
        grounding,
        product_fingerprint="M8 dual dash camera",
        cache_dir=tmp_path,
        cache_namespace="test",
    )
    second = run_best_effort_inference(
        provider,
        initial,
        fields,
        grounding,
        product_fingerprint="M8 dual dash camera",
        cache_dir=tmp_path,
        cache_namespace="test",
    )

    assert first.model_calls == 1
    assert second.model_calls == 0
    assert second.cache_hit is True
    assert provider.calls == 1
    assert second.packet.decisions[0].status == READY
    assert second.packet.decisions[0].values == ["Black"]
    assert second.packet.decisions[1].status == CONFLICT
    assert second.packet.decisions[2].status == READY
    assert second.packet.decisions[2].values == ["GC2053"]
    assert second.packet.decisions[2].confidence == 0.75
    assert second.packet.decisions[2].citations[0].source_reference == INFERENCE_REFERENCE
    assert second.packet.decisions[3].status == MISSING
    assert second.packet.decisions[4].status == BUSINESS_LOCKED
