from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError


MIN_LISTING_IMAGE_WIDTH = 300
MIN_LISTING_IMAGE_HEIGHT = 300


@dataclass(slots=True, frozen=True)
class ListingImageInspection:
    path: Path
    width: int
    height: int

    @property
    def meets_resolution(self) -> bool:
        return (
            self.width >= MIN_LISTING_IMAGE_WIDTH
            and self.height >= MIN_LISTING_IMAGE_HEIGHT
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "width": self.width,
            "height": self.height,
            "minimum_width": MIN_LISTING_IMAGE_WIDTH,
            "minimum_height": MIN_LISTING_IMAGE_HEIGHT,
            "meets_resolution": self.meets_resolution,
        }


def inspect_listing_image(path: str | Path) -> ListingImageInspection:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"商品图片不存在或不是文件：{source}")
    try:
        with Image.open(source) as opened:
            image = ImageOps.exif_transpose(opened)
            width, height = (int(value) for value in image.size)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValueError(f"商品图片无法解码：{source} ({exc})") from exc
    if width <= 0 or height <= 0:
        raise ValueError(f"商品图片尺寸无效：{source} ({width}x{height})")
    return ListingImageInspection(source, width, height)


def listing_image_resolution_error(inspection: ListingImageInspection) -> str | None:
    if inspection.meets_resolution:
        return None
    return (
        "图片低于 Makro Product Photos 最低分辨率；"
        f"actual={inspection.width}x{inspection.height}, "
        f"minimum={MIN_LISTING_IMAGE_WIDTH}x{MIN_LISTING_IMAGE_HEIGHT}。"
    )


__all__ = [
    "ListingImageInspection",
    "MIN_LISTING_IMAGE_HEIGHT",
    "MIN_LISTING_IMAGE_WIDTH",
    "inspect_listing_image",
    "listing_image_resolution_error",
]
