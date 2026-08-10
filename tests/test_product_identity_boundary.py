from __future__ import annotations

from app.makro.listing_creation import infer_listing_bootstrap
from app.product_identity import build_product_identity_request, build_product_identity_sources
from app.source_snapshot import SnapshotTableRow, SourceSnapshot


class SequenceProvider:
    name = "fake"

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def extract_json(self, request_payload):
        self.requests.append(request_payload)
        return self.responses.pop(0)


def _snapshot() -> SourceSnapshot:
    return SourceSnapshot(
        requested_url="https://detail.1688.com/offer/1.html",
        final_url="https://detail.1688.com/offer/1.html",
        title="316 stainless steel vacuum insulated bottle 750ml",
        captured_at="2026-08-10T00:00:00Z",
        visible_text=(
            "B2B e-commerce platform wholesale procurement marketplace seller services "
            * 1000
        ),
        table_rows=[
            SnapshotTableRow("材质", "316不锈钢", 1, 1),
            SnapshotTableRow("容量", "750ml", 1, 2),
        ],
        meta={"og:title": "316不锈钢真空保温杯 750ml"},
    )


def test_product_identity_sources_exclude_generic_visible_page_body():
    snapshot = _snapshot()
    sources = build_product_identity_sources(snapshot)
    serialized = "\n".join(str(item.get("content") or "") for item in sources)

    assert "B2B e-commerce platform" not in serialized
    assert "316 stainless steel vacuum insulated bottle" in serialized
    assert "316不锈钢" in serialized
    assert any(item["source_id"] == "identity:meta:og:title" for item in sources)


def test_product_identity_request_is_physical_product_grounded_contract():
    request = build_product_identity_request(_snapshot())

    assert request["task"] == "infer_grounded_supplier_product_identity"
    assert request["grounded_sources"]
    assert "visible_text" not in str(request["context"])
    assert "physical_product" in request["json_contract"]["properties"]["entity_kind"]["enum"]
    assert "evidence_refs" in request["json_contract"]["required"]


def test_listing_bootstrap_uses_identity_then_identity_only_search_terms():
    provider = SequenceProvider(
        [
            {
                "entity_kind": "physical_product",
                "product_type_en": "vacuum insulated bottle",
                "brand": "",
                "brand_status": "unknown",
                "product_summary": "750 ml stainless steel vacuum insulated bottle",
                "confidence": 0.98,
                "evidence_refs": ["identity:page-title", "identity:attribute:1:1"],
            },
            {
                "vertical_search_terms": [
                    "insulated water bottle",
                    "vacuum flask",
                ]
            },
        ]
    )

    hints = infer_listing_bootstrap(provider, _snapshot())

    assert hints.vertical_search_terms == (
        "vacuum insulated bottle",
        "insulated water bottle",
        "vacuum flask",
    )
    assert hints.product_identity is not None
    assert hints.product_identity["entity_kind"] == "physical_product"
    assert len(provider.requests) == 2
    assert provider.requests[0]["task"] == "infer_grounded_supplier_product_identity"
    assert provider.requests[1]["task"] == "derive_product_type_search_terms"
    assert "supplier_evidence" not in provider.requests[1].get("context", {})
    assert provider.requests[1]["context"]["product_identity"]["product_type_en"] == "vacuum insulated bottle"
