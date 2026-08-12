from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.makro.network_schema_probe import (
    MakroNetworkProbeError,
    MakroNetworkSchemaProbe,
    analyze_json_payload,
    assert_safe_makro_listing_url,
    sanitize_url,
)


ROOT = Path(__file__).resolve().parents[1]
CLI_SOURCE = (ROOT / "makro_probe_schema_network.py").read_text(encoding="utf-8")


def test_sanitize_url_redacts_secret_query_but_keeps_vertical():
    url = (
        "https://seller.makro.co.za/napi/graphql?"
        "vertical=air_purifier&csrf_token=abc123&requestId=42"
    )
    safe = sanitize_url(url)
    assert "vertical=air_purifier" in safe
    assert "requestId=42" in safe
    assert "abc123" not in safe
    assert "csrf_token=%5BREDACTED%5D" in safe


def test_schema_analysis_finds_nested_vertical_attribute_contract_without_auth_values():
    payload = {
        "data": {
            "vertical": "air_purifier",
            "attributes": [
                {
                    "attributeName": "Air Flow Level",
                    "dataType": "NUMBER",
                    "required": True,
                    "qualifiers": [{"name": "unit", "options": ["CFM"]}],
                }
            ],
        },
        "csrf_token": "do-not-persist",
        "sellerId": "seller-secret",
    }

    result = analyze_json_payload(payload)

    assert result["score"] > 0
    assert "vertical" in result["matched_keys"]
    assert any("attribute" in key for key in result["matched_keys"])
    rendered = json.dumps(result, ensure_ascii=False)
    assert "do-not-persist" not in rendered
    assert "seller-secret" not in rendered
    assert "Air Flow Level" in rendered


def test_non_schema_payload_has_zero_score():
    result = analyze_json_payload(
        {
            "status": "ok",
            "timestamp": 123,
            "message": "loaded",
        }
    )
    assert result["score"] == 0
    assert result["safe_schema_leaves"] == []


@pytest.mark.parametrize(
    "url",
    [
        "https://seller.makro.co.za/index.html#dashboard/addListings/single?vertical=air_purifier",
        "https://seller.makro.co.za/#dashboard/addListings/single?vertical=air_purifier",
    ],
)
def test_safe_listing_url_accepts_current_single_listing_routes(url):
    assert_safe_makro_listing_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/#dashboard/addListings/single",
        "https://seller.makro.co.za/#dashboard/listings",
        "javascript:alert(1)",
    ],
)
def test_safe_listing_url_rejects_unknown_navigation(url):
    with pytest.raises(MakroNetworkProbeError):
        assert_safe_makro_listing_url(url)


class _PlaywrightStyleEmitter:
    """Reproduce the sync Playwright wrapper mutation that caught the real bug."""

    def __init__(self):
        self.handler = None
        self.removed = None

    def on(self, event, handler):
        owner = getattr(handler, "__self__", None)
        if owner is not None:
            setattr(owner, f"_pw_impl_instance_on_{event}", handler)
        self.handler = handler

    def remove_listener(self, event, handler):
        self.removed = (event, handler)


def test_probe_listener_owner_accepts_playwright_runtime_wrapper_attribute():
    page = _PlaywrightStyleEmitter()
    probe = MakroNetworkSchemaProbe(page)  # type: ignore[arg-type]

    probe.start()
    assert page.handler is not None
    assert getattr(probe, "_pw_impl_instance_on_response") is page.handler

    probe.stop()
    assert page.removed == ("response", page.handler)


def test_cli_contract_never_reloads_or_clicks_original_listing():
    assert "source_page.reload" not in CLI_SOURCE
    assert "source_page.goto" not in CLI_SOURCE
    assert "source_page.click" not in CLI_SOURCE
    assert "source_page.locator" not in CLI_SOURCE
    assert "context.new_page()" in CLI_SOURCE
    assert "probe_page.goto(" in CLI_SOURCE
    assert "probe_page.close()" in CLI_SOURCE
    assert "Send to QC=False" in CLI_SOURCE
