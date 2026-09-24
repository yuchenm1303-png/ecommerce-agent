from __future__ import annotations

import json
from pathlib import Path

from gui.result_loader import load_run_result


def _write(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_degraded_hot_replay_keeps_cold_business_result_without_fake_hot_stats(tmp_path: Path) -> None:
    cold_manifest = _write(
        tmp_path / "02-cold-resolver" / "resolve-ai-cold" / "run-manifest.json",
        {
            "primary_product_url": "https://supplier.example/item/1",
            "image_evidence": {"batch_count": 2, "model_calls": 2},
            "product_facts": {"batch_count": 3, "model_calls": 3},
            "final_decision_summary": {"missing": 1, "conflict": 0},
            "outputs": {},
        },
    )
    _write(
        tmp_path / "run-manifest.json",
        {
            "mode": "full",
            "status": "prepare_complete",
            "product_url": "https://supplier.example/item/1",
            "cold_resolver_manifest": str(cold_manifest),
            "resolver_manifest": str(cold_manifest),
            "resolver_replay": {
                "status": "degraded",
                "canonical_resolver": "cold",
                "error_type": "RuntimeError",
            },
        },
    )

    result = load_run_result(tmp_path)

    assert result.cold.batch_count == 5
    assert result.cold.model_calls == 5
    assert result.hot.batch_count == 0
    assert result.hot.model_calls == 0
    assert result.resolver["primary_product_url"] == "https://supplier.example/item/1"
    assert result.missing == 1
