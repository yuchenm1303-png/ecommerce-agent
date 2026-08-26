from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.product_input import validate_product_input


def _manifest(tmp_path: Path, reference: str) -> Path:
    bootstrap = tmp_path / "bootstrap-source.json"
    bootstrap.write_text("{}", encoding="utf-8")
    path = tmp_path / "product-pack.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "input_mode": "customer_product_pack",
                "product_reference_url": reference,
                "bootstrap_snapshot": str(bootstrap),
                "customer_snapshots": [],
                "evidence_images": [],
                "listing_images": [],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_pack_manifest_may_carry_its_matching_stable_reference_url(tmp_path: Path) -> None:
    reference = "https://product-pack.invalid/0123456789abcdef01234567"
    manifest = _manifest(tmp_path, reference)

    assert (
        validate_product_input(
            product_url=reference,
            product_pack_manifest=manifest,
        )
        == "customer_product_pack"
    )


def test_real_supplier_url_becomes_primary_over_product_pack_reference(tmp_path: Path) -> None:
    manifest = _manifest(
        tmp_path,
        "https://product-pack.invalid/0123456789abcdef01234567",
    )

    assert (
        validate_product_input(
            product_url="https://detail.1688.com/offer/850845635717.html?sku=black",
            product_pack_manifest=manifest,
        )
        == "supplier_url"
    )


def test_raw_files_supplement_supplier_url(tmp_path: Path) -> None:
    source = tmp_path / "spec.txt"
    source.write_text("M8 dash camera", encoding="utf-8")

    assert (
        validate_product_input(
            product_url="https://detail.1688.com/offer/850845635717.html?sku=black",
            product_files=[source],
        )
        == "supplier_url"
    )


def test_manifest_and_raw_files_cannot_compete_as_two_customer_packs(tmp_path: Path) -> None:
    manifest = _manifest(
        tmp_path,
        "https://product-pack.invalid/0123456789abcdef01234567",
    )
    source = tmp_path / "spec.txt"
    source.write_text("M8 dash camera", encoding="utf-8")

    with pytest.raises(ValueError, match="不能再同时传入原始 product files"):
        validate_product_input(
            product_files=[source],
            product_pack_manifest=manifest,
        )
