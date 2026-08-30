from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PIL import Image

from app.listing_image_ranker import finalize_supplier_listing_images


class _FakeProvider:
    name = "fake-semantic"
    model = "fake-model"

    def __init__(self, decisions: dict[str, dict[str, object]]) -> None:
        self.decisions = decisions
        self.calls = 0

    def extract_json(self, request_payload: dict[str, object]) -> dict[str, object]:
        self.calls += 1
        assert request_payload["task"] == "rank_supplier_listing_images"
        return {"decisions": self.decisions, "summary": "ok"}


def _write_image(path: Path, color: tuple[int, int, int]) -> Path:
    Image.new("RGB", (600, 600), color).save(path)
    return path.resolve()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_snapshot(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "requested_url": "https://supplier.example/item/1",
                "final_url": "https://supplier.example/item/1",
                "title": "Acme Cordless Drill 18V Black",
                "captured_at": "2026-08-30T00:00:00Z",
                "visible_text": "Acme Cordless Drill 18V Black with battery and charger",
                "table_rows": [
                    {"key": "Brand", "value": "Acme", "table_index": 0, "row_index": 0},
                    {"key": "Voltage", "value": "18V", "table_index": 0, "row_index": 1},
                ],
                "json_ld": [],
                "embedded_data": [],
                "image_urls": [],
                "meta": {},
                "warnings": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _write_manifest(
    run_dir: Path,
    *,
    images: list[Path],
    observations: Path,
    snapshot: Path,
) -> Path:
    selection = run_dir / "listing-image-selection.json"
    manifest = run_dir / "run-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "input_mode": "supplier_url",
                "primary_product_url": "https://supplier.example/item/1",
                "total_model_calls": 4,
                "source_capture": {
                    "listing_images_selected": len(images),
                    "listing_images": [str(path) for path in images],
                },
                "outputs": {
                    "primary_source_snapshot": str(snapshot),
                    "primary_source_product_images": [str(path) for path in images],
                    "primary_source_listing_images": [str(path) for path in images],
                    "primary_source_listing_image_selection": str(selection),
                    "image_observations": str(observations),
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return manifest


def test_semantic_ranker_rejects_unrelated_first_image_and_promotes_hero(tmp_path: Path) -> None:
    unrelated = _write_image(tmp_path / "01-unrelated.jpg", (230, 230, 230))
    hero = _write_image(tmp_path / "02-hero.jpg", (250, 250, 250))
    detail = _write_image(tmp_path / "03-detail.jpg", (180, 180, 180))

    observations = tmp_path / "image-observations.json"
    observations.write_text(
        json.dumps(
            [
                {
                    "image_id": "source-image:1",
                    "origin": str(unrelated),
                    "sha256": _sha256(unrelated),
                    "visible_text": "Kitchen Blender 900W",
                    "facts": [
                        {
                            "name": "product type",
                            "scope": "product_body",
                            "value": "blender",
                            "qualifier": "",
                            "evidence_text": "The image shows a countertop blender.",
                        }
                    ],
                    "notes": "A different kitchen appliance product.",
                },
                {
                    "image_id": "source-image:2",
                    "origin": str(hero),
                    "sha256": _sha256(hero),
                    "visible_text": "Acme 18V",
                    "facts": [
                        {
                            "name": "product type",
                            "scope": "product_body",
                            "value": "cordless drill",
                            "qualifier": "",
                            "evidence_text": "Full cordless drill is visible on a clean background.",
                        }
                    ],
                    "notes": "Whole Acme drill, centered and unobstructed.",
                },
                {
                    "image_id": "source-image:3",
                    "origin": str(detail),
                    "sha256": _sha256(detail),
                    "visible_text": "18V",
                    "facts": [
                        {
                            "name": "voltage",
                            "scope": "product_body",
                            "value": "18V",
                            "qualifier": "",
                            "evidence_text": "Close-up label on the same drill body.",
                        }
                    ],
                    "notes": "Useful close-up of the target drill.",
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    snapshot = tmp_path / "source-snapshot.json"
    _write_snapshot(snapshot)
    manifest_path = _write_manifest(
        tmp_path,
        images=[unrelated, hero, detail],
        observations=observations,
        snapshot=snapshot,
    )

    provider = _FakeProvider(
        {
            "image_01": {
                "relevant": False,
                "role": "unrelated",
                "main_image_score": 0,
                "gallery_score": 0,
                "reason": "Different product category from the target cordless drill.",
            },
            "image_02": {
                "relevant": True,
                "role": "hero",
                "main_image_score": 98,
                "gallery_score": 96,
                "reason": "Exact target product, full view, clean composition.",
            },
            "image_03": {
                "relevant": True,
                "role": "detail",
                "main_image_score": 25,
                "gallery_score": 88,
                "reason": "Relevant close-up that belongs after the full-product image.",
            },
        }
    )

    result = finalize_supplier_listing_images(tmp_path, provider)

    assert provider.calls == 1
    assert result.status == "ranked"
    assert result.selected == (hero, detail)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["outputs"]["primary_source_listing_images"] == [str(hero), str(detail)]
    assert manifest["source_capture"]["listing_images"] == [str(hero), str(detail)]
    assert manifest["listing_image_ranking"]["semantic_rejected_count"] == 1
    assert manifest["total_model_calls"] == 5

    report = json.loads((tmp_path / "listing-image-selection.json").read_text(encoding="utf-8"))
    assert report["selected"] == [str(hero), str(detail)]
    assert report["semantic_ranking"]["selected_ids"] == ["image_02", "image_03"]


def test_ranker_fails_closed_when_upstream_ai_never_observed_images(tmp_path: Path) -> None:
    image = _write_image(tmp_path / "only.jpg", (240, 240, 240))
    observations = tmp_path / "image-observations.json"
    observations.write_text("[]", encoding="utf-8")
    snapshot = tmp_path / "source-snapshot.json"
    _write_snapshot(snapshot)
    manifest_path = _write_manifest(
        tmp_path,
        images=[image],
        observations=observations,
        snapshot=snapshot,
    )
    provider = _FakeProvider({})

    result = finalize_supplier_listing_images(tmp_path, provider)

    assert provider.calls == 0
    assert result.status == "no_ai_observations"
    assert result.selected == ()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["outputs"]["primary_source_listing_images"] == []
    assert manifest["listing_image_ranking"]["status"] == "no_ai_observations"
