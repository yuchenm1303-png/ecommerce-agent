from __future__ import annotations

from app.ai_decisions import (
    MISSING,
    READY,
    AIDecisionPacket,
    DecisionCitation,
    FieldDecision,
    field_id,
    schema_digest,
    source_manifest_digest,
)
from app.best_effort_inference import (
    _policy_handoff_from_web,
    build_best_effort_inference_request,
)
from app.business_fields import (
    WARRANTY_SERVICE_TYPE_POLICY,
    WARRANTY_SUMMARY_POLICY,
    generated_business_bundle,
    is_business_question,
)
from app.compact_evidence import CompactEvidence
from app.evidence_contract import ProductIdentity
from app.listing_content_policy import (
    GLOBAL_CONTENT_RULES,
    LISTING_INTENT_ENV,
    allow_best_effort_inference,
    allow_required_fallback,
    field_content_policy,
    requires_exact_web_identity,
)
from app.product_facts import build_product_fact_request
from app.semantic_grounding import GroundedSource, GroundingCatalog, TEXT_KIND


URL = "https://supplier.test/product"


def _field(
    key: str,
    label: str,
    *,
    section: str = "Product Description",
    required: bool = False,
    multi_value: bool = False,
) -> dict:
    return {
        "attribute_key": key,
        "label": label,
        "section_heading": section,
        "required": required,
        "multi_value": multi_value,
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
                origin=URL,
                content="Cordless heated neck massager with USB-C charging",
                sha256="a" * 64,
            )
        ]
    )


def _compact() -> CompactEvidence:
    return CompactEvidence(
        web_text="[s1] Cordless heated neck massager with USB-C charging",
        image_facts="",
        text_source_count=1,
        image_count=0,
        image_fact_count=0,
        citation_aliases={"s1": "supplier:001:text:1"},
        sha256="b" * 64,
    )


def test_content_policy_keeps_high_value_copy_rules_and_safe_exceptions() -> None:
    model = field_content_policy(_field("model_name", "Model Name"))
    description = field_content_policy(_field("description", "Description"))
    keywords = field_content_policy(_field("keywords", "Keywords", multi_value=True))

    assert model["policy_id"] == "model_name"
    assert "South African" in model["instruction"]
    assert "Omit brand names" in model["instruction"]
    assert model["required_fallback"] == "manual_only"
    assert description["policy_id"] == "description"
    assert "medical-style claims" in description["instruction"]
    assert keywords["policy_id"] == "keywords"
    assert keywords["max_values"] == 5
    assert "search volume" in keywords["instruction"]
    assert any("USB-C" in rule for rule in GLOBAL_CONTENT_RULES)
    assert any("medical-style" in rule for rule in GLOBAL_CONTENT_RULES)


def test_vehicle_model_name_is_exact_compatibility_not_product_model_alias() -> None:
    vehicle_model = field_content_policy(
        _field("vehicle_model_name", "Vehicle Model Name", required=True)
    )

    assert vehicle_model["policy_id"] == "vehicle_model_name"
    assert vehicle_model["generation_mode"] == "grounded_only"
    assert vehicle_model["evidence_mode"] == "exact_product_only"
    assert vehicle_model["best_effort"] == "disabled"
    assert vehicle_model["required_fallback"] == "manual_only"
    assert "product Model Number" in vehicle_model["instruction"]
    assert "Never output N/A" in vehicle_model["instruction"]
    assert allow_required_fallback(
        _field("vehicle_model_name", "Vehicle Model Name", required=True)
    ) is False


def test_exact_identity_compliance_and_package_fields_keep_ai_strict_and_protect_required_fallback() -> None:
    fields = [
        _field("ean", "EAN"),
        _field("certifications", "Certifications"),
        _field("sales_package", "Sales Package"),
    ]
    for target in fields:
        assert requires_exact_web_identity(target) is True
        assert allow_best_effort_inference(target) is False
        assert allow_required_fallback(target) is False
        assert field_content_policy(target)["evidence_mode"] == "exact_product_only"


def test_listing_intent_turns_sales_package_into_offer_aware_synthesis(monkeypatch) -> None:
    monkeypatch.setenv(LISTING_INTENT_ENV, "黑色净化器 + 2瓶香薰精油")
    sales_package = _field("sales_package", "Sales Package", required=True)
    colour = _field("colour", "Colour")

    package_policy = field_content_policy(sales_package)
    colour_policy = field_content_policy(colour)

    assert package_policy["listing_intent"] == "黑色净化器 + 2瓶香薰精油"
    assert package_policy["generation_mode"] == "grounded_synthesis"
    assert package_policy["best_effort"] == "listing_intent_allowed"
    assert allow_best_effort_inference(sales_package) is True
    assert allow_required_fallback(sales_package) is False
    assert colour_policy["policy_id"] == "listing_intent_scope"
    assert colour_policy["listing_intent"] == "黑色净化器 + 2瓶香薰精油"


def test_listing_intent_allows_sales_package_in_final_policy_stage(monkeypatch) -> None:
    monkeypatch.setenv(LISTING_INTENT_ENV, "Black purifier + 2 fragrance oil bottles")
    fields = [
        _field("model_name", "Model Name"),
        _field("sales_package", "Sales Package"),
        _field("ean", "EAN"),
    ]
    grounding = _grounding()
    packet = AIDecisionPacket(
        identity=ProductIdentity(),
        schema_sha256=schema_digest(fields),
        source_manifest_sha256=source_manifest_digest(grounding),
        decisions=[FieldDecision(field_id=field_id(field), status=MISSING) for field in fields],
        extractor="test",
    )

    request = build_best_effort_inference_request(
        packet,
        fields,
        product_fingerprint="portable air purifier ionizer",
    )
    targets = {item["key"]: item for item in request["target_fields"]}

    assert set(targets) == {"model_name", "sales_package"}
    assert targets["sales_package"]["content_policy"]["listing_intent"].startswith("Black purifier")
    assert "ean" not in targets


def test_title_contributing_field_keeps_copy_policy_and_protects_required_fallback() -> None:
    target = _field("type", "Type", required=True)
    target["context_text"] = "Attributes that can make up title"
    policy = field_content_policy(target)

    assert policy["policy_id"] == "title_contributor"
    assert policy["required_fallback"] == "manual_only"
    assert allow_required_fallback(target) is False
    assert "Never use N/A" in policy["instruction"]


def test_ordinary_required_field_keeps_existing_deterministic_fallback() -> None:
    target = _field("required_note", "Required Note", required=True)
    assert field_content_policy(target) == {}
    assert allow_required_fallback(target) is True


def test_local_product_fact_request_carries_policy_but_excludes_warranty_business_fields() -> None:
    model = _field("model_name", "Model Name", required=True)
    ean = _field("ean", "EAN")
    warranty = _field("warranty_summary", "Warranty Summary")
    request = build_product_fact_request([model, ean, warranty], _compact(), product_url=URL)

    by_key = {item["attribute_key"]: item for item in request["target_fields"]}
    assert set(by_key) == {"model_name", "ean"}
    assert by_key["model_name"]["content_policy"]["policy_id"] == "model_name"
    assert by_key["ean"]["content_policy"]["best_effort"] == "disabled"
    assert any("content_policy" in rule for rule in request["rules"])


def test_best_effort_targets_generated_copy_but_not_exact_only_fields() -> None:
    fields = [
        _field("model_name", "Model Name"),
        _field("description", "Description"),
        _field("keywords", "Keywords", multi_value=True),
        _field("ean", "EAN"),
        _field("certifications", "Certifications"),
        _field("sales_package", "Sales Package"),
        _field("vehicle_model_name", "Vehicle Model Name"),
    ]
    grounding = _grounding()
    packet = AIDecisionPacket(
        identity=ProductIdentity(),
        schema_sha256=schema_digest(fields),
        source_manifest_sha256=source_manifest_digest(grounding),
        decisions=[FieldDecision(field_id=field_id(field), status=MISSING) for field in fields],
        extractor="test",
    )

    request = build_best_effort_inference_request(
        packet,
        fields,
        product_fingerprint="cordless heated neck massager USB-C",
    )
    targets = {item["key"]: item for item in request["target_fields"]}

    assert set(targets) == {"model_name", "description", "keywords"}
    assert targets["model_name"]["content_policy"]["policy_id"] == "model_name"
    assert targets["keywords"]["content_policy"]["max_values"] == 5


def test_web_authored_policy_copy_is_rerouted_but_supplier_grounded_copy_is_frozen() -> None:
    model = _field("model_name", "Model Name")
    ean = _field("ean", "EAN")
    fields = [model, ean]
    grounding = _grounding()
    packet = AIDecisionPacket(
        identity=ProductIdentity(),
        schema_sha256=schema_digest(fields),
        source_manifest_sha256=source_manifest_digest(grounding),
        decisions=[
            FieldDecision(
                field_id=field_id(model),
                status=READY,
                values=["Competitor Style Neck Massager"],
                confidence=0.8,
                citations=[DecisionCitation("web-search:abc", "Comparable listing title")],
            ),
            FieldDecision(
                field_id=field_id(ean),
                status=READY,
                values=["1234567890123"],
                confidence=0.8,
                citations=[DecisionCitation("web-search:def", "Similar listing EAN")],
            ),
        ],
        extractor="web",
    )

    filtered = _policy_handoff_from_web(packet, fields)
    by_id = {item.field_id: item for item in filtered.decisions}

    assert by_id[field_id(model)].status == MISSING
    assert by_id[field_id(ean)].status == MISSING
    assert "rerouted" in by_id[field_id(model)].reason
    assert "Exact-only" in by_id[field_id(ean)].reason

    supplier_packet = AIDecisionPacket(
        identity=ProductIdentity(),
        schema_sha256=schema_digest([model]),
        source_manifest_sha256=source_manifest_digest(grounding),
        decisions=[
            FieldDecision(
                field_id=field_id(model),
                status=READY,
                values=["Cordless Heated Neck Massager with USB-C Charging"],
                confidence=1.0,
                citations=[DecisionCitation("supplier:001:text:1", "Cordless heated neck massager with USB-C charging")],
            )
        ],
        extractor="local",
    )
    frozen = _policy_handoff_from_web(supplier_packet, [model])
    assert frozen.decisions[0].status == READY
    assert frozen.decisions[0].values == ["Cordless Heated Neck Massager with USB-C Charging"]


def test_warranty_values_are_explicit_seller_policy_not_ai_product_facts() -> None:
    bundle = generated_business_bundle(URL, sku="812345678901")
    summary = bundle.candidates(("Warranty Summary",))
    service = bundle.candidates(("Warranty Service Type",))

    assert is_business_question("Warranty Summary") is True
    assert is_business_question("Warranty Service Type") is True
    assert len(summary) == 1 and summary[0].value == WARRANTY_SUMMARY_POLICY
    assert len(service) == 1 and service[0].value == WARRANTY_SERVICE_TYPE_POLICY
    assert summary[0].source_type == "rule"
    assert service[0].source_type == "rule"
    assert summary[0].source_reference.startswith("policy:listing-content:")
    assert service[0].source_reference.startswith("policy:listing-content:")
