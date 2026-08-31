from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from app.listing_image_ranker import finalize_supplier_listing_images
from app.source_snapshot import SourceSnapshot, write_source_snapshot


class _SequenceProvider:
    name = "fixture"
    model = "fixture-model"

    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, object]] = []

    def extract_json(self, request_payload):
        self.requests.append(request_payload)
        if not self.responses:
            raise AssertionError("unexpected ranking call")
        return self.responses.pop(0)


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
            title="Exact target product",
            captured_at="2026-08-31T00:00:00+00:00",
            visible_text="Exact target product listing",
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
    return run_dir, first, second


def test_first_empty_result_is_rechecked_before_supplier_gallery_is_removed(tmp_path) -> None:
    run_dir, first, _second = _resolver_run(tmp_path)
    provider = _SequenceProvider(
        [
            _ranking([], reason="first pass was overly strict"),
            _ranking(["image_01"], reason="reinspection confirms exact product"),
        ]
    )

    result = finalize_supplier_listing_images(run_dir, provider)

    assert result.selected == (first.resolve(),)
    assert result.model_calls == 2
    assert len(provider.requests) == 2
    assert provider.requests[1]["task"] == "confirm_empty_supplier_listing_gallery"
    manifest = json.loads((run_dir / "run-manifest.json").read_text(encoding="utf-8"))
    assert manifest["outputs"]["primary_source_listing_images"] == [str(first.resolve())]
    assert manifest["listing_image_ranking"]["zero_selection_confirmation_performed"] is True
    assert manifest["listing_image_ranking"]["zero_selection_confirmed"] is False
    assert manifest["total_model_calls"] == 2


def test_empty_supplier_gallery_requires_two_matching_zero_decisions(tmp_path) -> None:
    run_dir, _first, _second = _resolver_run(tmp_path)
    provider = _SequenceProvider(
        [
            _ranking([], reason="no exact product visible"),
            _ranking([], reason="second inspection also finds no exact product"),
        ]
    )

    result = finalize_supplier_listing_images(run_dir, provider)

    assert result.selected == ()
    assert result.model_calls == 2
    manifest = json.loads((run_dir / "run-manifest.json").read_text(encoding="utf-8"))
    assert manifest["outputs"]["primary_source_listing_images"] == []
    assert manifest["listing_image_ranking"]["zero_selection_confirmation_performed"] is True
    assert manifest["listing_image_ranking"]["zero_selection_confirmed"] is True
