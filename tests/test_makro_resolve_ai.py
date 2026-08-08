from __future__ import annotations

import inspect
import json
import sys

from openpyxl import Workbook

import makro_resolve_ai


def _qa_file(tmp_path):
    path = tmp_path / "qa.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Vehicle Camera System"])
    sheet.append(["Questions"])
    sheet.append(["编号", "问题", "问题说明", "问题类别", "选项", "单位", "答案"])
    sheet.append([1, "Screen Size", "", "DISPLAY", "", "inch", ""])
    sheet.append([2, "Warranty Summary", "", "WARRANTY", "", "", "1 year"])
    workbook.save(path)
    return path


class FakeProvider:
    name = "fake-pluggable-provider"

    def extract_json(self, request_payload):
        image_source = next(
            source
            for source in request_payload["grounded_sources"]
            if source["kind"] == "image"
        )
        return {
            "extractor": self.name,
            "product_identity": {"sku": "", "model_number": "", "brand": ""},
            "facts": [
                {
                    "key": "Screen Size",
                    "aliases": [],
                    "value": ["3.0 inch"],
                    "source_type": "product_image",
                    "source_reference": image_source["source_id"],
                    "confidence": 0.92,
                    "evidence_text": "Visible printed specification: 3.0 inch.",
                    "note": "",
                }
            ],
            "warnings": [],
        }


def test_provider_neutral_resolver_writes_same_audit_outputs(tmp_path, monkeypatch):
    qa = _qa_file(tmp_path)
    image = tmp_path / "front.jpg"
    image.write_bytes(b"fake-product-image")
    output = tmp_path / "out"
    captured = {}

    def fake_builder(config):
        captured["config"] = config
        return FakeProvider()

    monkeypatch.setattr(makro_resolve_ai, "build_semantic_provider", fake_builder)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "makro_resolve_ai.py",
            "--provider",
            "openai-compatible",
            "--model",
            "vendor-vision-model",
            "--base-url",
            "https://api.vendor.test/v1",
            "--api-key-env",
            "VENDOR_KEY",
            "--qa",
            str(qa),
            "--image",
            str(image),
            "--output-dir",
            str(output),
            "--batch-size",
            "10",
        ],
    )

    assert makro_resolve_ai.main() == 0
    assert captured["config"].provider == "openai-compatible"
    assert captured["config"].model == "vendor-vision-model"
    assert captured["config"].api_key_env == "VENDOR_KEY"

    run_dir = next(output.iterdir())
    for name in (
        "validated-semantic-evidence.json",
        "source-manifest.json",
        "semantic-batches.json",
        "resolution.json",
        "resolution.xlsx",
        "review-queue.json",
        "review-queue.xlsx",
        "run-manifest.json",
    ):
        assert (run_dir / name).exists(), name

    manifest = json.loads((run_dir / "run-manifest.json").read_text(encoding="utf-8"))
    assert manifest["provider_config"]["provider"] == "openai-compatible"
    assert manifest["provider_config"]["api_key_env"] == "VENDOR_KEY"
    assert "api_key" not in manifest["provider_config"]
    assert manifest["makro_browser_opened"] is False
    assert manifest["writes_performed"] == 0
    assert manifest["save_clicked"] is False
    assert manifest["send_to_qc_clicked"] is False


def test_generic_cli_never_accepts_raw_api_key():
    parser = makro_resolve_ai.build_parser()
    option_strings = {
        option
        for action in parser._actions
        for option in action.option_strings
    }
    assert "--api-key" not in option_strings
    assert "--openai-api-key" not in option_strings
    assert "--api-key-env" in option_strings


def test_generic_cli_contains_no_makro_browser_or_fill_path():
    source = inspect.getsource(makro_resolve_ai)
    assert "sync_playwright" not in source
    assert "EdgeHarness" not in source
    assert "fill_resolved_field" not in source
    assert "send_to_qc_clicked" in source
