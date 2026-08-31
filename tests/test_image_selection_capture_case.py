from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.image_selection_lab import capture as capture_module
from tools.image_selection_lab.capture import (
    build_captured_target_product,
    capture_case_from_url,
)
from tools.image_selection_lab.core import ImageSelectionLabError


def _snapshot() -> SimpleNamespace:
    return SimpleNamespace(
        final_url="https://shop.example/product/final",
        title="Dyson Airwrap HS05 Complete Long",
        json_ld=[
            {
                "@context": "https://schema.org",
                "@graph": [
                    {
                        "@type": "Product",
                        "name": "Dyson Airwrap HS05 Complete Long",
                        "brand": {"@type": "Brand", "name": "Dyson"},
                        "model": "HS05",
                        "sku": "SKU-HS05",
                    }
                ],
            }
        ],
    )


def test_build_captured_target_product_uses_grounded_json_ld_without_title_inference() -> None:
    target = build_captured_target_product(_snapshot())

    assert target["name"] == "Dyson Airwrap HS05 Complete Long"
    assert target["brand"] == "Dyson"
    assert target["model"] == "HS05"
    assert target["sku"] == "SKU-HS05"
    assert target["identity_source"] == "json_ld_product"

    title_only = SimpleNamespace(title="Widget MODEL-123", json_ld=[])
    fallback = build_captured_target_product(title_only)
    assert fallback == {"name": "Widget MODEL-123", "identity_source": "page_title"}
    assert "model" not in fallback


def test_capture_case_reuses_production_collector_and_exact_dedupes_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict] = []

    def fake_capture(url: str, **kwargs):
        calls.append({"url": url, **kwargs})
        raw = Path(kwargs["output_dir"])
        raw.mkdir(parents=True, exist_ok=True)
        first = raw / "product-01.jpg"
        duplicate = raw / "product-02.jpg"
        second = raw / "product-03.jpg"
        first.write_bytes(b"same-image-bytes")
        duplicate.write_bytes(b"same-image-bytes")
        second.write_bytes(b"different-image-bytes")
        snapshot_path = raw / "source-snapshot.json"
        screenshot_path = raw / "source-page.png"
        snapshot_path.write_text("{}", encoding="utf-8")
        screenshot_path.write_bytes(b"screenshot")
        return SimpleNamespace(
            snapshot=_snapshot(),
            snapshot_path=snapshot_path,
            screenshot_path=screenshot_path,
            launched_now=True,
            cache_hit=False,
            product_image_paths=(first, duplicate, second),
        )

    monkeypatch.setattr(capture_module, "capture_product_source", fake_capture)
    case_path = capture_case_from_url(
        url="https://shop.example/product",
        cases_root=tmp_path / "cases",
        case_id="B01_dyson_hs05",
    )

    assert len(calls) == 1
    assert calls[0]["force_refresh"] is True
    assert calls[0]["cache_dir"] is None
    assert calls[0]["cache_ttl_seconds"] == 0

    case = json.loads(case_path.read_text(encoding="utf-8"))
    assert case["label_status"] == "needs_review"
    assert case["target_product"]["model"] == "HS05"
    assert len(case["candidates"]) == 2
    assert all(item["ground_truth"] is None for item in case["candidates"])
    assert [item["capture"]["source_index"] for item in case["candidates"]] == [1, 3]
    assert case["capture"]["ai_called"] is False

    capture = json.loads((case_path.parent / "capture.json").read_text(encoding="utf-8"))
    assert capture["capture_mode"] == "production_source_capture"
    assert capture["captured_product_image_count"] == 3
    assert capture["unique_candidate_count"] == 2
    assert capture["exact_duplicate_count"] == 1
    assert capture["ai_called"] is False
    assert capture["listing_action_performed"] is False
    assert (case_path.parent / "images" / "img_001.jpg").read_bytes() == b"same-image-bytes"
    assert (case_path.parent / "images" / "img_002.jpg").read_bytes() == b"different-image-bytes"


def test_capture_case_human_target_fields_override_page_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_capture(url: str, **kwargs):
        raw = Path(kwargs["output_dir"])
        raw.mkdir(parents=True, exist_ok=True)
        image = raw / "product.jpg"
        image.write_bytes(b"image")
        snapshot_path = raw / "snapshot.json"
        screenshot_path = raw / "screen.png"
        snapshot_path.write_text("{}", encoding="utf-8")
        screenshot_path.write_bytes(b"screen")
        return SimpleNamespace(
            snapshot=_snapshot(),
            snapshot_path=snapshot_path,
            screenshot_path=screenshot_path,
            launched_now=False,
            cache_hit=False,
            product_image_paths=(image,),
        )

    monkeypatch.setattr(capture_module, "capture_product_source", fake_capture)
    path = capture_case_from_url(
        url="https://shop.example/product",
        cases_root=tmp_path / "cases",
        case_id="manual_target",
        target_name="Exact supplied product title",
        target_brand="Exact Brand",
        target_model="Exact Model",
        target_variant="Exact Variant",
    )
    target = json.loads(path.read_text(encoding="utf-8"))["target_product"]
    assert target["name"] == "Exact supplied product title"
    assert target["brand"] == "Exact Brand"
    assert target["model"] == "Exact Model"
    assert target["variant"] == "Exact Variant"
    assert target["identity_source"] == "human_override+json_ld"


def test_capture_case_never_overwrites_existing_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "cases" / "existing"
    destination.mkdir(parents=True)
    marker = destination / "keep.txt"
    marker.write_text("keep", encoding="utf-8")

    def forbidden_capture(*args, **kwargs):
        raise AssertionError("collector must not run when destination already exists")

    monkeypatch.setattr(capture_module, "capture_product_source", forbidden_capture)
    with pytest.raises(ImageSelectionLabError, match="already exists"):
        capture_case_from_url(
            url="https://shop.example/product",
            cases_root=tmp_path / "cases",
            case_id="existing",
        )
    assert marker.read_text(encoding="utf-8") == "keep"


def test_capture_module_has_no_semantic_provider_or_listing_action_dependency() -> None:
    source = Path("tools/image_selection_lab/capture.py").read_text(encoding="utf-8")
    assert "build_semantic_provider" not in source
    assert "listing_image_ranker" not in source
    assert "makro_create_listing" not in source
