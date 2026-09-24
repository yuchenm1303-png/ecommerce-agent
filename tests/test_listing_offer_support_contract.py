from __future__ import annotations

from pathlib import Path

import app.business_fields as business_fields
from app.listing_content_policy import LISTING_INTENT_ENV, current_listing_intent


ROOT = Path(__file__).resolve().parents[1]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_listing_offer_sources_are_python_syntax_valid() -> None:
    for relative in (
        "app/listing_content_policy.py",
        "gui/listing_offer_support.py",
        "run_local_gui.py",
    ):
        source = _source(relative)
        compile(source, str(ROOT / relative), "exec")


def test_gui_installs_offer_support_after_required_input_support() -> None:
    source = _source("run_local_gui.py")
    required = source.index("install_required_input_support(window)")
    offer = source.index("install_listing_offer_support(window)")
    assert required < offer


def test_offer_support_is_per_job_and_process_local() -> None:
    source = _source("gui/listing_offer_support.py")

    assert "ECOMMERCE_LISTING_INTENT" not in source  # shared constant only
    assert "LISTING_INTENT_ENV" in source
    assert "listing-intent.json" in source
    assert "销售规格 / 套装" in source
    assert "offer_input" in source
    assert "_with_process_intent" in source
    assert "original_spawn" in source
    # URL-keyed ownership was retired because the same supplier page may represent
    # multiple Batch rows. Job identity / sidecar ownership is the canonical model.
    assert "_listing_offer_intent_by_url" not in source
    assert "_job_intent" in source


def test_high_risk_required_fields_fail_closed_in_single_and_batch() -> None:
    source = _source("gui/listing_offer_support.py")
    policy = _source("app/listing_content_policy.py")

    assert "allow_required_fallback" in source
    assert 'job.status = "REVIEW"' in source
    assert '"required_fallback": "manual_only"' in policy
    assert "Never output N/A" in policy


def test_product_photos_reuse_existing_image_observations_for_offer_ranking() -> None:
    source = _source("gui/listing_offer_support.py")

    assert "rank_listing_images" in source
    assert 'outputs.get("image_observations")' in source
    assert 'outputs.get("primary_source_product_images")' in source
    assert "--upload-image" in source
    assert "No new vision call" in source


def test_offer_intent_does_not_replace_makro_seller_sku_or_qc_lock(monkeypatch) -> None:
    gui = _source("gui/listing_offer_support.py")
    launcher = _source("run_local_gui.py")
    product_url = "https://supplier.example/item/42?sku=blue"

    # Seller SKU generation is its own mechanical business channel. Listing intent
    # may change offer semantics, but it must never feed or replace the SKU value.
    monkeypatch.setattr(business_fields.secrets, "randbelow", lambda _limit: 123456)
    sku_without_intent = business_fields.generate_listing_sku(product_url)
    monkeypatch.setenv(LISTING_INTENT_ENV, "Black purifier + 2 fragrance oils")
    assert current_listing_intent() == "Black purifier + 2 fragrance oils"
    sku_with_intent = business_fields.generate_listing_sku(product_url)
    assert sku_with_intent == sku_without_intent
    assert len(sku_with_intent) == 12 and sku_with_intent.isdigit()

    assert "not the Makro seller SKU identifier" in gui
    assert "makro_execute_listing.py" not in gui  # reuse controller/executor instead of a second engine
    assert "Send to QC" not in gui
    assert "install_listing_offer_support" in launcher


def test_required_confirmation_panel_key_is_unique_across_account_batches() -> None:
    source = _source("gui/listing_offer_support.py")
    assert "def _batch_panel_key" in source
    assert "Path(run_dir).resolve()" in source
    assert "active_panel_keys" in source
