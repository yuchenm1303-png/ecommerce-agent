from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .product_pack import ProductPackCapture, capture_product_pack, load_product_pack_manifest
from .source_capture import CapturedProductSource, capture_product_source
from .source_snapshot import SourceSnapshot, source_snapshot_from_json


@dataclass(slots=True, frozen=True)
class AcquiredProductInput:
    mode: str
    product_reference_url: str
    snapshot_path: Path
    snapshot: SourceSnapshot
    supplier_snapshot_paths: tuple[Path, ...]
    customer_snapshot_paths: tuple[Path, ...]
    evidence_image_paths: tuple[Path, ...]
    listing_image_paths: tuple[Path, ...]
    screenshot_path: Path | None = None
    pack_manifest_path: Path | None = None
    source_cache_hit: bool = False
    source_edge_launched: bool = False
    warnings: tuple[str, ...] = ()

    @property
    def product_image_paths(self) -> tuple[Path, ...]:
        return self.evidence_image_paths


def validate_product_input(
    *,
    product_url: str = "",
    product_files: Iterable[str | Path] = (),
    product_pack_manifest: str | Path | None = None,
) -> str:
    url = str(product_url or "").strip()
    files = [str(value).strip() for value in product_files if str(value).strip()]
    manifest = str(product_pack_manifest or "").strip()
    selected = int(bool(url)) + int(bool(files)) + int(bool(manifest))
    if selected != 1:
        raise ValueError("商品输入必须且只能选择一种：供应商 URL、客户资料文件、或已解析资料包。")
    if manifest:
        return "customer_product_pack"
    if files:
        return "customer_product_pack"
    return "supplier_url"


def _from_pack_capture(captured: ProductPackCapture) -> AcquiredProductInput:
    return AcquiredProductInput(
        mode="customer_product_pack",
        product_reference_url=captured.product_reference_url,
        snapshot_path=captured.bootstrap_snapshot_path,
        snapshot=captured.bootstrap_snapshot,
        supplier_snapshot_paths=(),
        customer_snapshot_paths=captured.customer_snapshot_paths,
        evidence_image_paths=captured.evidence_image_paths,
        listing_image_paths=captured.listing_image_paths,
        pack_manifest_path=captured.manifest_path,
        warnings=captured.warnings,
    )


def _from_pack_manifest(path: str | Path) -> AcquiredProductInput:
    manifest_path = Path(path).resolve()
    payload = load_product_pack_manifest(manifest_path)
    bootstrap_path = Path(str(payload["bootstrap_snapshot"]))
    return AcquiredProductInput(
        mode="customer_product_pack",
        product_reference_url=str(payload["product_reference_url"]),
        snapshot_path=bootstrap_path,
        snapshot=source_snapshot_from_json(bootstrap_path),
        supplier_snapshot_paths=(),
        customer_snapshot_paths=tuple(Path(str(value)) for value in payload.get("customer_snapshots") or []),
        evidence_image_paths=tuple(Path(str(value)) for value in payload.get("evidence_images") or []),
        listing_image_paths=tuple(Path(str(value)) for value in payload.get("listing_images") or []),
        pack_manifest_path=manifest_path,
        warnings=tuple(str(value) for value in payload.get("warnings") or []),
    )


def _from_supplier(captured: CapturedProductSource, requested_url: str) -> AcquiredProductInput:
    reference = captured.snapshot.final_url or requested_url
    screenshot = captured.screenshot_path if captured.screenshot_path.is_file() else None
    return AcquiredProductInput(
        mode="supplier_url",
        product_reference_url=reference,
        snapshot_path=captured.snapshot_path,
        snapshot=captured.snapshot,
        supplier_snapshot_paths=(captured.snapshot_path,),
        customer_snapshot_paths=(),
        evidence_image_paths=tuple(captured.product_image_paths),
        listing_image_paths=(),
        screenshot_path=screenshot,
        source_cache_hit=bool(captured.cache_hit),
        source_edge_launched=bool(captured.launched_now),
    )


def acquire_product_input(
    *,
    output_dir: str | Path,
    product_url: str = "",
    product_files: Iterable[str | Path] = (),
    product_pack_manifest: str | Path | None = None,
    source_profile_dir: str | Path = "browser_profiles/source-edge",
    source_cdp_port: int = 9333,
    source_wait_ms: int = 1800,
    source_scroll_wait_ms: int = 180,
    source_max_scroll_steps: int = 120,
    source_max_visible_text_chars: int = 120_000,
    source_use_current_page: bool = False,
    source_cache_dir: str | Path | None = None,
    source_cache_ttl_seconds: int = 900,
    refresh_source: bool = False,
) -> AcquiredProductInput:
    """Acquire one normalized product input without applying product semantics.

    Supplier URLs retain the existing browser capture contract. Customer files are
    persisted and parsed once into a Product Pack; a downstream subprocess can
    receive ``product_pack_manifest`` to reuse exactly those bytes/snapshots.
    """

    mode = validate_product_input(
        product_url=product_url,
        product_files=product_files,
        product_pack_manifest=product_pack_manifest,
    )
    if mode == "customer_product_pack":
        if str(product_pack_manifest or "").strip():
            return _from_pack_manifest(str(product_pack_manifest))
        return _from_pack_capture(
            capture_product_pack(product_files, output_dir=Path(output_dir) / "product-pack")
        )

    url = str(product_url).strip()
    captured = capture_product_source(
        url,
        output_dir=output_dir,
        profile_dir=source_profile_dir,
        cdp_port=int(source_cdp_port),
        initial_wait_ms=int(source_wait_ms),
        scroll_wait_ms=int(source_scroll_wait_ms),
        max_scroll_steps=int(source_max_scroll_steps),
        max_visible_text_chars=int(source_max_visible_text_chars),
        use_current_page=bool(source_use_current_page),
        cache_dir=source_cache_dir,
        cache_ttl_seconds=int(source_cache_ttl_seconds),
        force_refresh=bool(refresh_source),
    )
    return _from_supplier(captured, url)


def product_input_manifest_payload(acquired: AcquiredProductInput) -> dict[str, Any]:
    return {
        "mode": acquired.mode,
        "product_reference_url": acquired.product_reference_url,
        "bootstrap_snapshot": str(acquired.snapshot_path.resolve()),
        "supplier_snapshots": [str(path.resolve()) for path in acquired.supplier_snapshot_paths],
        "customer_snapshots": [str(path.resolve()) for path in acquired.customer_snapshot_paths],
        "evidence_images": [str(path.resolve()) for path in acquired.evidence_image_paths],
        "listing_images": [str(path.resolve()) for path in acquired.listing_image_paths],
        "screenshot": str(acquired.screenshot_path.resolve()) if acquired.screenshot_path else "",
        "product_pack_manifest": str(acquired.pack_manifest_path.resolve()) if acquired.pack_manifest_path else "",
        "source_cache_hit": acquired.source_cache_hit,
        "source_edge_launched": acquired.source_edge_launched,
        "warnings": list(acquired.warnings),
    }


__all__ = [
    "AcquiredProductInput",
    "acquire_product_input",
    "product_input_manifest_payload",
    "validate_product_input",
]
