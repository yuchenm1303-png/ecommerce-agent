from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from app.listing_image_ranker import finalize_supplier_listing_images
from app.source_snapshot import SourceSnapshot, write_source_snapshot


class _SequenceProvider:
    name = "fixture"
    model = "fixture-model"

    def __init__(self, responses: list[dict[str, object] | BaseException]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, object]] = []

    def extract_json(self, request_payload):
        self.requests.append(request_payload)
        if not self.responses:
            raise AssertionError("unexpected AI call")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def _identity() -> dict[str, object]:
    return {
        "entity_kind": "physical_product",
        "product_type_en": "cordless drill",
        "brand_identity": "__UNKNOWN__",
        "product_summary": "18V cordless drill kit",
        "confidence": 0.95,
        "evidence_refs": ["identity:page-title"],
    }


def _blind_facts() -> dict[str, object]:
    def fact(view: str) -> dict[str, str]:
        return {
            "visual_subject": "cordless drill kit",
            "readable_identity": "18V",
            "raw_colour_materials": "grey plastic body",
            "design_configuration": "pistol-grip drill with battery",
            "neutral_product_guess": "18V cordless drill kit",
            "visual_uncertainty": "",
            "presentation_quality": view,
        }

    return {
        "facts": {"image_01": fact("hero view"), "image_02": fact("alternate angle")},
        "summary": "target-blind observations",
    }


def _ownership(classification: str = "EXACT_TARGET") -> dict[str, object]:
    def decision(index: int, confidence: float) -> dict[str, object]:
        return {
            "target_conflicts": "",
            "target_match_evidence": f"Frozen facts for image {index} establish the 18V drill kit.",
            "target_identity_gaps": "",
            "classification": classification,
            "confidence": confidence,
            "reason": f"ownership decision for image {index}",
        }

    return {
        "decisions": {"image_01": decision(1, 0.98), "image_02": decision(2, 0.97)},
        "summary": "ownership pass complete",
    }


def _decision(selected: bool, reason: str) -> dict[str, object]:
    return {"selected": selected, "reason": reason}


def _ranking(selected: list[str], *, reason: str) -> dict[str, object]:
    selected_set = set(selected)
    return {
        "selected_image_ids": selected,
        "decisions": {
            "image_01": _decision("image_01" in selected_set, reason),
            "image_02": _decision("image_02" in selected_set, reason),
        },
        "summary": reason,
    }


def _resolver_run(tmp_path: Path) -> tuple[Path, Path, Path]:
    run_dir = tmp_path / "resolve-ai-run"
    run_dir.mkdir()
    snapshot_path = write_source_snapshot(
        SourceSnapshot(
            requested_url="https://supplier.example/item/42",
            final_url="https://supplier.example/item/42",
            title="18V cordless drill kit",
            captured_at="2026-08-31T00:00:00+00:00",
            visible_text="18V cordless drill kit plus unrelated recommendation names",
        ),
        run_dir / "source-snapshot.json",
    )
    first = run_dir / "source-image-01.jpg"
    second = run_dir / "source-image-02.jpg"
    Image.new("RGB", (600, 600), (220, 220, 220)).save(first, format="JPEG")
    Image.new("RGB", (620, 620), (180, 180, 180)).save(second, format="JPEG")
    selection_path = run_dir / "listing-image-selection.json"
    manifest = {
        "input_mode": "supplier_url",
        "primary_product_url": "https://supplier.example/item/42",
        "total_model_calls": 0,
        "outputs": {
            "primary_source_snapshot": str(snapshot_path),
            "primary_source_product_images": [str(first), str(second)],
            "primary_source_listing_images": [str(first), str(second)],
            "primary_source_listing_image_selection": str(selection_path),
        },
        "source_capture": {},
    }
    (run_dir / "run-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return run_dir, first.resolve(), second.resolve()


def test_final_gallery_may_be_empty_without_a_recall_biased_rescue_pass(tmp_path: Path) -> None:
    run_dir, _first, _second = _resolver_run(tmp_path)
    provider = _SequenceProvider(
        [
            _identity(),
            _blind_facts(),
            _ownership(),
            _ranking([], reason="No candidate is safe enough for automatic upload."),
        ]
    )

    result = finalize_supplier_listing_images(run_dir, provider)

    assert result.selected == ()
    assert result.status == "ai_ranked_empty"
    assert result.model_calls == 4
    assert [request["task"] for request in provider.requests] == [
        "infer_grounded_supplier_product_identity",
        "observe_supplier_listing_images_blind",
        "compare_blind_image_facts_to_target",
        "order_identity_approved_supplier_gallery",
    ]
    assert all(request["task"] != "confirm_empty_supplier_listing_gallery" for request in provider.requests)

    manifest = json.loads((run_dir / "run-manifest.json").read_text(encoding="utf-8"))
    assert manifest["outputs"]["primary_source_listing_images"] == []
    assert manifest["listing_image_ranking"]["status"] == "ai_ranked_empty"
    assert manifest["listing_image_ranking"]["precision_first"] is True
    assert manifest["total_model_calls"] == 4


def test_semantic_ai_failure_clears_old_mechanical_gallery_before_raising(tmp_path: Path) -> None:
    run_dir, first, second = _resolver_run(tmp_path)
    provider = _SequenceProvider(
        [
            _identity(),
            RuntimeError("vision provider unavailable"),
        ]
    )

    with pytest.raises(RuntimeError, match="vision provider unavailable"):
        finalize_supplier_listing_images(run_dir, provider)

    manifest = json.loads((run_dir / "run-manifest.json").read_text(encoding="utf-8"))
    assert manifest["outputs"]["primary_source_listing_images"] == []
    assert manifest["source_capture"]["listing_images"] == []
    assert manifest["listing_image_ranking"]["status"] == "failed_closed"
    assert manifest["listing_image_ranking"]["error"]["type"] == "RuntimeError"
    assert manifest["listing_image_ranking"]["selected_count"] == 0
    assert str(first) not in manifest["outputs"]["primary_source_listing_images"]
    assert str(second) not in manifest["outputs"]["primary_source_listing_images"]

    report = json.loads((run_dir / "listing-image-selection.json").read_text(encoding="utf-8"))
    assert report["status"] == "failed_closed"
    assert report["policy"]["program_semantic_fallback"] == "none"
    assert report["error"]["message"] == "vision provider unavailable"
