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
                "visible_text": "Acme Cordless Drill 18V Black with battery and charger.",
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


def _blind_fact(
    subject: str,
    identity: str,
    colour: str,
    design: str,
    guess: str,
    quality: str,
) -> dict[str, object]:
    return {
        "visual_subject": subject,
        "readable_identity": identity,
        "raw_colour_materials": colour,
        "design_configuration": design,
        "neutral_product_guess": guess,
        "visual_uncertainty": "",
        "presentation_quality": quality,
    }


def test_blind_perception_then_text_ownership_filters_unrelated_products(tmp_path: Path) -> None:
    unrelated = _write_image(tmp_path / "01-corn-flakes.jpg", (230, 230, 230))
    alternate = _write_image(tmp_path / "02-drill-side.jpg", (180, 180, 180))
    hero = _write_image(tmp_path / "03-drill-hero.jpg", (250, 250, 250))
    snapshot = tmp_path / "source-snapshot.json"
    _write_snapshot(snapshot)
    manifest_path = _write_manifest(tmp_path, [unrelated, alternate, hero], snapshot)

    provider = _FakeProvider(
        {
            "infer_grounded_supplier_product_identity": _identity_response(),
            "observe_supplier_listing_images_blind": {
                "facts": {
                    "image_01": _blind_fact(
                        "box of breakfast cereal",
                        "Corn Flakes",
                        "yellow cereal box",
                        "rectangular food carton",
                        "breakfast cereal",
                        "clear product view",
                    ),
                    "image_02": _blind_fact(
                        "cordless drill shown from the side",
                        "Acme; 18V",
                        "black plastic and metal",
                        "pistol-grip drill with battery",
                        "Acme cordless drill",
                        "clear alternate angle",
                    ),
                    "image_03": _blind_fact(
                        "cordless drill with battery and charger",
                        "Acme; 18V",
                        "black plastic and metal",
                        "pistol-grip drill with battery and charger kit",
                        "Acme cordless drill kit",
                        "strong complete hero view",
                    ),
                },
                "summary": "target-blind observations",
            },
            "compare_blind_image_facts_to_target": {
                "decisions": {
                    "image_01": {
                        "target_conflicts": "Frozen facts establish breakfast cereal, not a cordless drill.",
                        "target_match_evidence": "",
                        "target_identity_gaps": "",
                        "classification": "OTHER_PRODUCT",
                        "confidence": 0.99,
                        "reason": "Different sellable product.",
                    },
                    "image_02": {
                        "target_conflicts": "",
                        "target_match_evidence": "Frozen Acme 18V black drill facts establish the target.",
                        "target_identity_gaps": "",
                        "classification": "EXACT_TARGET",
                        "confidence": 0.97,
                        "reason": "Exact target supported by frozen facts.",
                    },
                    "image_03": {
                        "target_conflicts": "",
                        "target_match_evidence": "Frozen Acme 18V black drill kit facts establish the full target sale unit.",
                        "target_identity_gaps": "",
                        "classification": "EXACT_TARGET",
                        "confidence": 0.99,
                        "reason": "Exact target kit supported by frozen facts.",
                    },
                },
                "summary": "two exact candidates",
            },
            "order_identity_approved_supplier_gallery": {
                "selected_image_ids": ["image_03", "image_02"],
                "decisions": {
                    "image_02": {"selected": True, "reason": "Useful alternate angle."},
                    "image_03": {"selected": True, "reason": "Strongest complete hero."},
                },
                "summary": "hero then alternate",
            },
        }
    )

    result = finalize_supplier_listing_images(tmp_path, provider)

    assert provider.calls == 4
    assert result.status == "ai_ranked"
    assert result.selected == (hero, alternate)
    assert result.model_calls == 4
    assert [request["task"] for request in provider.requests] == [
        "infer_grounded_supplier_product_identity",
        "observe_supplier_listing_images_blind",
        "compare_blind_image_facts_to_target",
        "order_identity_approved_supplier_gallery",
    ]

    blind_context = provider.requests[1]["context"]
    assert isinstance(blind_context, dict)
    assert "target_product" not in blind_context

    ownership_sources = provider.requests[2]["grounded_sources"]
    assert ownership_sources == []
    ownership_context = provider.requests[2]["context"]
    assert isinstance(ownership_context, dict)
    assert "target_product" in ownership_context
    assert "blind_visual_facts" in ownership_context

    gallery_context = provider.requests[3]["context"]
    assert isinstance(gallery_context, dict)
    assert "target_product" not in gallery_context

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["outputs"]["primary_source_listing_images"] == [str(hero), str(alternate)]
    assert manifest["listing_image_ranking"]["strategy"] == (
        "target_blind_perception_then_frozen_facts_ownership_then_quality_gallery"
    )
    assert manifest["listing_image_ranking"]["ownership_eligible_count"] == 2
    assert manifest["total_model_calls"] == 8

    report = json.loads((tmp_path / "listing-image-selection.json").read_text(encoding="utf-8"))
    assert report["policy"]["blind_visual_facts_target_visible"] is False
    assert report["policy"]["ownership_candidate_pixels_visible"] is False
    assert report["policy"]["gallery_target_identity_visible"] is False
    assert report["selected"] == [str(hero), str(alternate)]
    assert report["candidates"][0]["blind_visual_facts"]["visual_subject"] == (
        "box of breakfast cereal"
    )
    assert report["candidates"][0]["ownership"]["classification"] == "OTHER_PRODUCT"
    assert report["candidates"][0]["gallery"] is None


def test_ownership_may_reject_all_without_running_gallery(tmp_path: Path) -> None:
    unrelated = _write_image(tmp_path / "milk.jpg", (100, 150, 200))
    snapshot = tmp_path / "source-snapshot.json"
    _write_snapshot(snapshot)
    manifest_path = _write_manifest(tmp_path, [unrelated], snapshot)

    provider = _FakeProvider(
        {
            "infer_grounded_supplier_product_identity": _identity_response(),
            "observe_supplier_listing_images_blind": {
                "facts": {
                    "image_01": _blind_fact(
                        "milk carton",
                        "Full Cream Milk",
                        "white and blue carton",
                        "rectangular beverage carton",
                        "milk product",
                        "clear product view",
                    )
                },
                "summary": "blind milk observation",
            },
            "compare_blind_image_facts_to_target": {
                "decisions": {
                    "image_01": {
                        "target_conflicts": "Frozen facts establish milk, not a drill.",
                        "target_match_evidence": "",
                        "target_identity_gaps": "",
                        "classification": "OTHER_PRODUCT",
                        "confidence": 0.99,
                        "reason": "Different product.",
                    }
                },
                "summary": "no exact candidates",
            },
        }
    )

    result = finalize_supplier_listing_images(tmp_path, provider)

    assert provider.calls == 3
    assert result.status == "ai_ownership_empty"
    assert result.selected == ()
    assert result.model_calls == 3
    assert [request["task"] for request in provider.requests] == [
        "infer_grounded_supplier_product_identity",
        "observe_supplier_listing_images_blind",
        "compare_blind_image_facts_to_target",
    ]

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["outputs"]["primary_source_listing_images"] == []
    assert manifest["listing_image_ranking"]["selected_count"] == 0


def test_customer_auxiliary_images_never_enter_blind_perception(tmp_path: Path) -> None:
    supplier = _write_image(tmp_path / "supplier.jpg", (245, 245, 245))
    auxiliary = _write_image(tmp_path / "auxiliary.jpg", (120, 140, 160))
    snapshot = tmp_path / "source-snapshot.json"
    _write_snapshot(snapshot)
    manifest_path = _write_manifest(tmp_path, [auxiliary, supplier], snapshot)

    product_pack = tmp_path / "product-pack.json"
    product_pack.write_text(
        json.dumps({"evidence_images": [str(auxiliary)], "listing_images": [str(auxiliary)]}),
        encoding="utf-8",
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["outputs"]["product_pack_manifest"] = str(product_pack)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    provider = _FakeProvider(
        {
            "infer_grounded_supplier_product_identity": _identity_response(),
            "observe_supplier_listing_images_blind": {
                "facts": {
                    "image_01": _blind_fact(
                        "black cordless drill kit",
                        "Acme; 18V",
                        "black plastic and metal",
                        "drill, battery and charger",
                        "Acme cordless drill kit",
                        "clear hero view",
                    )
                },
                "summary": "supplier only",
            },
            "compare_blind_image_facts_to_target": {
                "decisions": {
                    "image_01": {
                        "target_conflicts": "",
                        "target_match_evidence": "Frozen Acme 18V drill kit facts establish target.",
                        "target_identity_gaps": "",
                        "classification": "EXACT_TARGET",
                        "confidence": 0.99,
                        "reason": "Exact target.",
                    }
                },
                "summary": "exact",
            },
            "order_identity_approved_supplier_gallery": {
                "selected_image_ids": ["image_01"],
                "decisions": {
                    "image_01": {"selected": True, "reason": "Use clear supplier hero."}
                },
                "summary": "one image",
            },
        }
    )

    result = finalize_supplier_listing_images(tmp_path, provider)

    assert result.selected == (supplier,)
    blind_sources = provider.requests[1]["grounded_sources"]
    assert isinstance(blind_sources, list)
    assert len(blind_sources) == 1
    assert Path(blind_sources[0]["image_path"]) == supplier

    report = json.loads((tmp_path / "listing-image-selection.json").read_text(encoding="utf-8"))
    assert report["transport_rejected"] == [
        {
            "source_index": 1,
            "path": str(auxiliary),
            "reason": "customer_auxiliary_not_auto_listing_candidate",
        }
    ]
