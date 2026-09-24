from __future__ import annotations

import json
from pathlib import Path

from app.ai_decisions import field_id
from gui.result_loader import load_run_result


def _write(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_result_loader_reads_current_workflow_resolver_and_fill_plan(tmp_path: Path) -> None:
    run = tmp_path / "workflow-step3"
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
    live = _write(
        run / "01-live-schema" / "live-scan-current" / "live-schema.json",
        {"schema_version": 1, "fields": [field]},
    )
    decisions = _write(
        run / "03-hot-resolver" / "resolve-ai-hot" / "ai-decisions.json",
        {
            "decisions": [
                {
                    "field_id": fid,
                    "status": "ready",
                    "values": ["Dash Camera"],
                    "qualifier": "",
                    "citations": [
                        {
                            "source_reference": "supplier:test",
                            "evidence_text": "Dash Camera",
                        }
                    ],
                    "alternatives": [],
                }
            ]
        },
    )
    cold_manifest = _write(
        run / "02-cold-resolver" / "resolve-ai-cold" / "run-manifest.json",
        {
            "primary_product_url": "https://example.test/item",
            "source_capture": {"source_cache_hit": True},
            "image_evidence": {
                "batch_count": 1,
                "model_calls": 1,
                "cache_hits": 0,
                "failed_batches": 0,
            },
            "product_facts": {
                "batch_count": 2,
                "model_calls": 2,
                "cache_hits": 0,
                "failed_batches": 0,
            },
            "web_fill": {
                "batch_count": 1,
                "model_calls": 1,
                "cache_hits": 0,
                "failed_batches": 0,
            },
            "best_effort_inference": {
                "requested_fields": 1,
                "model_calls": 1,
                "cache_hit": False,
                "failed": False,
            },
            "final_decision_summary": {
                "ready": 1,
                "review": 0,
                "conflict": 0,
                "missing": 0,
                "business_locked": 0,
            },
            "writes_performed": 0,
            "save_clicked": False,
            "send_to_qc_clicked": False,
            "outputs": {"final_decisions": str(decisions)},
        },
    )
    hot_manifest = _write(
        run / "03-hot-resolver" / "resolve-ai-hot" / "run-manifest.json",
        {
            "primary_product_url": "https://example.test/item",
            "source_capture": {"source_cache_hit": True},
            "image_evidence": {
                "batch_count": 1,
                "model_calls": 0,
                "cache_hits": 1,
                "failed_batches": 0,
            },
            "product_facts": {
                "batch_count": 2,
                "model_calls": 0,
                "cache_hits": 2,
                "failed_batches": 0,
            },
            "web_fill": {
                "batch_count": 1,
                "model_calls": 0,
                "cache_hits": 1,
                "failed_batches": 0,
            },
            "best_effort_inference": {
                "requested_fields": 1,
                "model_calls": 0,
                "cache_hit": True,
                "failed": False,
            },
            "final_decision_summary": {
                "ready": 1,
                "review": 0,
                "conflict": 0,
                "missing": 0,
                "business_locked": 0,
            },
            "writes_performed": 0,
            "save_clicked": False,
            "send_to_qc_clicked": False,
            "outputs": {"final_decisions": str(decisions)},
        },
    )
    fill_plan = _write(
        run / "04-fill-plan" / "plan-current" / "fill-plan.json",
        {
            "summary": {"ready": 1, "blocked": 0},
            "items": [
                {
                    "attribute_key": "Camera Type",
                    "label": "Camera Type",
                    "section_heading": "Product Details",
                    "action": "ready",
                    "resolution": {
                        "source_reference": "supplier:test",
                        "gate_reason": "",
                        "detail": "",
                    },
                }
            ],
        },
    )
    fill_plan_manifest = _write(
        run / "04-fill-plan" / "plan-current" / "manifest.json",
        {
            "writes_performed": 0,
            "save_clicked": False,
            "send_to_qc_clicked": False,
        },
    )
    _write(
        run / "run-manifest.json",
        {
            "mode": "step3",
            "status": "prepare_complete",
            "product_url": "https://example.test/item",
            "vertical": "Vehicle Camera System",
            "brand": "Unbranded",
            "live_schema": str(live),
            "cold_resolver_manifest": str(cold_manifest),
            "resolver_manifest": str(hot_manifest),
            "fill_plan": str(fill_plan),
            "fill_plan_manifest": str(fill_plan_manifest),
            "fill_plan_summary": {"ready": 1, "blocked": 0},
            "writes_performed": 0,
            "save_clicked": False,
            "send_to_qc_clicked": False,
        },
    )

    result = load_run_result(run)

    assert result.workflow_mode == "step3"
    assert result.workflow_status == "prepare_complete"
    assert result.vertical == "Vehicle Camera System"
    assert result.brand == "Unbranded"
    assert result.ready == 1
    assert result.blocked == 0
    assert result.missing == 0
    assert result.conflict == 0
    assert result.cold.model_calls == 4
    assert result.hot.model_calls == 0
    assert result.hot.cache_hits == 4
    assert result.hot.web_cache_hits == 1
    assert result.safety.safe is True
    assert result.fields[0].ai_result == "Dash Camera"
    assert result.fields[0].final_status == "READY"


def test_result_loader_accepts_step1_partial_result_without_fake_step3_data(tmp_path: Path) -> None:
    run = tmp_path / "workflow-step1"
    _write(
        run / "run-manifest.json",
        {
            "mode": "step1",
            "status": "step1_complete",
            "product_url": "https://example.test/item",
            "vertical": "Vehicle Camera System",
            "brand": "",
            "writes_performed": 0,
            "save_clicked": False,
            "send_to_qc_clicked": False,
        },
    )

    result = load_run_result(run)

    assert result.workflow_mode == "step1"
    assert result.vertical == "Vehicle Camera System"
    assert result.brand == ""
    assert result.live_field_count == 0
    assert result.plan_summary == {}
    assert result.ready == 0
    assert result.fields == []
    assert result.safety.safe is True
