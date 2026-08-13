from __future__ import annotations

from pathlib import Path


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


def test_offer_support_is_per_link_and_process_local() -> None:
    source = _source("gui/listing_offer_support.py")

    assert "ECOMMERCE_LISTING_INTENT" not in source  # use the shared constant, not a duplicated literal
    assert "LISTING_INTENT_ENV" in source
    assert "listing-intent.json" in source
    assert "销售规格 / 套装" in source
    assert "offer_input" in source
    assert "_listing_offer_intent_by_url" in source
    assert "_with_process_intent" in source
    assert "original_spawn" in source


def test_high_risk_required_fields_fail_closed_in_single_and_batch() -> None:
    source = _source("gui/listing_offer_support.py")
    policy = _source("app/listing_content_policy.py")

    assert "allow_required_fallback" in source
    assert "这些关键必填字段不能使用 N/A / 1 / 随机选项" in source
    assert 'job.status = "REVIEW"' in source
    assert "需确认关键字段" in source
    assert "关键 listing 字段" in source
    assert '"required_fallback": "manual_only"' in policy
    assert "Never output N/A" in policy


def test_product_photos_reuse_existing_image_observations_for_offer_ranking() -> None:
    source = _source("gui/listing_offer_support.py")

    assert "rank_listing_images" in source
    assert 'outputs.get("image_observations")' in source
    assert 'outputs.get("primary_source_product_images")' in source
    assert "--upload-image" in source
    assert "No new vision call" in source


def test_offer_intent_does_not_replace_makro_seller_sku_or_qc_lock() -> None:
    policy = _source("app/listing_content_policy.py")
    gui = _source("gui/listing_offer_support.py")
    launcher = _source("run_local_gui.py")

    assert "not the Makro seller SKU identifier" in gui
    assert "not a product identifier" in policy
    assert "makro_execute_listing.py" not in gui  # reuse controller/executor instead of a second engine
    assert "Send to QC" not in gui
    assert "install_listing_offer_support" in launcher
