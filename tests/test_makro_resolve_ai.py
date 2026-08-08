from __future__ import annotations

import inspect
import json
import sys

from openpyxl import Workbook

import makro_resolve_ai
from app.providers.registry import ProviderConfig, validate_provider_config


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
    name = "fake-pluggable-provider"

    def __init__(self):
        self.calls = 0
        self.requests = []

    def extract_json(self, request_payload):
        self.calls += 1
        self.requests.append(request_payload)
        image_source = next(
            source for source in request_payload["grounded_sources"] if source["kind"] == "image"
        )
        decisions = []
        for target in request_payload["target_fields"]:
            if target["label"] == "Screen Size":
                decisions.append(
                    {
                        "field_id": target["field_id"],
                        "status": "ready",
                        "values": ["3.0"],
                        "qualifier": "inch",
                        "citations": [
                            {
                                "source_reference": image_source["source_id"],
                                "evidence_text": "Visible printed specification: Screen Size 3.0 inch.",
                            }
                        ],
                    }
                )
            else:
                decisions.append(
                    {
                        "field_id": target["field_id"],
                        "status": "missing",
                        "search_queries": ["M8 WiFi package dimensions"],
                    }
                )
        return {"decisions": decisions, "model_summary": "resolved live schema"}


def test_resolver_is_one_local_product_call_browser_free_and_audited(tmp_path, monkeypatch):
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
            "--no-semantic-cache",
            "--output-dir", str(output),
        ],
    )

    assert makro_resolve_ai.main() == 0
    assert provider.calls == 1
    request = provider.requests[0]
    assert request["task"] == "fill_marketplace_fields_from_local_product_evidence"
    assert len(request["target_fields"]) == 2
    assert len(request["grounded_sources"]) == 2
    assert "schema_sha256" not in request
    assert "source_manifest_sha256" not in request
    assert captured["config"].provider == "openai-compatible"
    assert captured["config"].model == "vendor-vision-model"
    assert captured["config"].api_key_env == "VENDOR_KEY"
    assert captured["config"].request_timeout_seconds == 75
    assert captured["config"].structured_mode == "prompt_only"

    run_dir = next(output.iterdir())
    for name in (
        "ai-decisions.json",
        "search-requests.json",
        "web-search-sources.json",
        "source-manifest.json",
        "run-manifest.json",
    ):
        assert (run_dir / name).exists(), name

    decisions = json.loads((run_dir / "ai-decisions.json").read_text(encoding="utf-8"))
    assert decisions["contract_version"] == 2
    assert len(decisions["decisions"]) == 2
    assert [item["status"] for item in decisions["decisions"]] == ["ready", "missing"]
    assert decisions["web_sources"] == []

    search = json.loads((run_dir / "search-requests.json").read_text(encoding="utf-8"))
    assert len(search) == 1
    assert search[0]["label"] == "Length"

    manifest = json.loads((run_dir / "run-manifest.json").read_text(encoding="utf-8"))
    assert manifest["mode"] == "browser_free_ai_first_product_resolution"
    assert manifest["execution_model"] == "one_local_whole_product_fill_plus_optional_one_sourced_web_fill"
    assert manifest["provider_config"]["provider"] == "openai-compatible"
    assert manifest["provider_config"]["request_timeout_seconds"] == 75
    assert "api_key" not in manifest["provider_config"]
    assert manifest["customer_context_chars"] > 0
    assert manifest["live_field_count"] == 2
    assert manifest["local_ai"]["model_calls"] == 1
    assert manifest["local_ai"]["repair_attempts"] == 0
    assert manifest["local_ai"]["decision_summary"]["ready"] == 1
    assert manifest["local_ai"]["decision_summary"]["missing"] == 1
    assert manifest["web_enrichment"]["searched"] is False
    assert manifest["total_model_calls"] == 1
    assert manifest["writes_performed"] == 0
    assert manifest["save_clicked"] is False
    assert manifest["send_to_qc_clicked"] is False


def test_qwen_auto_mode_selects_json_output_and_no_thinking():
    config = validate_provider_config(
        ProviderConfig(
            provider="openai-compatible",
            model="qwen3.5-omni-plus",
            api_key_env="DASHSCOPE_API_KEY",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
    )
    assert config.structured_mode == "json_object"
    assert config.enable_thinking is False
    assert config.as_safe_dict()["max_output_tokens"] is None


def test_cache_namespace_ignores_transport_only_timeout_but_keeps_semantic_config():
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
    different_semantics = ProviderConfig(
        provider="openai-compatible",
        model="vision-model",
        api_key_env="KEY",
        base_url="https://api.vendor.test/v1",
        request_timeout_seconds=30,
        enable_thinking=True,
    )
    assert makro_resolve_ai._cache_namespace(base) == makro_resolve_ai._cache_namespace(slower)
    assert makro_resolve_ai._cache_namespace(base) != makro_resolve_ai._cache_namespace(different_semantics)


def test_cli_defaults_to_auto_json_and_zero_full_product_repair():
    parser = makro_resolve_ai.build_parser()
    structured = next(item for item in parser._actions if "--structured-mode" in item.option_strings)
    repair = next(item for item in parser._actions if "--max-repair-attempts" in item.option_strings)
    assert structured.default == "auto"
    assert repair.default == 0


def test_cli_has_no_legacy_batch_confidence_or_semantic_fact_controls():
    parser = makro_resolve_ai.build_parser()
    option_strings = {option for action in parser._actions for option in action.option_strings}
    assert "--api-key" not in option_strings
    assert "--api-key-env" in option_strings
    assert "--live-schema" in option_strings
    assert "--batch-size" not in option_strings
    assert "--source-concurrency" not in option_strings
    assert "--auto-fill-min-confidence" not in option_strings
    assert "--max-repair-attempts" in option_strings
    assert "--request-timeout-seconds" in option_strings
    assert "--web-enrich" in option_strings
    live_action = next(item for item in parser._actions if "--live-schema" in item.option_strings)
    assert live_action.required is True


def test_cli_contains_no_makro_browser_or_local_semantic_resolver_path():
    source = inspect.getsource(makro_resolve_ai)
    assert "sync_playwright" not in source
    assert "EdgeHarness" not in source
    assert "resolve_catalog" not in source
    assert "run_grounded_semantic_sources" not in source
    assert "question_matcher" not in source
    assert "fill_resolved_field" not in source
    assert "send_to_qc_clicked" in source
