from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from app.listing_image_ranker import finalize_supplier_listing_images


class _FakeProvider:
    name = "fake-semantic"
    model = "fake-model"

    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.calls = 0
        self.requests: list[dict[str, object]] = []

    def extract_json(self, request_payload: dict[str, object]) -> dict[str, object]:
        self.calls += 1
        self.requests.append(request_payload)
        return self.payload


def _write_image(path: Path, color: tuple[int, int, int]) -> Path:
    Image.new("RGB", (600, 600), color).save(path)
    return path.resolve()


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


def _write_manifest(run_dir: Path, images: list[Path], snapshot: Path) -> Path:
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
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return manifest


def test_multimodal_ai_owns_relevance_and_final_upload_order(tmp_path: Path) -> None:
    unrelated = _write_image(tmp_path / "01-unrelated.jpg", (230, 230, 230))
    alternate = _write_image(tmp_path / "02-alternate.jpg", (180, 180, 180))
    hero = _write_image(tmp_path / "03-hero.jpg", (250, 250, 250))

    snapshot = tmp_path / "source-snapshot.json"
    _write_snapshot(snapshot)
    manifest_path = _write_manifest(tmp_path, [unrelated, alternate, hero], snapshot)

    provider = _FakeProvider(
        {
            "selected_image_ids": ["image_03", "image_02"],
            "decisions": {
                "image_01": {
                    "selected": False,
                    "reason": "Different product and not useful for this listing.",
                },
                "image_02": {
                    "selected": True,
                    "reason": "Useful alternate view of the target product.",
                },
                "image_03": {
                    "selected": True,
                    "reason": "Strongest main image of the exact target product.",
                },
            },
            "summary": "Use the clean hero first, then the alternate view.",
        }
    )

    result = finalize_supplier_listing_images(tmp_path, provider)

    assert provider.calls == 1
    assert result.status == "ai_ranked"
    assert result.selected == (hero, alternate)

    request = provider.requests[0]
    assert request["task"] == "select_and_order_supplier_listing_images"
    grounded_sources = request["grounded_sources"]
    assert isinstance(grounded_sources, list)
    assert [item["source_id"] for item in grounded_sources] == [
        "image_01",
        "image_02",
        "image_03",
    ]
    assert all(item["kind"] == "image" for item in grounded_sources)
    assert [Path(item["image_path"]) for item in grounded_sources] == [
        unrelated,
        alternate,
        hero,
    ]

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["outputs"]["primary_source_listing_images"] == [
        str(hero),
        str(alternate),
    ]
    assert manifest["listing_image_ranking"]["selected_ids"] == ["image_03", "image_02"]
    assert manifest["listing_image_ranking"]["strategy"] == (
        "multimodal_ai_owns_semantics_duplicates_and_order"
    )
    assert manifest["total_model_calls"] == 5

    report = json.loads((tmp_path / "listing-image-selection.json").read_text(encoding="utf-8"))
    assert report["policy"]["semantic_owner"] == "multimodal_ai"
    assert report["policy"]["ordering"] == "selected_image_ids_verbatim"
    assert report["selected"] == [str(hero), str(alternate)]


def test_ranker_does_not_depend_on_upstream_image_observations(tmp_path: Path) -> None:
    image = _write_image(tmp_path / "only.jpg", (240, 240, 240))
    snapshot = tmp_path / "source-snapshot.json"
    _write_snapshot(snapshot)
    manifest_path = _write_manifest(tmp_path, [image], snapshot)

    provider = _FakeProvider(
        {
            "selected_image_ids": ["image_01"],
            "decisions": {
                "image_01": {
                    "selected": True,
                    "reason": "The image directly shows the exact target product.",
                }
            },
            "summary": "One useful product image is available.",
        }
    )

    result = finalize_supplier_listing_images(tmp_path, provider)

    assert provider.calls == 1
    assert result.selected == (image,)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert "image_observations" not in manifest["outputs"]
    assert manifest["outputs"]["primary_source_listing_images"] == [str(image)]


def test_ai_may_reject_all_candidates_when_none_are_useful(tmp_path: Path) -> None:
    image = _write_image(tmp_path / "unrelated.jpg", (100, 150, 200))
    snapshot = tmp_path / "source-snapshot.json"
    _write_snapshot(snapshot)
    _write_manifest(tmp_path, [image], snapshot)

    provider = _FakeProvider(
        {
            "selected_image_ids": [],
            "decisions": {
                "image_01": {
                    "selected": False,
                    "reason": "The image is unrelated to the exact target product.",
                }
            },
            "summary": "No candidate belongs in the listing.",
        }
    )

    result = finalize_supplier_listing_images(tmp_path, provider)

    assert result.status == "ai_ranked"
    assert result.selected == ()
    assert result.semantically_rejected_count == 1


def test_customer_auxiliary_images_never_compete_for_auto_listing_slots(tmp_path: Path) -> None:
    supplier = _write_image(tmp_path / "supplier.jpg", (245, 245, 245))
    auxiliary = _write_image(tmp_path / "auxiliary.jpg", (120, 140, 160))
    snapshot = tmp_path / "source-snapshot.json"
    _write_snapshot(snapshot)
    manifest_path = _write_manifest(tmp_path, [auxiliary, supplier], snapshot)

    product_pack = tmp_path / "product-pack.json"
    product_pack.write_text(
        json.dumps(
            {
                "evidence_images": [str(auxiliary)],
                "listing_images": [str(auxiliary)],
            }
        ),
        encoding="utf-8",
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["outputs"]["product_pack_manifest"] = str(product_pack)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    provider = _FakeProvider(
        {
            "selected_image_ids": ["image_01"],
            "decisions": {
                "image_01": {
                    "selected": True,
                    "reason": "This is the supplier product image.",
                }
            },
            "summary": "Use the supplier photo only.",
        }
    )

    result = finalize_supplier_listing_images(tmp_path, provider)

    assert result.selected == (supplier,)
    grounded_sources = provider.requests[0]["grounded_sources"]
    assert isinstance(grounded_sources, list)
    assert len(grounded_sources) == 1
    assert Path(grounded_sources[0]["image_path"]) == supplier

    report = json.loads((tmp_path / "listing-image-selection.json").read_text(encoding="utf-8"))
    assert report["transport_rejected"] == [
        {
            "source_index": 1,
            "path": str(auxiliary),
            "reason": "customer_auxiliary_not_auto_listing_candidate",
        }
    ]
