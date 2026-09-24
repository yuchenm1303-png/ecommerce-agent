from __future__ import annotations

import json
import zipfile
from pathlib import Path

from openpyxl import Workbook
from PIL import Image

from app.product_pack import capture_product_pack, load_product_pack_manifest
from app.semantic_grounding import TEXT_KIND, build_grounding_catalog
from app.source_snapshot import source_snapshot_from_json


def _write_png(path: Path, size: tuple[int, int] = (1200, 1200)) -> None:
    image = Image.new("RGB", size, "white")
    image.save(path)


def test_product_pack_persists_text_table_and_images(tmp_path: Path) -> None:
    text = tmp_path / "notes.txt"
    text.write_text("Model: M8\nWiFi: supported\nRecording: 4K", encoding="utf-8")

    csv_path = tmp_path / "fields.csv"
    csv_path.write_text("Field,Value\nBrand,Acme\nWeight,420 g\n", encoding="utf-8")

    workbook_path = tmp_path / "spec.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Specifications"
    sheet.append(["Field", "Value"])
    sheet.append(["Width", "130 mm"])
    sheet.append(["Height", "80 mm"])
    workbook.save(workbook_path)

    photo = tmp_path / "product.png"
    _write_png(photo)

    result = capture_product_pack(
        [text, csv_path, workbook_path, photo],
        output_dir=tmp_path / "captured",
    )

    assert result.manifest_path.is_file()
    assert result.bootstrap_snapshot_path.is_file()
    assert len(result.customer_snapshot_paths) >= 3
    assert len(result.evidence_image_paths) == 1
    assert len(result.listing_image_paths) == 1
    assert result.product_reference_url.startswith("https://product-pack.invalid/")

    bootstrap = source_snapshot_from_json(result.bootstrap_snapshot_path)
    assert "M8" in bootstrap.visible_text
    # Intake remains mechanical: the raw two-column rows are preserved for the
    # Resolver instead of Python deciding that a particular table is Makro data.
    assert "Brand | Acme" in bootstrap.visible_text
    assert "Width | 130 mm" in bootstrap.visible_text
    assert any(row.key == "Field" and row.value == "Brand" for row in bootstrap.table_rows)
    assert any(row.key == "Value" and row.value == "130 mm" for row in bootstrap.table_rows)

    manifest = load_product_pack_manifest(result.manifest_path)
    assert manifest["input_mode"] == "customer_product_pack"
    assert len(manifest["evidence_images"]) == 1


def test_product_pack_bootstrap_expands_to_exact_customer_file_sources(tmp_path: Path) -> None:
    notes = tmp_path / "notes.txt"
    notes.write_text("Model: M8\nCapacity: 500 ml", encoding="utf-8")
    specs = tmp_path / "spec.csv"
    specs.write_text("Field,Value\nMaterial,ABS\nWeight,420 g\n", encoding="utf-8")

    result = capture_product_pack([notes, specs], output_dir=tmp_path / "captured")

    catalog = build_grounding_catalog(
        supplier_snapshots=(str(result.bootstrap_snapshot_path),),
    )
    text_sources = [source for source in catalog.sources if source.kind == TEXT_KIND]

    assert text_sources
    assert all(source.source_type == "customer_file" for source in text_sources)
    assert all(source.source_id.startswith("customer-file:") for source in text_sources)
    assert all(not source.source_id.startswith("supplier:") for source in text_sources)
    assert all("product-pack://local" not in source.origin for source in text_sources)

    exact_origins = {
        snapshot.final_url or snapshot.requested_url
        for snapshot in (
            source_snapshot_from_json(path) for path in result.customer_snapshot_paths
        )
    }
    assert exact_origins
    assert all(any(source.origin.startswith(origin) for origin in exact_origins) for source in text_sources)

    # Resolver, planner and real executor rebuild the catalog independently. The
    # Product Pack expansion must therefore be deterministic across safety gates.
    rebuilt = build_grounding_catalog(
        supplier_snapshots=(str(result.bootstrap_snapshot_path),),
    )
    assert catalog.as_manifest() == rebuilt.as_manifest()


def test_zip_accepts_supported_files_and_ignores_executables(tmp_path: Path) -> None:
    archive_path = tmp_path / "pack.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("docs/spec.txt", "Brand: Acme\nModel: M8")
        archive.writestr("unsafe/tool.exe", b"not executable here")
        archive.writestr("../escape.txt", "must not be extracted")

    result = capture_product_pack([archive_path], output_dir=tmp_path / "captured")
    payload = json.loads(result.manifest_path.read_text(encoding="utf-8"))

    stored = [Path(item["stored_path"]).name for item in payload["stored_files"]]
    assert any(name.endswith("spec.txt") for name in stored)
    assert not any(name.endswith("tool.exe") for name in stored)
    assert not (tmp_path / "escape.txt").exists()


def test_duplicate_input_bytes_are_stored_once(tmp_path: Path) -> None:
    first = tmp_path / "a.txt"
    second = tmp_path / "b.txt"
    first.write_text("same product data", encoding="utf-8")
    second.write_text("same product data", encoding="utf-8")

    result = capture_product_pack([first, second], output_dir=tmp_path / "captured")

    docs = [item for item in result.stored_files if item.kind == "document"]
    assert len(docs) == 1
