from __future__ import annotations

import json
from pathlib import Path

from app.ai_decisions import field_id
from gui.result_loader import load_run_result


def _write(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_gui_result_loader_combines_decisions_plan_cache_and_safety(tmp_path: Path) -> None:
    field = {
        "attribute_key": "Camera Type",
        "label": "Camera Type",
        "section_heading": "Product Details",
        "required": True,
        "multi_value": False,
        "options": ["Dash Camera"],
        "qualifier_options": [],
        "help_text": "",
        "context_text": "",
    }
    fid = field_id(field)
    run = tmp_path / "readonly-run"
    live = _write(
        run / "01-live-schema" / "live-scan-1" / "live-schema.json",
        {"schema_version": 1, "fields": [field]},
    )
    _write(
        live.parent / "manifest.json",
        {"writes_performed": 0, "save_clicked": False, "send_to_qc_clicked": False},
    )

    decisions = _write(
        run / "03-hot-resolver" / "resolve-ai-1" / "ai-decisions.json",
        {
            "decisions": [
                {
                    "field_id": fid,
                    "status": "ready",
                    "values": ["Dash Camera"],
                    "qualifier": "",
                    "citations": [
                        {"source_reference": "supplier:test", "evidence_text": "Dash Camera"}
                    ],
                    "alternatives": [],
                }
            ],
            "web_sources": [],
        },
    )
    hot_manifest = {
        "primary_product_url": "https://example.test/item",
        "source_capture": {"source_cache_hit": True},
        "local_fill": {
            "batch_count": 1,
            "model_calls": 0,
            "cache_hits": 1,
            "failed_batches": 0,
        },
        "web_fill": {
            "batch_count": 1,
            "model_calls": 0,
            "cache_hits": 1,
            "failed_batches": 0,
        },
        "writes_performed": 0,
        "save_clicked": False,
        "send_to_qc_clicked": False,
        "outputs": {"final_decisions": str(decisions)},
    }
    _write(run / "03-hot-resolver" / "resolve-ai-1" / "run-manifest.json", hot_manifest)
    cold_manifest = dict(hot_manifest)
    cold_manifest["source_capture"] = {"source_cache_hit": False}
    cold_manifest["local_fill"] = {
        "batch_count": 1,
        "model_calls": 1,
        "cache_hits": 0,
        "failed_batches": 0,
    }
    cold_manifest["web_fill"] = {
        "batch_count": 1,
        "model_calls": 1,
        "cache_hits": 0,
        "failed_batches": 0,
    }
    _write(run / "02-cold-resolver" / "resolve-ai-1" / "run-manifest.json", cold_manifest)

    plan_dir = run / "04-fill-plan" / "plan-1"
    _write(
        plan_dir / "fill-plan.json",
        {
            "summary": {"ready": 1, "blocked": 0},
            "items": [
                {
                    "attribute_key": "Camera Type",
                    "label": "Camera Type",
                    "section_heading": "Product Details",
                    "required": True,
                    "action": "ready",
                    "reason": "",
                    "resolution": {
                        "status": "resolved",
                        "source_reference": "supplier:test",
                        "gate_reason": "",
                        "detail": "",
                    },
                }
            ],
            "warnings": [],
        },
    )
    _write(
        plan_dir / "manifest.json",
        {"writes_performed": 0, "save_clicked": False, "send_to_qc_clicked": False},
    )

    _write(
        run / "_cache" / "semantic" / "web-product-research-test.json",
        {
            "payload": {
                "source_matches": [
                    {
                        "source_url": "https://manufacturer.test/m8",
                        "match": "same_product",
                        "reason": "matching variant anchors",
                        "identity_evidence": ["dual camera", "WiFi"],
                    }
                ]
            },
            "sources": [
                {
                    "url": "https://manufacturer.test/m8",
                    "title": "M8 product page",
                    "site_name": "manufacturer",
                }
            ],
            "request_id": "req-1",
        },
    )

    result = load_run_result(run)

    assert result.ready == 1
    assert result.missing == 0
    assert result.conflict == 0
    assert result.blocked == 0
    assert result.cold.model_calls == 1
    assert result.cold.cache_hits == 0
    assert result.hot.model_calls == 0
    assert result.hot.cache_hits == 1
    assert result.hot.source_cache_hit is True
    assert result.safety.safe is True
    assert result.fields[0].field_name == "Camera Type"
    assert result.fields[0].ai_result == "Dash Camera"
    assert result.fields[0].final_status == "READY"
    assert result.web_candidates[0].match == "same_product"
    assert result.web_candidates[0].title == "M8 product page"
