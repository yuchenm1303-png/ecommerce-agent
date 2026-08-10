from __future__ import annotations

from app.ai_decisions import AIDecisionPacket, FieldDecision, MISSING, READY, field_id
from app.best_effort_inference import build_best_effort_inference_request
from app.compact_evidence import CompactEvidence
from app.evidence_contract import ProductIdentity
from app.fill_plan import _hard_guard_values
from app.live_schema import live_schema_payload
from app.product_facts import build_product_fact_request


def _numeric_unit_field() -> dict:
    return {
        "attribute_key": "weight_with_bracket",
        "label": "Weight With Bracket",
        "section_heading": "Additional Description",
        "required": False,
        "multi_value": False,
        # Reproduce the old semantic aggregate: unit-control options polluted
        # the field-level options list even though the value input was free/numeric.
        "options": [
            {"text": "kg", "value": "kg"},
            {"text": "g", "value": "g"},
        ],
        "controls": [
            {
                "id": "weight_with_bracket",
                "name": "weight_with_bracket_0_value",
                "field_kind": "input",
                "options": [],
                "context_text": "Weight With Bracket",
            },
            {
                "name": "weight_with_bracket_0_qualifier",
                "field_kind": "select",
                "options": [
                    {"text": "kg", "value": "kg"},
                    {"text": "g", "value": "g"},
                ],
            },
        ],
        "help_text": "",
    }


def test_live_schema_separates_value_options_from_qualifier_options() -> None:
    payload = live_schema_payload([_numeric_unit_field()])
    field = payload["fields"][0]

    assert field["options"] == []
    assert field["qualifier_options"] == ["kg", "g"]


def test_fill_plan_matches_numeric_value_against_qualifier_contract_not_unit_as_value_option() -> None:
    field = live_schema_payload([_numeric_unit_field()])["fields"][0]
    decision = FieldDecision(
        field_id=field_id(field),
        status=READY,
        values=["320"],
        qualifier="g",
        confidence=1.0,
    )

    values, qualifier, error = _hard_guard_values(field, decision)

    assert values == ["320"]
    assert qualifier == "g"
    assert error is None


def test_direct_product_fact_contract_requires_single_value_and_real_qualifiers() -> None:
    field = live_schema_payload([_numeric_unit_field()])["fields"][0]
    compact = CompactEvidence(
        web_text="",
        image_facts="",
        text_source_count=0,
        image_count=0,
        image_fact_count=0,
        citation_aliases={},
        sha256="a" * 64,
    )

    request = build_product_fact_request([field], compact)
    rules = "\n".join(request["rules"])
    target = request["target_fields"][0]

    assert target["multi_value"] is False
    assert target.get("options", []) == []
    assert target["qualifier_options"] == ["kg", "g"]
    assert "multi_value=false" in rules
    assert "exactly one value string" in rules
    assert "qualifier is only the marketplace unit/qualifier" in rules
    assert "bare finite number" in rules


def test_best_effort_contract_exposes_false_multi_value_and_numeric_unit_shape() -> None:
    field = live_schema_payload([_numeric_unit_field()])["fields"][0]
    identifier = field_id(field)
    packet = AIDecisionPacket(
        identity=ProductIdentity(),
        schema_sha256="",
        source_manifest_sha256="",
        decisions=[FieldDecision(field_id=identifier, status=MISSING)],
    )

    request = build_best_effort_inference_request(
        packet,
        [field],
        product_fingerprint="dash camera",
    )
    target = request["target_fields"][0]
    rules = "\n".join(request["rules"])

    assert target["multi_value"] is False
    assert target["qualifier_options"] == ["kg", "g"]
    assert "multi_value=false" in rules
    assert "qualifier is only a marketplace unit/qualifier" in rules
    assert "bare finite number" in rules
