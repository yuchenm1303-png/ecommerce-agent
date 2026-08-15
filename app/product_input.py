from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .product_pack import ProductPackCapture, capture_product_pack, load_product_pack_manifest
from .source_capture import CapturedProductSource, capture_product_source
from .source_snapshot import SourceSnapshot, source_snapshot_from_json, write_source_snapshot


_COMBINED_TEXT_LIMIT = 160_000
_COMBINED_TABLE_ROW_LIMIT = 12_000


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
    """Return the primary input mode while allowing customer files as supplements.

    A real supplier URL stays the primary identity/source even when customer files
    are attached. A files-only input remains a customer Product Pack. An already
    parsed Product Pack may be paired with a real supplier URL, but its synthetic
    product-pack URL is still recognized as files-only internal reuse.
    """

    url = str(product_url or "").strip()
    files = [str(value).strip() for value in product_files if str(value).strip()]
    manifest = str(product_pack_manifest or "").strip()

    if manifest:
        if files:
            raise ValueError("已解析资料包不能再同时传入原始 product files。")
        payload = load_product_pack_manifest(manifest)
        reference = str(payload.get("product_reference_url") or "").strip()
        if not url or url == reference:
            return "customer_product_pack"
        return "supplier_url"

    if url:
        return "supplier_url"
    if files:
        return "customer_product_pack"
    raise ValueError("商品输入不能为空：请输入供应商 URL，或选择客户商品资料文件。")


def _from_pack_capture(captured: ProductPackCapture) -> AcquiredProductInput:
    # Files-only Product Pack keeps its existing canonical bootstrap contract.
    return AcquiredProductInput(
        mode="customer_product_pack",
        product_reference_url=captured.product_reference_url,
        snapshot_path=captured.bootstrap_snapshot_path,
        snapshot=captured.bootstrap_snapshot,
        supplier_snapshot_paths=(captured.bootstrap_snapshot_path,),
        customer_snapshot_paths=(),
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
        supplier_snapshot_paths=(bootstrap_path,),
        customer_snapshot_paths=(),
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


def _dedupe_paths(*groups: Iterable[Path]) -> tuple[Path, ...]:
    output: list[Path] = []
    seen: set[Path] = set()
    for group in groups:
        for raw in group:
            path = Path(raw).resolve()
            if path in seen:
                continue
            seen.add(path)
            output.append(path)
    return tuple(output)


def _pack_manifest_components(
    path: str | Path,
) -> tuple[Path, SourceSnapshot, tuple[Path, ...], tuple[Path, ...], tuple[Path, ...], tuple[str, ...]]:
    manifest_path = Path(path).resolve()
    payload = load_product_pack_manifest(manifest_path)
    bootstrap_path = Path(str(payload["bootstrap_snapshot"])).resolve()
    customer_snapshots = tuple(
        Path(str(value)).resolve() for value in payload.get("customer_snapshots") or []
    )
    evidence_images = tuple(
        Path(str(value)).resolve() for value in payload.get("evidence_images") or []
    )
    listing_images = tuple(
        Path(str(value)).resolve() for value in payload.get("listing_images") or []
    )
    warnings = tuple(str(value) for value in payload.get("warnings") or [])
    return (
        manifest_path,
        source_snapshot_from_json(bootstrap_path),
        customer_snapshots,
        evidence_images,
        listing_images,
        warnings,
    )


def _combined_bootstrap_snapshot(
    supplier: SourceSnapshot,
    customer: SourceSnapshot,
    *,
    supplemental_file_count: int,
) -> SourceSnapshot:
    supplier_text = str(supplier.visible_text or "").strip()
    customer_text = str(customer.visible_text or "").strip()
    parts: list[str] = []
    if supplier_text:
        parts.append(supplier_text)
    if customer_text:
        parts.append("[Customer supplemental product files]\n" + customer_text)
    visible_text = "\n\n".join(parts)[:_COMBINED_TEXT_LIMIT]
    table_rows = [*supplier.table_rows, *customer.table_rows][:_COMBINED_TABLE_ROW_LIMIT]
    warnings = list(dict.fromkeys([*supplier.warnings, *customer.warnings]))
    return SourceSnapshot(
        requested_url=supplier.requested_url,
        final_url=supplier.final_url,
        title=supplier.title,
        captured_at=supplier.captured_at,
        visible_text=visible_text,
        table_rows=table_rows,
        json_ld=list(supplier.json_ld),
        embedded_data=list(supplier.embedded_data),
        image_urls=list(supplier.image_urls),
        meta={
            **supplier.meta,
            "input_mode": "supplier_url",
            "customer_supplement": "true",
            "supplemental_files": str(max(0, int(supplemental_file_count))),
        },
        warnings=warnings,
    )


def _from_supplier_with_customer_supplement(
    supplier: CapturedProductSource,
    requested_url: str,
    *,
    output_dir: Path,
    product_files: tuple[str | Path, ...],
    product_pack_manifest: str | Path | None,
) -> AcquiredProductInput:
    if str(product_pack_manifest or "").strip():
        (
            pack_manifest_path,
            pack_bootstrap,
            customer_snapshots,
            pack_images,
            pack_listing_images,
            pack_warnings,
        ) = _pack_manifest_components(str(product_pack_manifest))
        supplemental_file_count = len(customer_snapshots) or 1
    else:
        captured_pack = capture_product_pack(
            product_files,
            output_dir=output_dir / "product-pack",
        )
        pack_manifest_path = captured_pack.manifest_path
        pack_bootstrap = captured_pack.bootstrap_snapshot
        customer_snapshots = captured_pack.customer_snapshot_paths
        pack_images = captured_pack.evidence_image_paths
        pack_listing_images = captured_pack.listing_image_paths
        pack_warnings = captured_pack.warnings
        supplemental_file_count = len(captured_pack.stored_files)

    combined = _combined_bootstrap_snapshot(
        supplier.snapshot,
        pack_bootstrap,
        supplemental_file_count=supplemental_file_count,
    )
    combined_path = write_source_snapshot(
        combined,
        output_dir / "combined-bootstrap-source.json",
    )
    screenshot = supplier.screenshot_path if supplier.screenshot_path.is_file() else None
    reference = supplier.snapshot.final_url or requested_url
    return AcquiredProductInput(
        mode="supplier_url",
        product_reference_url=reference,
        snapshot_path=combined_path,
        snapshot=combined,
        supplier_snapshot_paths=(supplier.snapshot_path,),
        customer_snapshot_paths=tuple(customer_snapshots),
        evidence_image_paths=_dedupe_paths(supplier.product_image_paths, pack_images),
        listing_image_paths=_dedupe_paths(pack_listing_images),
        screenshot_path=screenshot,
        pack_manifest_path=pack_manifest_path,
        source_cache_hit=bool(supplier.cache_hit),
        source_edge_launched=bool(supplier.launched_now),
        warnings=tuple(dict.fromkeys([*supplier.snapshot.warnings, *pack_warnings])),
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

    Supplier URLs remain the primary source. Optional customer files are parsed as
    supplemental evidence and merged only for bootstrap understanding; Resolver
    grounding keeps the raw supplier snapshot and customer snapshots separately.
    Files-only input retains the existing Product Pack behavior.
    """

    file_values = tuple(product_files)
    manifest_value = str(product_pack_manifest or "").strip()
    mode = validate_product_input(
        product_url=product_url,
        product_files=file_values,
        product_pack_manifest=manifest_value or None,
    )
    if mode == "customer_product_pack":
        if manifest_value:
            return _from_pack_manifest(manifest_value)
        return _from_pack_capture(
            capture_product_pack(file_values, output_dir=Path(output_dir) / "product-pack")
        )

    url = str(product_url).strip()
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    has_customer_supplement = bool(file_values or manifest_value)
    supplier_output = root / "supplier-source" if has_customer_supplement else root
    captured = capture_product_source(
        url,
        output_dir=supplier_output,
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
    if not has_customer_supplement:
        return _from_supplier(captured, url)
    return _from_supplier_with_customer_supplement(
        captured,
        url,
        output_dir=root,
        product_files=file_values,
        product_pack_manifest=manifest_value or None,
    )


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
        "customer_supplement": bool(
            acquired.mode == "supplier_url" and acquired.pack_manifest_path is not None
        ),
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
