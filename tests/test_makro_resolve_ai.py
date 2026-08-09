from __future__ import annotations

import inspect
import json
import sys
from types import SimpleNamespace

from openpyxl import Workbook

import makro_resolve_ai
from app.providers.registry import ProviderConfig
from app.source_snapshot import SourceSnapshot, SnapshotTableRow, write_source_snapshot


def _qa_file(tmp_path):
    path = tmp_path / "qa.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Vehicle Camera System"])
    sheet.append(["Selected variant | M8 WiFi"])
    sheet.append(["编号", "问题", "问题说明", "问题类别", "选项", "单位", "答案"])
    sheet.append([1, "Screen Size", "", "DISPLAY", "", "inch", ""])
    sheet.append([2, "Warranty Summary", "", "WARRANTY", "", "", "1 year"])
    workbook.save(path)
    return path


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
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


class FakeProvider:
    name = "fake-direct-provider"

    def __init__(self):
        self.calls = 0
        self.requests = []

    def extract_json(self, request):
        self.calls += 1
        self.requests.append(request)
        assert request["task"] == "fill_marketplace_fields_from_exact_product_evidence"
        image_source = next((source for source in request["grounded_sources"] if source["kind"] == "image"), None)
        decisions = []
        for target in request["target_fields"]:
            if target["label"] == "Screen Size":
                citation = (
                    {
                        "source_reference": image_source["source_id"],
                        "evidence_text": "visible screen size 3.0 inch",
                    }
                    if image_source is not None
                    else {
                        "source_reference": request["grounded_sources"][0]["source_id"],
                        "evidence_text": "Screen Size: 3.0 inch",
                    }
                )
                decisions.append(
                    {
                        "field_id": target["field_id"],
                        "status": "ready",
                        "values": ["3.0"],
                        "qualifier": "inch",
                        "citations": [citation],
                    }
                )
            else:
                decisions.append({"field_id": target["field_id"], "status": "missing"})
        return {"decisions": decisions}


def test_resolver_fills_directly_from_raw_sources_without_profile_stage(tmp_path, monkeypatch):
    qa = _qa_file(tmp_path)
    live_schema = _live_schema_file(tmp_path)
    image = tmp_path / "front.jpg"
    image.write_bytes(b"fake-product-image")
    output = tmp_path / "out"
    captured = {}
    provider = FakeProvider()

    def fake_builder(config):
        captured["config"] = config
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
            "--qa", str(qa),
            "--live-schema", str(live_schema),
            "--image", str(image),
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
    assert all(any(source["kind"] == "image" for source in request["grounded_sources"]) for request in provider.requests)
    assert captured["config"].structured_mode == "json_object"

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

    decisions = json.loads((run_dir / "ai-decisions.json").read_text(encoding="utf-8"))
    assert [item["status"] for item in decisions["decisions"]] == ["ready", "missing"]

    manifest = json.loads((run_dir / "run-manifest.json").read_text(encoding="utf-8"))
    assert manifest["execution_model"] == makro_resolve_ai.EXECUTION_MODEL
    assert "product_profile" not in manifest
    assert manifest["local_fill"]["batch_count"] == 2
    assert manifest["local_fill"]["model_calls"] == 2
    assert manifest["web_fill"]["searched"] is False
    assert manifest["total_model_calls"] == 2
    assert manifest["writes_performed"] == 0
    assert manifest["save_clicked"] is False
    assert manifest["send_to_qc_clicked"] is False


def test_product_url_capture_becomes_primary_supplier_text_and_image(tmp_path, monkeypatch):
    qa = _qa_file(tmp_path)
    live_schema = _live_schema_file(tmp_path)
    output = tmp_path / "out"
    capture_dir = tmp_path / "capture"
    snapshot = SourceSnapshot(
        requested_url="https://detail.1688.com/offer/850845635717.html",
        final_url="https://detail.1688.com/offer/850845635717.html",
        title="M8 dash cam",
        captured_at="2026-08-09T00:00:00Z",
        visible_text="Screen Size: 3.0 inch",
        table_rows=[SnapshotTableRow("Screen Size", "3.0 inch", 1, 1)],
    )
    snapshot_path = write_source_snapshot(snapshot, capture_dir / "source-snapshot.json")
    screenshot_path = capture_dir / "source-page.png"
    screenshot_path.write_bytes(b"fake-page-image")
    provider = FakeProvider()

    monkeypatch.setattr(makro_resolve_ai, "build_semantic_provider", lambda config: provider)
    monkeypatch.setattr(
        makro_resolve_ai,
        "capture_product_source",
        lambda *args, **kwargs: SimpleNamespace(
            snapshot_path=snapshot_path,
            screenshot_path=screenshot_path,
            snapshot=snapshot,
            launched_now=False,
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "makro_resolve_ai.py",
            "--provider", "openai-compatible",
            "--model", "vendor-vision-model",
            "--base-url", "https://api.vendor.test/v1",
            "--api-key-env", "VENDOR_KEY",
            "--qa", str(qa),
            "--live-schema", str(live_schema),
            "--product-url", "https://detail.1688.com/offer/850845635717.html",
            "--field-batch-size", "2",
            "--web-enrich", "off",
            "--no-semantic-cache",
            "--output-dir", str(output),
        ],
    )

    assert makro_resolve_ai.main() == 0
    assert provider.calls == 1
    request = provider.requests[0]
    assert request["product_identity"]["source_product_url"].endswith("850845635717.html")
    assert any(source["source_type"] == "supplier_web" for source in request["grounded_sources"])
    assert any(source["kind"] == "image" for source in request["grounded_sources"])

    run_dir = next(output.iterdir())
    manifest = json.loads((run_dir / "run-manifest.json").read_text(encoding="utf-8"))
    assert manifest["source_capture"]["requested"] is True
    assert manifest["primary_product_url"].endswith("850845635717.html")


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


def test_cli_is_simple_and_has_product_url_capture_controls():
    parser = makro_resolve_ai.build_parser()
    option_strings = {option for action in parser._actions for option in action.option_strings}
    assert "--api-key" not in option_strings
    assert "--api-key-env" in option_strings
    assert "--live-schema" in option_strings
    assert "--product-url" in option_strings
    assert "--source-cdp-port" in option_strings
    assert "--field-batch-size" in option_strings
    assert "--field-concurrency" in option_strings
    assert "--web-batch-size" in option_strings
    assert "--web-concurrency" in option_strings
    assert "--max-repair-attempts" not in option_strings
    assert "--auto-fill-min-confidence" not in option_strings


def test_cli_contains_no_legacy_or_second_semantic_resolver_path():
    source = inspect.getsource(makro_resolve_ai)
    assert "run_product_profile" not in source
    assert "resolve_catalog" not in source
    assert "run_grounded_semantic_sources" not in source
    assert "run_ai_resolution" not in source
    assert "question_matcher" not in source
    assert "final_resolve" not in source
    assert "send_to_qc_clicked" in source
