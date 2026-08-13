from __future__ import annotations

import json
from pathlib import Path

from app.fill_plan import BLOCKED, READY, LiveFillPlan, LiveFillPlanItem
from app.live_schema import write_live_schema
from app.required_overrides import (
    apply_required_overrides,
    load_required_blocked_fields,
    required_fallback_override,
    write_required_fallback_overrides,
)
from app.resolution_types import MISSING, ResolutionRecord


ROOT = Path(__file__).resolve().parents[1]


def _field(*, help_text: str) -> dict:
    return {
        "attribute_key": "length",
        "label": "Length",
        "section_heading": "Price, Stock and Shipping Information",
        "required": True,
        "multi_value": False,
        "options": [],
        "qualifier_options": ["cm"],
        "help_text": help_text,
        "context_text": "",
    }


def _blocked_item() -> LiveFillPlanItem:
    return LiveFillPlanItem(
        attribute_key="length",
        label="Length",
        section_heading="Price, Stock and Shipping Information",
        required=True,
        action=BLOCKED,
        reason="missing required value",
        resolution=ResolutionRecord(
            attribute_key="length",
            label="Length",
            status=MISSING,
            answer=None,
            answer_values=[],
            qualifier=None,
            confidence=0.0,
            source_type=None,
            source_reference=None,
            evidence=None,
            detail="missing required value",
            eligible_for_autofill=False,
            preview_eligible=False,
            gate_reason="ai_missing",
            question_options=[],
        ),
    )


def _write_plan(path: Path, count: int) -> None:
    item = {
        "attribute_key": "length",
        "label": "Length",
        "section_heading": "Price, Stock and Shipping Information",
        "required": True,
        "action": "blocked",
        "reason": "missing required value",
        "resolution": {
            "detail": "missing required value",
            "question_options": [],
        },
    }
    path.write_text(
        json.dumps(
            {
                "summary": {"required_blocked": count},
                "items": [dict(item) for _ in range(count)],
            }
        ),
        encoding="utf-8",
    )


def test_required_blocked_file_binding_preserves_repeated_same_label_fields(tmp_path: Path) -> None:
    fields = [_field(help_text="outer package length"), _field(help_text="product length")]
    schema = write_live_schema(fields, tmp_path / "live-schema.json")
    plan = tmp_path / "fill-plan.json"
    _write_plan(plan, 2)

    blocked = load_required_blocked_fields(plan, schema)

    assert len(blocked) == 2
    assert blocked[0]["field_id"] != blocked[1]["field_id"]
    assert [item["field"]["help_text"] for item in blocked] == [
        "outer package length",
        "product length",
    ]


def test_shared_writer_creates_one_fallback_per_required_occurrence(tmp_path: Path) -> None:
    fields = [_field(help_text="outer package length"), _field(help_text="product length")]
    schema = write_live_schema(fields, tmp_path / "live-schema.json")
    plan = tmp_path / "fill-plan.json"
    _write_plan(plan, 2)

    summary = write_required_fallback_overrides(plan, schema)
    payload = json.loads((tmp_path / "required-overrides.json").read_text(encoding="utf-8"))

    assert summary["count"] == 2
    assert len(payload["overrides"]) == 2
    assert all(item["source_type"] == "fallback" for item in payload["overrides"])
    assert all(item["values"] == ["1"] for item in payload["overrides"])
    assert all(item["qualifier"] == "cm" for item in payload["overrides"])


def test_executor_override_application_does_not_collapse_repeated_plan_items() -> None:
    fields = [_field(help_text="outer package length"), _field(help_text="product length")]
    plan = LiveFillPlan(items=[_blocked_item(), _blocked_item()])
    overrides = [required_fallback_override(field) for field in fields]

    summary = apply_required_overrides(plan, fields, overrides, planned_fields=fields)

    assert summary["applied"] == 2
    assert [item.action for item in plan.items] == [READY, READY]
    assert plan.required_blocked_count == 0
    assert [item.resolution.answer_values for item in plan.items] == [["1"], ["1"]]
    assert [item.resolution.qualifier for item in plan.items] == ["cm", "cm"]


def test_batch_and_single_share_required_fallback_backend() -> None:
    batch = (ROOT / "gui" / "batch_runner.py").read_text(encoding="utf-8")
    single = (ROOT / "gui" / "required_input_support.py").read_text(encoding="utf-8")

    assert "write_required_fallback_overrides" in batch
    assert "latest_fill_plan" in batch
    assert "[required-fallback]" in batch
    assert '"makro_execute_listing.py"' in batch
    assert "load_required_blocked_fields" in single
    assert "required_fallback_override" in single
