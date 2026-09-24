from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, Iterable

from app.source_capture import (
    DEFAULT_SOURCE_CDP_PORT,
    SourceAccessBlocked,
    capture_product_source,
    validate_source_url,
)

from .core import CASE_SCHEMA_VERSION, ImageSelectionLabError


CAPTURE_SCHEMA_VERSION = 1


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_ld_products(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, list):
        for item in value:
            yield from _json_ld_products(item)
        return
    if not isinstance(value, dict):
        return

    raw_type = value.get("@type")
    types = raw_type if isinstance(raw_type, list) else [raw_type]
    if any(str(item or "").strip().casefold() == "product" for item in types):
        yield value

    for child in value.values():
        if isinstance(child, (dict, list)):
            yield from _json_ld_products(child)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _brand_text(value: Any) -> str:
    if isinstance(value, dict):
        return _text(value.get("name") or value.get("brand"))
    if isinstance(value, list):
        for item in value:
            brand = _brand_text(item)
            if brand:
                return brand
        return ""
    return _text(value)


def build_captured_target_product(
    snapshot: Any,
    *,
    target_name: str = "",
    target_brand: str = "",
    target_model: str = "",
    target_variant: str = "",
) -> dict[str, Any]:
    """Build only mechanically grounded target identity for an unlabelled case.

    Explicit CLI fields win because they are human-provided target facts. Otherwise
    JSON-LD Product fields are copied verbatim, with the page title used only as a
    final name fallback. This function never infers a SKU/model from title tokens.
    """

    products = list(_json_ld_products(getattr(snapshot, "json_ld", None)))
    product = products[0] if products else {}
    identity: dict[str, Any] = {}

    name = _text(target_name) or _text(product.get("name")) or _text(getattr(snapshot, "title", ""))
    brand = _text(target_brand) or _brand_text(product.get("brand"))
    model = _text(target_model) or _text(product.get("model"))
    variant = _text(target_variant)

    if name:
        identity["name"] = name
    if brand:
        identity["brand"] = brand
    if model:
        identity["model"] = model
    if variant:
        identity["variant"] = variant

    for source_key, target_key in (
        ("sku", "sku"),
        ("mpn", "mpn"),
        ("gtin", "gtin"),
        ("gtin8", "gtin8"),
        ("gtin12", "gtin12"),
        ("gtin13", "gtin13"),
        ("gtin14", "gtin14"),
        ("productID", "product_id"),
    ):
        value = _text(product.get(source_key))
        if value:
            identity[target_key] = value

    identity["identity_source"] = (
        "human_override+json_ld" if any((target_name, target_brand, target_model, target_variant)) and product
        else "human_override" if any((target_name, target_brand, target_model, target_variant))
        else "json_ld_product" if product
        else "page_title"
    )
    if not any(_text(value) for key, value in identity.items() if key != "identity_source"):
        raise ImageSelectionLabError(
            "captured page has no mechanically grounded target identity; provide --target-name"
        )
    return identity


def _relative_or_name(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


def capture_case_from_url(
    *,
    url: str,
    cases_root: str | Path,
    case_id: str,
    profile_dir: str | Path = "browser_profiles/source-edge",
    cdp_port: int = DEFAULT_SOURCE_CDP_PORT,
    initial_wait_ms: int = 1800,
    scroll_wait_ms: int = 180,
    max_scroll_steps: int = 120,
    max_visible_text_chars: int = 120_000,
    use_current_page: bool = False,
    target_name: str = "",
    target_brand: str = "",
    target_model: str = "",
    target_variant: str = "",
) -> Path:
    """Capture a real product page into an unlabelled local benchmark case.

    Acquisition deliberately reuses the production source-capture boundary. It
    never constructs a semantic provider and never invokes image ownership/gallery
    AI. The raw capture remains inside the local, gitignored case directory.
    """

    source_url = validate_source_url(url)
    normalized_case_id = str(case_id or "").strip()
    if not normalized_case_id:
        raise ImageSelectionLabError("case_id is required")
    if any(part in normalized_case_id for part in ("/", "\\", "..")):
        raise ImageSelectionLabError("case_id must be one local directory name")

    destination = Path(cases_root).expanduser().resolve() / normalized_case_id
    if destination.exists():
        raise ImageSelectionLabError(f"case destination already exists: {destination}")

    raw_dir = destination / "capture_raw"
    images_dir = destination / "images"
    destination.mkdir(parents=True, exist_ok=False)
    images_dir.mkdir(parents=True, exist_ok=False)

    try:
        try:
            captured = capture_product_source(
                source_url,
                output_dir=raw_dir,
                profile_dir=profile_dir,
                cdp_port=int(cdp_port),
                initial_wait_ms=int(initial_wait_ms),
                scroll_wait_ms=int(scroll_wait_ms),
                max_scroll_steps=int(max_scroll_steps),
                max_visible_text_chars=int(max_visible_text_chars),
                use_current_page=bool(use_current_page),
                cache_dir=None,
                cache_ttl_seconds=0,
                force_refresh=True,
            )
        except SourceAccessBlocked as exc:
            raise ImageSelectionLabError(
                "source page requires normal manual verification; complete it in the dedicated "
                "Source Edge and rerun capture-case with --use-current-page"
            ) from exc

        snapshot = captured.snapshot
        target_product = build_captured_target_product(
            snapshot,
            target_name=target_name,
            target_brand=target_brand,
            target_model=target_model,
            target_variant=target_variant,
        )

        candidates: list[dict[str, Any]] = []
        seen_hashes: set[str] = set()
        duplicate_count = 0
        missing_count = 0
        for source_index, source_path_raw in enumerate(captured.product_image_paths, start=1):
            source_path = Path(source_path_raw)
            if not source_path.is_file():
                missing_count += 1
                continue
            digest = _sha256_file(source_path)
            if digest in seen_hashes:
                duplicate_count += 1
                continue
            seen_hashes.add(digest)
            image_id = f"img_{len(candidates) + 1:03d}"
            suffix = source_path.suffix.lower() or ".jpg"
            target_path = images_dir / f"{image_id}{suffix}"
            shutil.copy2(source_path, target_path)
            candidates.append(
                {
                    "image_id": image_id,
                    "file": f"images/{target_path.name}",
                    "ground_truth": None,
                    "capture": {
                        "source_index": source_index,
                        "sha256": digest,
                        "source_file": source_path.name,
                    },
                }
            )

        if not candidates:
            raise ImageSelectionLabError(
                "production source capture completed but produced no usable product-image candidates"
            )

        capture_payload = {
            "schema_version": CAPTURE_SCHEMA_VERSION,
            "capture_mode": "production_source_capture",
            "source_url": source_url,
            "final_url": _text(getattr(snapshot, "final_url", "")),
            "page_title": _text(getattr(snapshot, "title", "")),
            "captured_product_image_count": len(captured.product_image_paths),
            "unique_candidate_count": len(candidates),
            "exact_duplicate_count": duplicate_count,
            "missing_candidate_file_count": missing_count,
            "json_ld_block_count": len(getattr(snapshot, "json_ld", None) or []),
            "snapshot_file": _relative_or_name(Path(captured.snapshot_path), destination),
            "screenshot_file": _relative_or_name(Path(captured.screenshot_path), destination),
            "source_edge_launched_now": bool(captured.launched_now),
            "source_cache_hit": bool(captured.cache_hit),
            "ai_called": False,
            "listing_action_performed": False,
        }
        _write_json(destination / "capture.json", capture_payload)
        _write_json(
            destination / "case.json",
            {
                "schema_version": CASE_SCHEMA_VERSION,
                "case_id": normalized_case_id,
                "suite": ["real_capture", "hard_negative"],
                "label_status": "needs_review",
                "source_url": source_url,
                "target_product": target_product,
                "capture": {
                    "file": "capture.json",
                    "candidate_count": len(candidates),
                    "ai_called": False,
                },
                "candidates": candidates,
            },
        )
        return destination / "case.json"
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise


__all__ = [
    "CAPTURE_SCHEMA_VERSION",
    "build_captured_target_product",
    "capture_case_from_url",
]
