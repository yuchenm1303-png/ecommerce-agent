from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from app.listing_image_ranker import finalize_supplier_listing_images


class _FakeProvider:
    name = "fake-semantic"
    model = "fake-model"

    def __init__(self, responses: dict[str, dict[str, object]]) -> None:
        self.responses = responses
        self.calls = 0
        self.requests: list[dict[str, object]] = []

    def extract_json(self, request_payload: dict[str, object]) -> dict[str, object]:
        self.calls += 1
        self.requests.append(request_payload)
        task = str(request_payload.get("task") or "")
        if task not in self.responses:
            raise AssertionError(f"unexpected AI task: {task}")
        return self.responses[task]


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
                "visible_text": (
                    "Acme Cordless Drill 18V Black with battery and charger. "
                    "Customers also viewed Corn Flakes Full Cream Milk Vaseline Lotion."
                ),
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


def _identity_response() -> dict[str, object]:
    return {
        "entity_kind": "physical_product",
        "product_type_en": "cordless drill",
        "brand_identity": "Acme",
        "product_summary": "Acme 18V black cordless drill with battery and charger",
        "confidence": 0.98,
        "evidence_refs": ["identity:page-title", "identity:attribute:0:0"],
    }


def test_ai_ownership_filters_unrelated_page_products_before_gallery_ranking(tmp_path: Path) -> None:
    unrelated = _write_image(tmp_path / "01-corn-flakes.jpg", (230, 230, 230))
    alternate = _write_image(tmp_path / "02-drill-side.jpg", (180, 180, 180))
    hero = _write_image(tmp_path / "03-drill-hero.jpg", (250, 250, 250))

    snapshot = tmp_path / "source-snapshot.json"
    _write_snapshot(snapshot)
    manifest_path = _write_manifest(tmp_path, [unrelated, alternate, hero], snapshot)

    provider = _FakeProvider(
        {
            "infer_grounded_supplier_product_identity": _identity_response(),
            "classify_supplier_listing_image_ownership": {
                "decisions": {
                    "image_01": {
                        "classification": "OTHER_PRODUCT",
                        "confidence": 0.99,
                        "reason": "Corn flakes are a different sellable product.",
                    },
                    "image_02": {
                        "classification": "EXACT_TARGET",
                        "confidence": 0.97,
                        "reason": "The image depicts the exact black cordless drill.",
                    },
                    "image_03": {
                        "classification": "EXACT_TARGET",
                        "confidence": 0.99,
                        "reason": "The image depicts the exact black cordless drill and kit.",
                    },
                },
                "summary": "Only image_02 and image_03 belong to the target product.",
            },
            "verify_and_order_exact_supplier_gallery": {
                "selected_image_ids": ["image_03", "image_02"],
                "decisions": {
                    "image_02": {
                        "selected": True,
                        "reason": "Useful alternate view of the exact target.",
                    },
                    "image_03": {
                        "selected": True,
                        "reason": "Strongest main image of the exact target.",
                    },
                },
                "summary": "Use the hero first, then the alternate view.",
            },
        }
    )

    result = finalize_supplier_listing_images(tmp_path, provider)

    assert provider.calls == 3
    assert result.status == "ai_ranked"
    assert result.selected == (hero, alternate)
    assert result.model_calls == 3
    assert [request["task"] for request in provider.requests] == [
        "infer_grounded_supplier_product_identity",
        "classify_supplier_listing_image_ownership",
        "verify_and_order_exact_supplier_gallery",
    ]

    identity_sources = provider.requests[0]["grounded_sources"]
    assert isinstance(identity_sources, list)
    assert "Corn Flakes" not in json.dumps(identity_sources, ensure_ascii=False)

    ownership_sources = provider.requests[1]["grounded_sources"]
    assert isinstance(ownership_sources, list)
    assert [item["source_id"] for item in ownership_sources] == [
        "image_01",
        "image_02",
        "image_03",
    ]

    gallery_sources = provider.requests[2]["grounded_sources"]
    assert isinstance(gallery_sources, list)
    assert [item["source_id"] for item in gallery_sources] == ["image_02", "image_03"]

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["outputs"]["primary_source_listing_images"] == [
        str(hero),
        str(alternate),
    ]
    assert manifest["listing_image_ranking"]["strategy"] == (
        "grounded_identity_then_ai_ownership_then_gallery_verification"
    )
    assert manifest["listing_image_ranking"]["ownership_eligible_count"] == 2
    assert manifest["total_model_calls"] == 7

    report = json.loads((tmp_path / "listing-image-selection.json").read_text(encoding="utf-8"))
    assert report["policy"]["ownership_semantic_owner"] == "multimodal_ai"
    assert report["policy"]["gallery_semantic_owner"] == "multimodal_ai"
    assert report["policy"]["precision_policy"] == "fewer_correct_images_over_quota_fill"
    assert report["selected"] == [str(hero), str(alternate)]
    assert report["candidates"][0]["ownership"]["classification"] == "OTHER_PRODUCT"
    assert report["candidates"][0]["gallery"] is None


def test_ownership_ai_may_reject_all_candidates_without_forcing_gallery_fill(tmp_path: Path) -> None:
    unrelated = _write_image(tmp_path / "milk.jpg", (100, 150, 200))
    snapshot = tmp_path / "source-snapshot.json"
    _write_snapshot(snapshot)
    manifest_path = _write_manifest(tmp_path, [unrelated], snapshot)

    provider = _FakeProvider(
        {
            "infer_grounded_supplier_product_identity": _identity_response(),
            "classify_supplier_listing_image_ownership": {
                "decisions": {
                    "image_01": {
                        "classification": "OTHER_PRODUCT",
                        "confidence": 0.99,
                        "reason": "The image is milk, not the target cordless drill.",
                    }
                },
                "summary": "No candidate belongs to the target product.",
            },
        }
    )

    result = finalize_supplier_listing_images(tmp_path, provider)

    assert provider.calls == 2
    assert result.status == "ai_ownership_empty"
    assert result.selected == ()
    assert result.semantically_rejected_count == 1
    assert [request["task"] for request in provider.requests] == [
        "infer_grounded_supplier_product_identity",
        "classify_supplier_listing_image_ownership",
    ]

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["outputs"]["primary_source_listing_images"] == []
    assert manifest["listing_image_ranking"]["selected_count"] == 0


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
            "infer_grounded_supplier_product_identity": _identity_response(),
            "classify_supplier_listing_image_ownership": {
                "decisions": {
                    "image_01": {
                        "classification": "EXACT_TARGET",
                        "confidence": 0.99,
                        "reason": "The supplier image depicts the exact target drill.",
                    }
                },
                "summary": "The supplier image belongs to the target product.",
            },
            "verify_and_order_exact_supplier_gallery": {
                "selected_image_ids": ["image_01"],
                "decisions": {
                    "image_01": {
                        "selected": True,
                        "reason": "Use the exact supplier product image.",
                    }
                },
                "summary": "One exact target image is available.",
            },
        }
    )

    result = finalize_supplier_listing_images(tmp_path, provider)

    assert result.selected == (supplier,)
    ownership_sources = provider.requests[1]["grounded_sources"]
    assert isinstance(ownership_sources, list)
    assert len(ownership_sources) == 1
    assert Path(ownership_sources[0]["image_path"]) == supplier

    report = json.loads((tmp_path / "listing-image-selection.json").read_text(encoding="utf-8"))
    assert report["transport_rejected"] == [
        {
            "source_index": 1,
            "path": str(auxiliary),
            "reason": "customer_auxiliary_not_auto_listing_candidate",
        }
    ]
