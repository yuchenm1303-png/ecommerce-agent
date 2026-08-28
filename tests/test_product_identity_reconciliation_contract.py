from __future__ import annotations

from pathlib import Path

from app.makro.listing_creation import infer_listing_bootstrap
from app.product_identity import build_product_identity_request
from app.source_snapshot import SourceSnapshot


class FakeProvider:
    name = "fake"

    def __init__(self, response):
        self.response = response
        self.requests = []

    def extract_json(self, request_payload):
        self.requests.append(request_payload)
        return dict(self.response)


def _snapshot() -> SourceSnapshot:
    return SourceSnapshot(
        requested_url="https://supplier.example/item",
        final_url="https://supplier.example/item",
        title="Personalized Wooden Polarized Protective Glasses",
        captured_at="2026-08-26T00:00:00Z",
        visible_text="Polarized protective eyewear with impact-resistant lenses and wooden-style frame.",
    )


def test_product_identity_request_classifies_core_retail_class_before_descriptive_attributes() -> None:
    request = build_product_identity_request(_snapshot())
    prompt = request["prompt_instruction"].casefold()
    rules = " ".join(request["rules"]).casefold()

    assert request["context"]["identity_contract_version"] == 3
    assert "core sold product class" in prompt
    assert "core retail class" in rules
    assert "classification anchor" in rules
    for incidental in ("material", "engraving", "personalization", "colour", "size"):
        assert incidental in rules
    assert "ultrasonic cleaner" in rules
    assert "not merely a cleaner" in rules


def test_bootstrap_preserves_customer_intent_and_cited_supplier_text_as_independent_evidence() -> None:
    provider = FakeProvider(
        {
            "entity_kind": "physical_product",
            "product_type_en": "sunglasses",
            "brand_identity": "__UNKNOWN__",
            "product_summary": "Polarized protective eyewear with a wooden-style frame.",
            "confidence": 0.82,
            "evidence_refs": ["identity:page-title"],
        }
    )

    hints = infer_listing_bootstrap(
        provider,
        _snapshot(),
        image_paths=(),
        listing_intent="1个偏光防护镜",
    )

    assert hints.product_identity["product_type_en"] == "sunglasses"
    assert hints.customer_intent == "1个偏光防护镜"
    assert hints.grounded_product_evidence
    assert "Personalized Wooden Polarized Protective Glasses" in hints.grounded_product_evidence[0]


def test_batch_prepare_consumes_existing_job_local_intent_channel_and_passes_it_into_bootstrap() -> None:
    source = (Path(__file__).resolve().parents[1] / "makro_batch_job.py").read_text(encoding="utf-8")

    assert "from app.listing_content_policy import current_listing_intent" in source
    assert "args.listing_intent or current_listing_intent()" in source
    assert "listing_intent=listing_intent" in source
    assert '"listing_intent_present": bool(listing_intent)' in source


def test_batch_gui_owns_listing_intent_per_child_process_instead_of_global_shared_state() -> None:
    root = Path(__file__).resolve().parents[1]
    support = (root / "gui" / "listing_offer_support.py").read_text(encoding="utf-8")
    policy = (root / "app" / "listing_content_policy.py").read_text(encoding="utf-8")

    # GUI consumes the shared constant instead of duplicating the environment key.
    assert "LISTING_INTENT_ENV" in support
    assert "ECOMMERCE_LISTING_INTENT" not in support
    assert "ECOMMERCE_LISTING_INTENT" in policy
    assert "current_listing_intent" in policy
    assert "_with_process_intent" in support
