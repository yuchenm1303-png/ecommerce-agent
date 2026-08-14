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


def test_pack_manifest_rejects_a_different_product_reference(tmp_path: Path) -> None:
    manifest = _manifest(
        tmp_path,
        "https://product-pack.invalid/0123456789abcdef01234567",
    )

    with pytest.raises(ValueError, match="身份不一致"):
        validate_product_input(
            product_url="https://product-pack.invalid/ffffffffffffffffffffffff",
            product_pack_manifest=manifest,
        )


def test_raw_files_and_supplier_url_are_mutually_exclusive(tmp_path: Path) -> None:
    source = tmp_path / "spec.txt"
    source.write_text("M8 dash camera", encoding="utf-8")

    with pytest.raises(ValueError, match="不能同时"):
        validate_product_input(
            product_url="https://example.invalid/product",
            product_files=[source],
        )
