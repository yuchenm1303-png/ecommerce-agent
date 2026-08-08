from app.ai_decisions import FieldDecision, MISSING, field_id, schema_digest, source_manifest_digest
from app.evidence_contract import ProductIdentity
from app.field_mapping import build_field_mapping_request
from app.product_profile import ProductFact, ProductProfile, ProfileCandidate, build_product_profile_request
from app.semantic_grounding import GroundedSource, GroundingCatalog, TEXT_KIND
from app.web_enrichment import _targets
from app.ai_decisions import AIDecisionPacket, DecisionCitation


def _field():
    return {
        "attribute_key": "package_breadth",
        "label": "Length",
        "section_heading": "Price, Stock and Shipping Information",
        "required": False,
        "multi_value": False,
        "options": [],
        "qualifier_options": ["cm", "mm"],
        "controls": [],
        "help_text": "",
    }


def _grounding():
    return GroundingCatalog(
        sources=[
            GroundedSource(
                source_id="supplier:001:text:0001:abc",
                source_type="supplier_web",
                kind=TEXT_KIND,
                origin="supplier",
                content="Package 16 x 11 x 7 cm",
                sha256="a" * 64,
            )
        ]
    )


def _profile():
    sources = _grounding()
    return ProductProfile(
        identity=ProductIdentity(model_number="M8"),
        source_manifest_sha256=source_manifest_digest(sources),
        facts=[
            ProductFact(
                name="package_dimensions",
                scope="packaging",
                status="supported",
                candidates=(
                    ProfileCandidate(
                        value="16 x 11 x 7 cm",
                        citations=(
                            DecisionCitation(
                                "supplier:001:text:0001:abc",
                                "Package 16 x 11 x 7 cm",
                            ),
                        ),
                    ),
                ),
            )
        ],
    )


def test_product_profile_prompt_has_no_marketplace_target_fields():
    request = build_product_profile_request(_grounding(), identity=ProductIdentity(model_number="M8"))
    assert request["task"] == "understand_product_from_local_evidence"
    assert request["target_fields"] == []
    assert len(request["grounded_sources"]) == 1
    rules = "\n".join(request["rules"])
    assert "Do not map" not in rules  # instruction lives in system/prompt, not a schema-specific field rule
    assert "packaging dimensions" in rules
    assert "Negative facts" in rules


def test_field_mapping_prompt_keeps_stable_field_identity_and_uses_profile_only():
    field = _field()
    request = build_field_mapping_request([field], _profile())
    target = request["target_fields"][0]
    assert target["attribute_key"] == "package_breadth"
    assert target["label"] == "Length"
    assert target["qualifier_options"] == ["cm", "mm"]
    assert len(request["grounded_sources"]) == 1
    assert request["grounded_sources"][0]["source_type"] == "derived_product_profile"
    assert request["grounded_sources"][0]["kind"] == "text"
    rules = "\n".join(request["rules"])
    assert "CONFLICT" in rules
    assert "multi_value=false" in rules
    assert "packaging dimensions/weight" in rules


def test_cli_defaults_to_qwen37_plus_and_bounded_parallel_stages():
    from makro_resolve_ai import build_parser

    parser = build_parser()
    args = parser.parse_args(["--qa", "q.xlsx", "--live-schema", "live.json"])
    assert args.model == "qwen3.7-plus"
    assert args.web_search_model == "qwen3.7-max"
    assert args.structured_mode == "json_object"
    assert args.enable_thinking is False
    assert args.field_batch_size == 12
    assert args.field_concurrency == 4
    assert args.web_batch_size == 5
    assert args.web_concurrency == 3
    options = {option for action in parser._actions for option in action.option_strings}
    assert "--max-repair-attempts" not in options
    assert "--field-batch-size" in options
    assert "--web-batch-size" in options


def test_missing_field_without_authored_query_still_enters_web_research():
    field = {
        "attribute_key": "image_sensor",
        "label": "Image Sensor",
        "section_heading": "Additional Description",
        "required": False,
        "multi_value": False,
        "options": [],
        "controls": [],
        "help_text": "",
    }
    grounding = GroundingCatalog(sources=[])
    packet = AIDecisionPacket(
        identity=ProductIdentity(model_number="M8"),
        schema_sha256=schema_digest([field]),
        source_manifest_sha256=source_manifest_digest(grounding),
        decisions=[FieldDecision(field_id=field_id(field), status=MISSING, search_queries=[])],
    )
    targets = _targets(packet, [field])
    assert len(targets) == 1
    assert targets[0][1].field_id == field_id(field)
