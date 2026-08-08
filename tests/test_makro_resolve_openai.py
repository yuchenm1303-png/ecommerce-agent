from __future__ import annotations

import inspect
import json
import sys

from openpyxl import Workbook

import makro_resolve_openai


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
    name = "fake-grounded-provider"

    def __init__(self, *, model, image_detail, max_output_tokens):
        self.model = model
        self.image_detail = image_detail
        self.max_output_tokens = max_output_tokens

    def extract_json(self, request_payload):
        image_source = next(
            source
            for source in request_payload["grounded_sources"]
            if source["kind"] == "image"
        )
        return {
            "extractor": "ignored-model-name",
            "product_identity": {"sku": "", "model_number": "", "brand": ""},
            "facts": [
                {
                    "key": "Screen Size",
                    "aliases": [],
                    "value": ["3.0 inch"],
                    "source_type": "product_image",
                    "source_reference": image_source["source_id"],
                    "confidence": 0.92,
                    # This CLI smoke test is about report generation, not semantic
                    # quarantine. Make the fake visual evidence explicitly bind
                    # the value to the QA attribute so it remains a direct fact.
                    "evidence_text": "Visible label reads Screen Size: 3.0 inch.",
                    "note": "",
                }
            ],
            "warnings": [],
        }


def test_one_shot_openai_resolver_is_browser_free_and_writes_audit_reports(tmp_path, monkeypatch):
    qa = _qa_file(tmp_path)
    image = tmp_path / "front.jpg"
    image.write_bytes(b"fake-product-image")
    output = tmp_path / "out"

    monkeypatch.setattr(makro_resolve_openai, "OpenAISemanticProvider", FakeProvider)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "makro_resolve_openai.py",
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

    assert makro_resolve_openai.main() == 0

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

    resolution = json.loads((run_dir / "resolution.json").read_text(encoding="utf-8"))
    assert resolution["summary"]["total"] == 2
    assert resolution["summary"]["resolved"] == 2
    assert resolution["summary"]["eligible_for_autofill"] == 2

    review = json.loads((run_dir / "review-queue.json").read_text(encoding="utf-8"))
    assert review["summary"]["total"] == 0

    manifest = json.loads((run_dir / "run-manifest.json").read_text(encoding="utf-8"))
    assert manifest["makro_browser_opened"] is False
    assert manifest["writes_performed"] == 0
    assert manifest["save_clicked"] is False
    assert manifest["send_to_qc_clicked"] is False


def test_cli_does_not_accept_api_key_argument():
    parser = makro_resolve_openai.build_parser()
    option_strings = {
        option
        for action in parser._actions
        for option in action.option_strings
    }
    assert "--api-key" not in option_strings
    assert "--openai-api-key" not in option_strings


def test_cli_contains_no_makro_browser_or_fill_path():
    source = inspect.getsource(makro_resolve_openai)
    assert "sync_playwright" not in source
    assert "EdgeHarness" not in source
    assert "fill_resolved_field" not in source
    assert "Send to QC" in source  # audit output only
    assert "save_clicked" in source  # manifest proves it remained false
