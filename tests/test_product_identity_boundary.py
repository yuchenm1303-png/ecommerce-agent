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


def _identity_response(brand_identity: str) -> dict:
    return {
        "entity_kind": "physical_product",
        "product_type_en": "vacuum insulated bottle",
        "brand_identity": brand_identity,
        "product_summary": "750 ml stainless steel vacuum insulated bottle",
        "confidence": 0.98,
        "evidence_refs": ["identity:page-title", "identity:attribute:1:1"],
    }


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
    properties = request["json_contract"]["properties"]

    assert request["task"] == "infer_grounded_supplier_product_identity"
    assert request["context"]["identity_contract_version"] == 2
    assert request["grounded_sources"]
    assert "visible_text" not in str(request["context"])
    assert "physical_product" in properties["entity_kind"]["enum"]
    assert properties["brand_identity"]["minLength"] == 1
    assert "brand" not in properties
    assert "brand_status" not in properties
    assert "brand_identity" in request["json_contract"]["required"]
    assert request["json_contract"]["properties"]["evidence_refs"]["minItems"] == 1


def test_listing_bootstrap_derives_unknown_brand_from_atomic_identity():
    provider = SequenceProvider([_identity_response("__UNKNOWN__")])

    hints = infer_listing_bootstrap(provider, _snapshot())

    assert hints.vertical_search_terms == ("vacuum insulated bottle",)
    assert hints.brand == ""
    assert hints.brand_status == "unknown"
    assert hints.product_identity is not None
    assert hints.product_identity["entity_kind"] == "physical_product"
    assert len(provider.requests) == 1
    assert provider.requests[0]["task"] == "infer_grounded_supplier_product_identity"


def test_listing_bootstrap_derives_explicit_brand_from_atomic_identity():
    provider = SequenceProvider([_identity_response("Thermos")])

    hints = infer_listing_bootstrap(provider, _snapshot())

    assert hints.brand == "Thermos"
    assert hints.brand_status == "explicit"
    assert hints.product_identity["brand"] == "Thermos"
    assert hints.product_identity["brand_status"] == "explicit"


def test_listing_bootstrap_derives_unbranded_from_atomic_identity():
    provider = SequenceProvider([_identity_response("__UNBRANDED__")])

    hints = infer_listing_bootstrap(provider, _snapshot())

    assert hints.brand == ""
    assert hints.brand_status == "unbranded"
