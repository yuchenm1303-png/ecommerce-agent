from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from app.listing_image_pipeline import run_supplier_listing_image_pipeline


class _Provider:
    name = "fake-semantic"
    model = "fake-model"

    def __init__(self, responses: dict[str, dict[str, object]] | None = None) -> None:
        self.responses = responses or {}
        self.calls = 0
        self.requests: list[dict[str, object]] = []

    def extract_json(self, request_payload: dict[str, object]) -> dict[str, object]:
        self.calls += 1
        self.requests.append(request_payload)
        task = str(request_payload.get("task") or "")
        if task not in self.responses:
            raise AssertionError(f"unexpected uncached AI task: {task}")
        return self.responses[task]


def _image(path: Path, size: tuple[int, int], colour: tuple[int, int, int]) -> Path:
    Image.new("RGB", size, colour).save(path)
    return path.resolve()


def _snapshot(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "requested_url": "https://supplier.example/item/1",
                "final_url": "https://supplier.example/item/1",
                "title": "Acme Garden Fertilizer 20 kg",
                "captured_at": "2026-08-31T00:00:00Z",
                "visible_text": "Acme Garden Fertilizer 20 kg",
                "table_rows": [
                    {"key": "Brand", "value": "Acme", "table_index": 0, "row_index": 0},
                    {"key": "Quantity", "value": "20 kg", "table_index": 0, "row_index": 1},
                ],
                "json_ld": [],
                "embedded_data": [],
                "image_urls": [],
                "meta": {},
                "warnings": [],
            }
        ),
        encoding="utf-8",
    )
    return path.resolve()


def _manifest(run_dir: Path, snapshot: Path, images: list[Path]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run-manifest.json").write_text(
        json.dumps(
            {
                "input_mode": "supplier_url",
                "total_model_calls": 5,
                "source_capture": {},
                "outputs": {
                    "primary_source_snapshot": str(snapshot),
                    "primary_source_product_images": [str(path) for path in images],
                    "primary_source_listing_images": [str(path) for path in images],
                    "primary_source_listing_image_selection": str(
                        (run_dir / "listing-image-selection.json").resolve()
                    ),
                },
            }
        ),
        encoding="utf-8",
    )


def _responses() -> dict[str, dict[str, object]]:
    return {
        "infer_grounded_supplier_product_identity": {
            "entity_kind": "physical_product",
            "product_type_en": "garden fertilizer",
            "brand_identity": "Acme",
            "product_summary": "Acme Garden Fertilizer 20 kg",
            "confidence": 0.99,
            "evidence_refs": ["identity:page-title"],
        },
        "classify_supplier_listing_image_ownership": {
            "decisions": {
                "image_01": {
                    "visual_subject": "bag of garden fertilizer",
                    "visible_identity": "Acme",
                    "visible_configuration": "20 kg fertilizer bag",
                    "target_match_evidence": "Acme fertilizer bag and 20 kg configuration",
                    "target_identity_gaps": "",
                    "classification": "EXACT_TARGET",
                    "confidence": 0.99,
                    "reason": "The pixels establish the exact target sale unit.",
                }
            },
            "summary": "One exact target image.",
        },
        "verify_and_order_exact_supplier_gallery": {
            "selected_image_ids": ["image_01"],
            "decisions": {
                "image_01": {
                    "selected": True,
                    "reason": "Use the exact target product image.",
                }
            },
            "summary": "One suitable product photo.",
        },
    }


def test_undersized_supplier_image_is_removed_before_semantic_ranking(tmp_path: Path) -> None:
    run_dir = tmp_path / "cold"
    low = _image(tmp_path / "thumb.jpg", (80, 128), (20, 30, 40))
    good = _image(tmp_path / "full.jpg", (600, 600), (40, 50, 60))
    snapshot = _snapshot(tmp_path / "snapshot.json")
    _manifest(run_dir, snapshot, [low, good])
    provider = _Provider(_responses())

    result = run_supplier_listing_image_pipeline(
        run_dir,
        provider,
        cache_dir=tmp_path / "semantic-cache",
    )

    assert result.selected == (good,)
    assert result.mechanically_rejected_count == 1
    assert result.model_calls == 3
    assert provider.calls == 3
    ownership_sources = provider.requests[1]["grounded_sources"]
    assert [Path(item["image_path"]) for item in ownership_sources] == [good]

    report = json.loads((run_dir / "listing-image-selection.json").read_text(encoding="utf-8"))
    rejected = report["marketplace_mechanical_rejected"]
    assert rejected[0]["reason"] == "below_marketplace_minimum_resolution"
    assert rejected[0]["width"] == 80
    assert rejected[0]["height"] == 128


def test_hot_run_reuses_exact_cold_image_semantics_across_different_paths(tmp_path: Path) -> None:
    cache_dir = tmp_path / "semantic-cache"
    cold = tmp_path / "cold"
    hot = tmp_path / "hot"
    cold_image = _image(cold / "product.jpg", (600, 600), (70, 80, 90))
    hot_image = _image(hot / "copied-product.jpg", (600, 600), (70, 80, 90))
    cold_snapshot = _snapshot(cold / "snapshot.json")
    hot_snapshot = _snapshot(hot / "snapshot.json")
    _manifest(cold, cold_snapshot, [cold_image])
    _manifest(hot, hot_snapshot, [hot_image])

    cold_provider = _Provider(_responses())
    cold_result = run_supplier_listing_image_pipeline(
        cold,
        cold_provider,
        cache_dir=cache_dir,
    )
    assert cold_result.model_calls == 3

    hot_provider = _Provider()
    hot_result = run_supplier_listing_image_pipeline(
        hot,
        hot_provider,
        cache_dir=cache_dir,
    )

    assert hot_provider.calls == 0
    assert hot_result.model_calls == 0
    assert hot_result.cache_hits == 3
    assert hot_result.request_count == 3
    assert hot_result.selected == (hot_image,)
    manifest = json.loads((hot / "run-manifest.json").read_text(encoding="utf-8"))
    assert manifest["listing_image_ranking"]["model_calls"] == 0
    assert manifest["listing_image_ranking"]["cache_hits"] == 3
    assert manifest["total_model_calls"] == 5
