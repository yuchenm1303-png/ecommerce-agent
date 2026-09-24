from __future__ import annotations

from pathlib import Path

from makro_probe_vertical_catalog_network import (
    STEP1_URL,
    extract_canonical_vertical,
    vertical_search_term,
)


ROOT = Path(__file__).resolve().parents[1]
CLI_SOURCE = (ROOT / "makro_probe_vertical_catalog_network.py").read_text(encoding="utf-8")


def test_extract_canonical_vertical_from_current_listing_hash_route():
    assert (
        extract_canonical_vertical(
            "https://seller.makro.co.za/index.html#dashboard/addListings/single?vertical=air_purifier&brand=Dexmary&requestId=abc"
        )
        == "air_purifier"
    )


def test_extract_canonical_vertical_decodes_url_value():
    assert (
        extract_canonical_vertical(
            "https://seller.makro.co.za/#dashboard/addListings/single?vertical=air%5Fpurifier"
        )
        == "air_purifier"
    )


def test_vertical_search_term_is_mechanical_only():
    assert vertical_search_term("air_purifier") == "air purifier"
    assert vertical_search_term("mobile_phone_case") == "mobile phone case"


def test_probe_contract_opens_new_step1_tab_and_never_selects_result():
    assert STEP1_URL == "https://seller.makro.co.za/index.html#dashboard/addListings/single"
    assert "context.new_page()" in CLI_SOURCE
    assert "probe_page.goto(" in CLI_SOURCE
    assert "search.fill(search_term)" in CLI_SOURCE
    assert "vertical_result_clicked\": False" in CLI_SOURCE
    assert "vertical_selected\": False" in CLI_SOURCE
    assert "brand_selected\": False" in CLI_SOURCE
    assert "listing_created\": False" in CLI_SOURCE
    assert "source_page.goto" not in CLI_SOURCE
    assert "source_page.reload" not in CLI_SOURCE
    assert "source_page.click" not in CLI_SOURCE
    assert ".click(" not in CLI_SOURCE
    assert "Send to QC=False" in CLI_SOURCE
