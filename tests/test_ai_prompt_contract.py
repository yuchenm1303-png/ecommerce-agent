from app.ai_decisions import build_ai_resolution_request
from app.semantic_grounding import GroundingCatalog


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


def test_cli_defaults_target_plus_and_json_mode():
    from makro_resolve_ai import build_parser
    parser = build_parser()
    args = parser.parse_args(["--qa", "q.xlsx", "--live-schema", "live.json"])
    assert args.model == "qwen3.6-plus"
    assert args.structured_mode == "json_object"
    assert args.enable_thinking is False
