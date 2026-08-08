from __future__ import annotations

import inspect
import json
import sys

from openpyxl import Workbook

import makro_resolve_ai
from app.providers.registry import ProviderConfig


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
    name = "fake-staged-provider"

    def __init__(self):
        self.calls = 0
        self.requests = []

    def extract_json(self, request):
        self.calls += 1
        self.requests.append(request)
        if request["task"] == "understand_product_from_local_evidence":
            image_source = next(source for source in request["grounded_sources"] if source["kind"] == "image")
            return {
                "facts": [
                    {
                        "name": "screen_size",
                        "scope": "product",
                        "status": "supported",
                        "candidates": [
                            {
                                "value": "3.0 inch",
                                "citations": [
                                    {
                                        "source_reference": image_source["source_id"],
                                        "evidence_text": "visible screen size 3.0 inch",
                                    }
                                ],
                            }
                        ],
                    }
                ],
                "summary": "M8 compact profile",
            }
        if request["task"] == "map_product_profile_to_marketplace_fields":
            decisions = []
            profile_payload = json.loads(request["grounded_sources"][0]["content"])
            profile_fact = profile_payload["facts"][0]
            profile_citation = profile_fact["candidates"][0]["citations"][0]
            for target in request["target_fields"]:
                if target["label"] == "Screen Size":
                    decisions.append(
                        {
                            "field_id": target["field_id"],
                            "status": "ready",
                            "values": ["3.0"],
                            "qualifier": "inch",
                            "citations": [profile_citation],
                            "profile_fact_ids": [profile_fact["fact_id"]],
                        }
                    )
                else:
                    decisions.append({"field_id": target["field_id"], "status": "missing"})
            return {"decisions": decisions}
        raise AssertionError(f"unexpected task: {request['task']}")


def test_resolver_builds_profile_once_then_maps_small_batches_without_browser(tmp_path, monkeypatch):
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
    assert provider.calls == 3  # 1 profile + 2 one-field mapping batches
    profile_request = provider.requests[0]
    mapping_requests = provider.requests[1:]
    assert profile_request["task"] == "understand_product_from_local_evidence"
    assert any(source["kind"] == "image" for source in profile_request["grounded_sources"])
    assert all(request["task"] == "map_product_profile_to_marketplace_fields" for request in mapping_requests)
    assert all(len(request["target_fields"]) == 1 for request in mapping_requests)
    assert all(
        request["grounded_sources"][0]["source_type"] == "derived_product_profile"
        for request in mapping_requests
    )
    assert captured["config"].structured_mode == "json_object"

    run_dir = next(output.iterdir())
    for name in (
        "product-profile.json",
        "ai-decisions.local.json",
        "ai-decisions.json",
        "search-requests.json",
        "web-search-sources.json",
        "web-evidence.json",
        "source-manifest.json",
        "run-manifest.json",
    ):
        assert (run_dir / name).exists(), name

    decisions = json.loads((run_dir / "ai-decisions.json").read_text(encoding="utf-8"))
    assert [item["status"] for item in decisions["decisions"]] == ["ready", "missing"]

    manifest = json.loads((run_dir / "run-manifest.json").read_text(encoding="utf-8"))
    assert manifest["execution_model"] == makro_resolve_ai.EXECUTION_MODEL
    assert manifest["product_profile"]["model_calls"] == 1
    assert manifest["field_mapping"]["batch_count"] == 2
    assert manifest["field_mapping"]["model_calls"] == 2
    assert manifest["web_enrichment"]["searched"] is False
    assert manifest["total_model_calls"] == 3
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


def test_cli_has_staged_batch_controls_and_no_whole_product_repair_switch():
    parser = makro_resolve_ai.build_parser()
    option_strings = {option for action in parser._actions for option in action.option_strings}
    assert "--api-key" not in option_strings
    assert "--api-key-env" in option_strings
    assert "--live-schema" in option_strings
    assert "--field-batch-size" in option_strings
    assert "--field-concurrency" in option_strings
    assert "--web-batch-size" in option_strings
    assert "--web-concurrency" in option_strings
    assert "--max-repair-attempts" not in option_strings
    assert "--source-concurrency" not in option_strings
    assert "--auto-fill-min-confidence" not in option_strings


def test_cli_contains_no_browser_or_legacy_local_semantic_resolver_path():
    source = inspect.getsource(makro_resolve_ai)
    assert "sync_playwright" not in source
    assert "EdgeHarness" not in source
    assert "resolve_catalog" not in source
    assert "run_grounded_semantic_sources" not in source
    assert "run_ai_resolution" not in source
    assert "question_matcher" not in source
    assert "fill_resolved_field" not in source
    assert "send_to_qc_clicked" in source
