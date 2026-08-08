from app.ai_decisions import build_ai_resolution_request, FieldDecision, MISSING, field_id
from app.semantic_grounding import GroundingCatalog
from app.web_enrichment import _target_decisions
from app.evidence_contract import ProductIdentity
from app.ai_decisions import AIDecisionPacket, schema_digest, source_manifest_digest


def test_ai_prompt_keeps_field_identity_and_hard_output_shape_in_ai():
    field = {
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
    request = build_ai_resolution_request([field], GroundingCatalog(sources=[]))
    target = request["target_fields"][0]
    rules = "\n".join(request["rules"])

    assert target["attribute_key"] == "package_breadth"
    assert target["label"] == "Length"
    assert target["qualifier_options"] == ["cm", "mm"]
    assert "MUST be CONFLICT" in rules
    assert "multi_value=false" in rules
    assert "qualifier_options" in rules
    assert "Package/packaging dimensions" in rules
    assert "explicit negative evidence" in rules


def test_cli_defaults_target_plus_json_mode_and_responses_web_endpoint():
    from makro_resolve_ai import build_parser

    parser = build_parser()
    args = parser.parse_args(["--qa", "q.xlsx", "--live-schema", "live.json"])
    assert args.model == "qwen3.6-plus"
    assert args.structured_mode == "json_object"
    assert args.enable_thinking is False
    assert args.web_enrich == "auto"
    assert args.web_base_url == ""
    options = {option for action in parser._actions for option in action.option_strings}
    assert "--web-base-url" in options
    assert "--web-native-base-url" not in options


def test_missing_field_without_model_authored_query_still_enters_single_web_phase():
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
    targets = _target_decisions(packet, [field])
    assert len(targets) == 1
    assert targets[0][1].field_id == field_id(field)
