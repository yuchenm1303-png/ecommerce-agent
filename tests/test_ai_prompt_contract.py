from app.ai_decisions import (
    AIDecisionPacket,
    CONFLICT,
    MISSING,
    DecisionAlternative,
    DecisionCitation,
    FieldDecision,
    field_id,
    schema_digest,
    source_manifest_digest,
)
from app.compact_evidence import CompactEvidence
from app.evidence_contract import ProductIdentity
from app.product_facts import build_product_fact_request
from app.semantic_grounding import GroundedSource, GroundingCatalog, TEXT_KIND
from app.web_enrichment import _targets


PRODUCT_URL = "https://detail.1688.com/offer/850845635717.html"


def _field():
    return {
        "attribute_key": "package_breadth",
        "label": "Breadth",
        "section_heading": "Price, Stock and Shipping Information",
        "required": False,
        "multi_value": False,
        "options": [],
        "qualifier_options": ["cm", "mm"],
        "controls": [],
        "help_text": "Package breadth",
        "context_text": "Breadth cm",
    }


def _grounding():
    return GroundingCatalog(
        sources=[
            GroundedSource(
                source_id="supplier:001:text:0001:abc",
                source_type="supplier_web",
                kind=TEXT_KIND,
                origin=PRODUCT_URL,
                content="Package length 16 cm, width 11 cm, height 7 cm",
                sha256="a" * 64,
            )
        ]
    )


def test_local_fill_prompt_reads_original_sources_directly_and_uses_typed_schema():
    evidence = CompactEvidence(
        web_text="[s1] Package length 16 cm, width 11 cm, height 7 cm",
        image_facts="",
        text_source_count=1,
        image_count=0,
        image_fact_count=0,
        citation_aliases={"s1": "supplier:001:text:0001:abc"},
        sha256="c" * 64,
    )
    request = build_product_fact_request(
        [_field()],
        evidence,
        product_url=PRODUCT_URL,
    )
    target = request["target_fields"][0]
    assert request["task"] == "resolve_compact_product_facts"
    assert target["attribute_key"] == "package_breadth"
    assert target["label"] == "Breadth"
    assert target["qualifier_options"] == ["cm", "mm"]
    assert target["context_text"] == "Breadth cm"
    assert request["grounded_sources"][0]["source_type"] == "compact_supplier_evidence"
    assert request["product_identity"] == {"source_product_url": PRODUCT_URL}
    assert request["strict_json_schema"] is True
    properties = request["json_contract"]["properties"]
    assert set(properties) == {"facts", "model_summary"}
    rules = "\n".join(request["rules"])
    assert "physical scope" in rules
    assert "CONFLICT" in rules


def test_cli_defaults_to_qwen37_plus_and_single_product_url_input():
    from makro_resolve_ai import build_parser

    parser = build_parser()
    args = parser.parse_args(["--live-schema", "live.json", "--product-url", PRODUCT_URL])
    assert args.model == "qwen3.7-plus"
    assert args.web_search_model == "qwen3.7-max"
    assert args.structured_mode == "json_object"
    assert args.enable_thinking is False
    assert args.web_batch_size == 5
    assert args.web_concurrency == 3
    assert args.source_cache_ttl_seconds == 900
    options = {option for action in parser._actions for option in action.option_strings}
    assert "--product-url" in options
    assert "--sku" not in options
    assert "--qa" not in options
    assert "--source-cdp-port" in options
    assert "--refresh-source" in options
    assert "--max-repair-attempts" not in options
    assert "--field-batch-size" not in options
    assert "--web-batch-size" in options


def test_only_empty_field_enters_web_search_and_local_conflict_stays_frozen():
    missing_field = {
        "attribute_key": "image_sensor",
        "label": "Image Sensor",
        "section_heading": "Additional Description",
        "required": False,
        "multi_value": False,
        "options": [],
        "controls": [],
        "help_text": "",
    }
    conflict_field = {
        **missing_field,
        "attribute_key": "recording_resolution",
        "label": "Recording Resolution",
    }
    grounding = GroundingCatalog(sources=[])
    citation = DecisionCitation("local", "evidence")
    packet = AIDecisionPacket(
        identity=ProductIdentity(),
        schema_sha256=schema_digest([missing_field, conflict_field]),
        source_manifest_sha256=source_manifest_digest(grounding),
        decisions=[
            FieldDecision(field_id=field_id(missing_field), status=MISSING),
            FieldDecision(
                field_id=field_id(conflict_field),
                status=CONFLICT,
                alternatives=[
                    DecisionAlternative(values=("720p",), citations=(citation,)),
                    DecisionAlternative(values=("1080p",), citations=(citation,)),
                ],
            ),
        ],
    )
    targets = _targets(packet, [missing_field, conflict_field])
    assert [item[1].field_id for item in targets] == [field_id(missing_field)]
