from __future__ import annotations

import inspect
from pathlib import Path

from app.makro import schema_harvest


ROOT = Path(__file__).resolve().parents[1]
CLI_SOURCE = (ROOT / "makro_harvest_schema.py").read_text(encoding="utf-8")
HARVEST_SOURCE = inspect.getsource(schema_harvest)


def test_schema_harvest_never_reuses_or_navigates_owned_listing_tab():
    assert "harness.context.new_page()" in CLI_SOURCE
    assert "page = harness.page" not in CLI_SOURCE
    assert "page = harness.ensure_page()" not in CLI_SOURCE
    assert "page.close()" in CLI_SOURCE


def test_schema_harvest_refuses_to_start_or_restart_long_lived_edge():
    ready_guard = CLI_SOURCE.index("if not is_cdp_ready(args.cdp_port):")
    harness = CLI_SOURCE.index("harness = EdgeHarness(")
    assert ready_guard < harness
    assert "schema harvest 不会自动启动/重启浏览器" in CLI_SOURCE


def test_browser_harvest_surface_is_download_only_not_listing_persistence():
    assert "expect_download" in HARVEST_SOURCE
    assert 'name="Download"' in HARVEST_SOURCE
    assert "set_input_files" not in HARVEST_SOURCE
    assert "Send to QC" not in HARVEST_SOURCE
    assert "send_to_qc" not in HARVEST_SOURCE
    assert "save_section" not in HARVEST_SOURCE


def test_navigation_labels_are_only_official_bulk_product_creation_path():
    assert schema_harvest._ALLOWED_NAVIGATION_LABELS == (
        "Listings",
        "Bulk Product Creation",
        "Create Product",
    )


def test_vertical_placeholder_cleanup_is_deterministic():
    assert schema_harvest._clean_verticals(
        ["Select Vertical", " air_purifier ", "air_purifier", "fan", ""]
    ) == ["air_purifier", "fan"]
