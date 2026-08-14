from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, UnidentifiedImageError


LISTING_IMAGE_POLICY_VERSION = 1
MIN_LISTING_IMAGE_SHORT_EDGE = 160
MIN_LISTING_IMAGE_PIXEL_AREA = 80_000
MAX_LISTING_IMAGE_ASPECT_RATIO = 4.0


@dataclass(slots=True, frozen=True)
class ListingImageAssessment:
    """Mechanical suitability result for one captured source image."""

    path: Path
    source_index: int
    eligible: bool
    width: int | None
    height: int | None
    pixel_area: int | None
    aspect_ratio: float | None
    sha256: str
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "source_index": self.source_index,
            "eligible": self.eligible,
            "width": self.width,
            "height": self.height,
            "pixel_area": self.pixel_area,
            "aspect_ratio": round(self.aspect_ratio, 4) if self.aspect_ratio is not None else None,
            "sha256": self.sha256,
            "reasons": list(self.reasons),
        }


@dataclass(slots=True, frozen=True)
class ListingImageSelection:
    """Derived Listing Photos candidates; raw source evidence remains untouched."""

    selected: tuple[Path, ...]
    assessments: tuple[ListingImageAssessment, ...]

    @property
    def rejected_count(self) -> int:
        return sum(1 for item in self.assessments if not item.eligible)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "policy": {
                "version": LISTING_IMAGE_POLICY_VERSION,
                "kind": "mechanical-listing-image-quality-gate",
                "min_short_edge": MIN_LISTING_IMAGE_SHORT_EDGE,
                "min_pixel_area": MIN_LISTING_IMAGE_PIXEL_AREA,
                "max_aspect_ratio": MAX_LISTING_IMAGE_ASPECT_RATIO,
                "dedupe": "exact-content-sha256",
                "ordering": "preserve-source-order",
            },
            "selected": [str(path) for path in self.selected],
            "selected_count": len(self.selected),
            "rejected_count": self.rejected_count,
            "assessments": [item.as_dict() for item in self.assessments],
        }


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _assessment_for_path(
    raw: str | Path,
    *,
    source_index: int,
    seen_hashes: set[str],
) -> ListingImageAssessment:
    path = Path(raw).expanduser().resolve()
    if not path.is_file():
        return ListingImageAssessment(
            path=path,
            source_index=source_index,
            eligible=False,
            width=None,
            height=None,
            pixel_area=None,
            aspect_ratio=None,
            sha256="",
            reasons=("missing_file",),
        )

    try:
        with Image.open(path) as opened:
            width, height = (int(opened.width), int(opened.height))
            opened.verify()
    except (UnidentifiedImageError, OSError, ValueError):
        return ListingImageAssessment(
            path=path,
            source_index=source_index,
            eligible=False,
            width=None,
            height=None,
            pixel_area=None,
            aspect_ratio=None,
            sha256="",
            reasons=("decode_error",),
        )

    if width <= 0 or height <= 0:
        return ListingImageAssessment(
            path=path,
            source_index=source_index,
            eligible=False,
            width=width,
            height=height,
            pixel_area=0,
            aspect_ratio=None,
            sha256="",
            reasons=("invalid_dimensions",),
        )

    sha256 = _file_sha256(path)
    pixel_area = width * height
    short_edge = min(width, height)
    aspect_ratio = max(width, height) / short_edge
    reasons: list[str] = []

    if sha256 in seen_hashes:
        reasons.append("duplicate_content")
    if short_edge < MIN_LISTING_IMAGE_SHORT_EDGE:
        reasons.append(f"short_edge<{MIN_LISTING_IMAGE_SHORT_EDGE}")
    if pixel_area < MIN_LISTING_IMAGE_PIXEL_AREA:
        reasons.append(f"pixel_area<{MIN_LISTING_IMAGE_PIXEL_AREA}")
    if aspect_ratio > MAX_LISTING_IMAGE_ASPECT_RATIO:
        reasons.append(f"aspect_ratio>{MAX_LISTING_IMAGE_ASPECT_RATIO:g}")

    eligible = not reasons
    if eligible:
        seen_hashes.add(sha256)

    return ListingImageAssessment(
        path=path,
        source_index=source_index,
        eligible=eligible,
        width=width,
        height=height,
        pixel_area=pixel_area,
        aspect_ratio=aspect_ratio,
        sha256=sha256,
        reasons=tuple(reasons),
    )


def select_listing_images(image_paths: Iterable[str | Path]) -> ListingImageSelection:
    """Select upload-safe Listing Photos without changing the evidence universe.

    This gate is deliberately product/category agnostic. It checks only file
    decodability, exact duplicates and geometry that distinguishes useful image
    surfaces from strips/banners/sprites. Input order is preserved so Python does
    not invent product-image semantics or ranking.
    """

    assessments: list[ListingImageAssessment] = []
    selected: list[Path] = []
    seen_hashes: set[str] = set()

    for source_index, raw in enumerate(image_paths, start=1):
        assessment = _assessment_for_path(
            raw,
            source_index=source_index,
            seen_hashes=seen_hashes,
        )
        assessments.append(assessment)
        if assessment.eligible:
            selected.append(assessment.path)

    return ListingImageSelection(
        selected=tuple(selected),
        assessments=tuple(assessments),
    )


def listing_images_from_resolver_outputs(outputs: dict[str, Any]) -> tuple[Path, ...]:
    """Return the canonical auto-upload image set for new and legacy Resolver runs.

    New manifests publish ``primary_source_listing_images`` explicitly. Legacy
    manifests only have raw evidence images, so the same quality gate is applied
    at consumption time. An explicit empty curated list remains empty and never
    falls back to raw evidence or a page screenshot.
    """

    if "primary_source_listing_images" in outputs:
        values = outputs.get("primary_source_listing_images") or []
    else:
        values = outputs.get("primary_source_product_images") or []
    return select_listing_images(values).selected


def write_listing_image_selection(
    selection: ListingImageSelection,
    path: str | Path,
) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(selection.as_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return target


__all__ = [
    "LISTING_IMAGE_POLICY_VERSION",
    "MAX_LISTING_IMAGE_ASPECT_RATIO",
    "MIN_LISTING_IMAGE_PIXEL_AREA",
    "MIN_LISTING_IMAGE_SHORT_EDGE",
    "ListingImageAssessment",
    "ListingImageSelection",
    "listing_images_from_resolver_outputs",
    "select_listing_images",
    "write_listing_image_selection",
]
