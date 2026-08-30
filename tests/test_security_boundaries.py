from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from app import product_pack
from app.product_pack import ProductPackError, capture_product_pack, load_product_pack_manifest
from app.public_resource_fetch import PublicResourceFetchError, validate_public_resource_url


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://10.1.2.3/private",
        "http://192.168.1.8/private",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/private",
        "http://localhost/private",
    ],
)
def test_background_resource_policy_rejects_non_public_targets(url: str) -> None:
    with pytest.raises(PublicResourceFetchError):
        validate_public_resource_url(url)


def test_background_resource_policy_does_not_whitelist_supplier_domains() -> None:
    assert validate_public_resource_url("https://8.8.8.8/example.jpg") == "https://8.8.8.8/example.jpg"


def test_product_pack_refuses_to_delete_unmanaged_output_directory(tmp_path: Path) -> None:
    source = tmp_path / "input.txt"
    source.write_text("Brand: Example\nModel: A1", encoding="utf-8")
    output = tmp_path / "existing"
    output.mkdir()
    sentinel = output / "keep-me.txt"
    sentinel.write_text("must survive", encoding="utf-8")

    with pytest.raises(ProductPackError, match="拒绝覆盖非 Product Pack 管理目录"):
        capture_product_pack([source], output_dir=output)

    assert sentinel.read_text(encoding="utf-8") == "must survive"


def test_product_pack_manifest_cannot_escape_pack_root(tmp_path: Path) -> None:
    source = tmp_path / "input.txt"
    source.write_text("Brand: Example\nModel: A1", encoding="utf-8")
    captured = capture_product_pack([source], output_dir=tmp_path / "pack")
    external = tmp_path / "external.json"
    external.write_text('{"visible_text":"external"}', encoding="utf-8")

    payload = json.loads(captured.manifest_path.read_text(encoding="utf-8"))
    payload["bootstrap_snapshot"] = str(external)
    captured.manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ProductPackError, match="越出资料包目录"):
        load_product_pack_manifest(captured.manifest_path)


def test_office_archive_uses_expanded_size_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive_path = tmp_path / "sample.xlsx"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/worksheets/sheet1.xml", "x" * 2048)
    monkeypatch.setattr(product_pack, "_MAX_ARCHIVE_EXPANDED_BYTES", 1024)

    with pytest.raises(ProductPackError, match="解压后的内部数据超过安全上限"):
        product_pack._validate_office_archive_budget(archive_path, label="Excel")


def test_stable_publish_requires_signing_but_development_build_contract_remains_separate() -> None:
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "publish-update.yml").read_text(encoding="utf-8")
    build_script = (root / "scripts" / "build_windows.ps1").read_text(encoding="utf-8")

    assert "Require Stable code signing" in workflow
    assert "VPK_AZURE_TRUSTED_SIGN_FILE: ${{ secrets.VPK_AZURE_TRUSTED_SIGN_FILE }}" in workflow
    assert "VPK_SIGN_PARAMS: ${{ secrets.VPK_SIGN_PARAMS }}" in workflow
    assert "package is unsigned. This is acceptable for development/E2E only" in build_script


def test_portal_device_allocation_is_serialized_in_database() -> None:
    root = Path(__file__).resolve().parents[1]
    migration = (
        root / "supabase" / "migrations" / "20260830151000_portal_device_activation_atomic_v1.sql"
    ).read_text(encoding="utf-8")
    edge = (root / "supabase" / "functions" / "portal-license" / "index.ts").read_text(encoding="utf-8")

    assert "for update;" in migration.casefold()
    assert "activate_listing_portal_device_v1" in migration
    assert "grant execute" in migration.casefold()
    assert "to service_role" in migration.casefold()
    assert 'admin.rpc(\n    "activate_listing_portal_device_v1"' in edge
