from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .product_pack import load_product_pack_manifest
from .semantic_grounding import GroundedSource, GroundingCatalog, build_grounding_catalog


def customer_file_grounding_catalog(
    snapshots: Iterable[str | Path],
    *,
    max_text_chars: int,
    overlap_chars: int,
) -> GroundingCatalog:
    """Build citable customer-file sources with the same snapshot parser as web evidence."""

    values = [str(Path(value)) for value in snapshots]
    if not values:
        return GroundingCatalog([])
    parsed = build_grounding_catalog(
        official_snapshots=values,
        max_text_chars=max_text_chars,
        overlap_chars=overlap_chars,
    )
    converted: list[GroundedSource] = []
    for source in parsed.sources:
        source_id = source.source_id
        if source_id.startswith("official:"):
            source_id = "customer-file:" + source_id[len("official:") :]
        converted.append(
            GroundedSource(
                source_id=source_id,
                source_type="customer_file",
                kind=source.kind,
                origin=source.origin,
                content=source.content,
                image_path=source.image_path,
                sha256=source.sha256,
            )
        )
    return GroundingCatalog(converted)


def build_product_grounding_catalog(
    *,
    image_paths: Iterable[str | Path] = (),
    supplier_snapshots: Iterable[str | Path] = (),
    customer_snapshots: Iterable[str | Path] = (),
    official_snapshots: Iterable[str | Path] = (),
    max_text_chars: int = 3000,
    overlap_chars: int = 250,
) -> GroundingCatalog:
    """Build one exact source universe for URL and customer-pack product inputs."""

    regular = build_grounding_catalog(
        image_paths=[str(Path(value)) for value in image_paths],
        supplier_snapshots=[str(Path(value)) for value in supplier_snapshots],
        official_snapshots=[str(Path(value)) for value in official_snapshots],
        max_text_chars=max_text_chars,
        overlap_chars=overlap_chars,
    )
    customer = customer_file_grounding_catalog(
        customer_snapshots,
        max_text_chars=max_text_chars,
        overlap_chars=overlap_chars,
    )
    return GroundingCatalog([*regular.sources, *customer.sources])


def build_rebind_grounding_catalog(
    *,
    product_pack_manifest: str | Path | None = None,
    image_paths: Iterable[str | Path] = (),
    supplier_snapshots: Iterable[str | Path] = (),
    official_snapshots: Iterable[str | Path] = (),
    max_text_chars: int = 3000,
    overlap_chars: int = 250,
) -> GroundingCatalog:
    """Rebuild exactly the resolver source universe for planner/executor validation.

    Product-pack mode is authoritative: its persisted customer snapshots replace
    legacy supplier snapshots, while ``image_paths`` must be the exact Resolver
    evidence images. URL mode retains the established supplier/official sources.
    """

    manifest_value = str(product_pack_manifest or "").strip()
    if manifest_value:
        manifest = load_product_pack_manifest(manifest_value)
        return build_product_grounding_catalog(
            image_paths=image_paths,
            customer_snapshots=manifest.get("customer_snapshots") or [],
            max_text_chars=max_text_chars,
            overlap_chars=overlap_chars,
        )
    return build_product_grounding_catalog(
        image_paths=image_paths,
        supplier_snapshots=supplier_snapshots,
        official_snapshots=official_snapshots,
        max_text_chars=max_text_chars,
        overlap_chars=overlap_chars,
    )


__all__ = [
    "build_product_grounding_catalog",
    "build_rebind_grounding_catalog",
    "customer_file_grounding_catalog",
]
