from __future__ import annotations

import inspect
import json
import sys
from types import SimpleNamespace

import makro_resolve_ai
from app.business_fields import generate_listing_sku
from app.providers.registry import ProviderConfig
from app.source_snapshot import SourceSnapshot, SnapshotTableRow, write_source_snapshot


PRODUCT_URL = "https://detail.1688.com/offer/850845635717.html"


def _live_schema_file(tmp_path):
    path = tmp_path / "live-schema.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "fields": [
                    {
                        "attribute_key": "screen_size",
                        "label": "Screen Size",
                        "section_heading": "Product Description (0/14)",
                        "required": True,
                        "multi_value": False,
                        "options": [],
                        "qualifier_options": ["inch"],
                        "help_text": "",
                        "context_text": "Screen Size inch",
                    },
                    {
                        "attribute_key": "package_length",
                        "label": "Length",
                        "section_heading": "Price, Stock and Shipping Information (0/14)",
                        "required": True,
                        "multi_value": False,
                        "options": [],
                        "qualifier_options": ["cm"],
                        "help_text": "",
                        "context_text": "Length cm",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _fake_capture(tmp_path):
    capture_dir = tmp_path / "capture"
    snapshot = SourceSnapshot(
        requested_url=PRODUCT_URL,
        final_url=PRODUCT_URL,
        title="M8 dash cam",
        captured_at="2026-08-09T00:00:00Z",
        visible_text="Screen Size: 3.0 inch",
        table_rows=[SnapshotTableRow("Screen Size", "3.0 inch", 1, 1)],
        embedded_data=['{"sku2":"m8WiFi dual front+cabin English+German manual","skuId":6017876765651}'],
        image_urls=["https://img.test/detail.jpg"],
    )
    snapshot_path = write_source_snapshot(snapshot, capture_dir / "source-snapshot.json")
    screenshot_path = capture_dir / "source-page.png"
    screenshot_path.write_bytes(b"fake-page-image")
    detail = capture_dir / "product-images" / "source-image-01.jpg"
    detail.parent.mkdir(parents=True, exist_ok=True)
    detail.write_bytes(b"fake-detail-image")
    return SimpleNamespace(
        snapshot_path=snapshot_path,
        screenshot_path=screenshot_path,
        snapshot=snapshot,
        launched_now=False,
        product_image_paths=(detail,),
        cache_hit=True,
    )


class FakeProvider:
    name = "fake-direct-provider"

    def __init__(self):
        self.calls = 0
        self.requests = []

    def extract_json(self, request):
        self.calls += 1
        self.requests.append(request)
        assert request["task"] == "fill_marketplace_fields_from_exact_product_evidence"
        image_source = next(source for source in request["grounded_sources"] if source["kind"] == "image")
        ready = []
        missing = []
        for target in request["target_fields"]:
            if target["label"] == "Screen Size":
                ready.append(
                    {
                        "field_id": target["field_id"],
                        "values": ["3.0"],
                        "qualifier": "inch",
                        "confidence": 1.0,
                        "citations": [
                            {
                                "source_reference": image_source["source_id"],
                                "evidence_text": "visible screen size 3.0 inch",
                            }
                        ],
                    }
                )
            else:
                missing.append({"field_id": target["field_id"], "search_queries": []})
        return {
            "ready": ready,
            "conflicts": [],
            "missing": missing,
            "model_summary": "fake typed local result",
        }


def test_resolver_uses_only_captured_product_url_sources(tmp_path, monkeypatch):
    live_schema = _live_schema_file(tmp_path)
    output = tmp_path / "out"
    provider = FakeProvider()
    captured_config = {}

    monkeypatch.setattr(makro_resolve_ai, "capture_product_source", lambda *a, **k: _fake_capture(tmp_path))

    def fake_builder(config):
        captured_config["config"] = config
        return provider

    monkeypatch.setattr(makro_resolve_ai, "build_semantic_provider", fake_builder)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "makro_resolve_ai.py",
            "--provider", "openai-compatible",
            "--model", "vendor-vision-model",
            "--base-url", "https://api.vendor.test/v1",
            "--api-key-env", "VENDOR_KEY",
            "--request-timeout-seconds", "75",
            "--live-schema", str(live_schema),
            "--product-url", PRODUCT_URL,
            "--field-batch-size", "1",
            "--field-concurrency", "2",
            "--web-enrich", "off",
            "--no-semantic-cache",
            "--output-dir", str(output),
        ],
    )

    assert makro_resolve_ai.main() == 0
    assert provider.calls == 2
    assert all(request["task"] == "fill_marketplace_fields_from_exact_product_evidence" for request in provider.requests)
    assert all(request["product_identity"] == {"source_product_url": PRODUCT_URL} for request in provider.requests)
    assert all(sum(source["kind"] == "image" for source in request["grounded_sources"]) == 2 for request in provider.requests)
    assert all(
        any("6017876765651" in source.get("content", "") for source in request["grounded_sources"])
        for request in provider.requests
    )
    assert captured_config["config"].structured_mode == "json_object"

    run_dir = next(output.iterdir())
    for name in (
        "ai-decisions.local.json",
        "ai-decisions.json",
        "search-requests.json",
        "web-search-sources.json",
        "web-evidence.json",
        "source-manifest.json",
        "run-manifest.json",
    ):
        assert (run_dir / name).exists(), name
    assert not (run_dir / "product-profile.json").exists()

    manifest = json.loads((run_dir / "run-manifest.json").read_text(encoding="utf-8"))
    assert manifest["execution_model"] == makro_resolve_ai.EXECUTION_MODEL
    assert "qa_source" not in manifest
    assert "customer_context_chars" not in manifest
    assert manifest["primary_product_url"] == PRODUCT_URL
    assert manifest["generated_listing_sku"] == generate_listing_sku(PRODUCT_URL)
    assert manifest["source_capture"]["embedded_data_items"] == 1
    assert manifest["source_capture"]["product_image_urls"] == 1
    assert manifest["source_capture"]["product_images_downloaded"] == 1
    assert manifest["source_capture"]["source_cache_hit"] is True
    assert manifest["local_fill"]["batch_count"] == 2
    assert manifest["local_fill"]["model_calls"] == 2
    assert manifest["web_fill"]["searched"] is False
    assert manifest["total_model_calls"] == 2
    assert manifest["writes_performed"] == 0
    assert manifest["save_clicked"] is False
    assert manifest["send_to_qc_clicked"] is False


def test_cache_namespace_ignores_transport_timeout_but_keeps_semantic_config():
    base = ProviderConfig(
        provider="openai-compatible",
        model="vision-model",
        api_key_env="KEY",
        base_url="https://api.vendor.test/v1",
        request_timeout_seconds=30,
        enable_thinking=False,
    )
    slower = ProviderConfig(
        provider="openai-compatible",
        model="vision-model",
        api_key_env="KEY",
        base_url="https://api.vendor.test/v1",
        request_timeout_seconds=180,
        enable_thinking=False,
    )
    different = ProviderConfig(
        provider="openai-compatible",
        model="vision-model",
        api_key_env="KEY",
        base_url="https://api.vendor.test/v1",
        request_timeout_seconds=30,
        enable_thinking=True,
    )
    assert makro_resolve_ai._cache_namespace(base) == makro_resolve_ai._cache_namespace(slower)
    assert makro_resolve_ai._cache_namespace(base) != makro_resolve_ai._cache_namespace(different)
