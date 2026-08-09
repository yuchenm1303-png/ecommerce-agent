from __future__ import annotations

from app.ai_decisions import BUSINESS_LOCKED, CONFLICT, MISSING, READY, field_id
from app.compact_evidence import CompactEvidence
from app.product_facts import build_product_fact_request, run_product_facts
from app.semantic_grounding import GroundedSource, GroundingCatalog, TEXT_KIND


def _field(key: str, label: str, *, business: bool = False):
    return {
        "attribute_key": "flipkart_selling_price" if business else key,
        "label": "Your selling price" if business else label,
        "section_heading": "Price, Stock and Shipping Information" if business else "Product Description",
        "required": True,
        "multi_value": False,
        "options": [],
        "qualifier_options": ["cm"] if key == "package_length" else [],
        "controls": [],
        "help_text": "",
        "context_text": "",
    }


def _grounding():
    return GroundingCatalog(
        [
            GroundedSource(
                source_id="supplier:001:text:0001:abc",
                source_type="supplier_web",
                kind=TEXT_KIND,
                origin="https://supplier.test/item",
                content="Length 16 cm; resolution 720p",
                sha256="a" * 64,
            ),
            GroundedSource(
                source_id="supplier:001:text:0002:def",
                source_type="supplier_web",
                kind=TEXT_KIND,
                origin="https://supplier.test/item",
                content="Resolution 1080p",
                sha256="b" * 64,
            ),
        ]
    )


def _compact():
    return CompactEvidence(
        web_text="[s1] Length 16 cm; resolution 720p\n[s2] Resolution 1080p",
        image_facts="",
        text_source_count=2,
        image_count=0,
        image_fact_count=0,
        citation_aliases={
            "s1": "supplier:001:text:0001:abc",
            "s2": "supplier:001:text:0002:def",
        },
        sha256="c" * 64,
    )


class Provider:
    name = "fake-product-facts"

    def __init__(self):
        self.calls = 0

    def extract_json(self, request):
        self.calls += 1
        by_key = {item["attribute_key"]: item["field_id"] for item in request["target_fields"]}
        return {
            "facts": [
                {
                    "field_id": by_key["package_length"],
                    "status": "ready",
                    "values": ["16"],
                    "qualifier": "cm",
                    "confidence": 1.0,
                    "citations": [{"source_reference": "[s1]", "evidence_text": "Length 16 cm"}],
                    "alternatives": [],
                },
                {
                    "field_id": by_key["recording_resolution"],
                    "status": "conflict",
                    "values": [],
                    "qualifier": "",
                    "confidence": 1.0,
                    "citations": [],
                    "alternatives": [
                        {
                            "values": ["720p"],
                            "qualifier": "",
                            "citations": [{"source_reference": "s1", "evidence_text": "resolution 720p"}],
                        },
                        {
                            "values": ["1080p"],
                            "qualifier": "",
                            "citations": [{"source_reference": "s2", "evidence_text": "Resolution 1080p"}],
                        },
                    ],
                },
            ],
            "model_summary": "grounded facts",
        }


def test_request_sees_all_non_business_fields_once_and_uses_compact_text_only():
    fields = [
        _field("package_length", "Length"),
        _field("recording_resolution", "Recording Resolution"),
        _field("price", "Price", business=True),
    ]
    request = build_product_fact_request(fields, _compact(), product_url="https://supplier.test/item")

    assert request["task"] == "resolve_compact_product_facts"
    assert len(request["target_fields"]) == 2
    assert request["strict_json_schema"] is True
    assert {source["source_id"] for source in request["grounded_sources"]} == {"compact:web"}
    assert set(request["json_contract"]["properties"]) == {"facts", "model_summary"}


def test_global_facts_expand_aliases_preserve_conflict_and_synthesize_missing_and_business(tmp_path):
    fields = [
        _field("package_length", "Length"),
        _field("recording_resolution", "Recording Resolution"),
        _field("gps", "GPS"),
        _field("price", "Price", business=True),
    ]
    result = run_product_facts(
        Provider(),
        fields,
        _grounding(),
        _compact(),
        cache_dir=tmp_path / "cache",
        cache_namespace="test",
    )
    by_id = {decision.field_id: decision for decision in result.packet.decisions}

    assert by_id[field_id(fields[0])].status == READY
    assert by_id[field_id(fields[0])].citations[0].source_reference == "supplier:001:text:0001:abc"
    assert by_id[field_id(fields[1])].status == CONFLICT
    assert by_id[field_id(fields[2])].status == MISSING
    assert by_id[field_id(fields[3])].status == BUSINESS_LOCKED
    assert result.model_calls == 1
    assert result.fact_count == 2


def test_identical_product_fact_input_hits_one_cache(tmp_path):
    fields = [_field("package_length", "Length"), _field("recording_resolution", "Recording Resolution")]
    provider = Provider()
    kwargs = dict(cache_dir=tmp_path / "cache", cache_namespace="test")

    first = run_product_facts(provider, fields, _grounding(), _compact(), **kwargs)
    second = run_product_facts(provider, fields, _grounding(), _compact(), **kwargs)

    assert first.model_calls == 1
    assert second.model_calls == 0
    assert second.cache_hit is True
    assert provider.calls == 1


def test_unscoped_fov_and_packaging_scoped_product_dimension_are_not_ready():
    fields = [
        _field("exterior_field_of_view", "Exterior Field of View"),
        _field("width", "Width"),
    ]
    fields[1]["section_heading"] = "Additional Description"

    class UnsafeProvider:
        name = "unsafe-provider"

        def extract_json(self, request):
            by_key = {item["attribute_key"]: item["field_id"] for item in request["target_fields"]}
            return {
                "facts": [
                    {
                        "field_id": by_key["exterior_field_of_view"],
                        "status": "ready",
                        "values": ["120°"],
                        "qualifier": "",
                        "confidence": 1.0,
                        "citations": [{"source_reference": "s1", "evidence_text": "shooting angle 120°"}],
                        "alternatives": [],
                    },
                    {
                        "field_id": by_key["width"],
                        "status": "ready",
                        "values": ["11"],
                        "qualifier": "cm",
                        "confidence": 1.0,
                        "citations": [{"source_reference": "s1", "evidence_text": "scope=packaging; breadth=11 cm"}],
                        "alternatives": [],
                    },
                ],
                "model_summary": "",
            }

    result = run_product_facts(UnsafeProvider(), fields, _grounding(), _compact())

    assert [decision.status for decision in result.packet.decisions] == [MISSING, MISSING]


def test_packaging_height_is_not_mistaken_for_product_body_height():
    target = _field("height", "Length")
    target["section_heading"] = "Price, Stock and Shipping Information"

    class PackagingProvider:
        name = "packaging-provider"

        def extract_json(self, request):
            return {
                "facts": [{
                    "field_id": request["target_fields"][0]["field_id"],
                    "status": "ready",
                    "values": ["7"],
                    "qualifier": "cm",
                    "confidence": 1.0,
                    "citations": [{"source_reference": "s1", "evidence_text": "scope=packaging; height=7 cm"}],
                    "alternatives": [],
                }],
                "model_summary": "",
            }

    result = run_product_facts(PackagingProvider(), [target], _grounding(), _compact())

    assert result.packet.decisions[0].status == READY
    assert result.packet.decisions[0].values == ["7"]


def test_packaging_axis_is_bound_from_explicit_canonical_evidence_key():
    length = _field("length", "Length")
    breadth = _field("breadth", "Length")
    for target in (length, breadth):
        target["section_heading"] = "Price, Stock and Shipping Information"

    class SwappedProvider:
        name = "swapped-provider"

        def extract_json(self, request):
            by_key = {item["attribute_key"]: item["field_id"] for item in request["target_fields"]}
            return {
                "facts": [
                    {
                        "field_id": by_key["breadth"],
                        "status": "ready",
                        "values": ["16"],
                        "qualifier": "cm",
                        "confidence": 1.0,
                        "citations": [{"source_reference": "s1", "evidence_text": "length=16 cm"}],
                        "alternatives": [],
                    },
                    {
                        "field_id": by_key["length"],
                        "status": "ready",
                        "values": ["11"],
                        "qualifier": "cm",
                        "confidence": 1.0,
                        "citations": [{"source_reference": "s1", "evidence_text": "breadth=11 cm"}],
                        "alternatives": [],
                    },
                ],
                "model_summary": "",
            }

    result = run_product_facts(SwappedProvider(), [length, breadth], _grounding(), _compact())
    by_id = {decision.field_id: decision for decision in result.packet.decisions}

    assert by_id[field_id(length)].values == ["16"]
    assert by_id[field_id(breadth)].values == ["11"]
