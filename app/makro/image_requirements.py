from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops, ImageOps, UnidentifiedImageError


MIN_LISTING_IMAGE_WIDTH = 300
MIN_LISTING_IMAGE_HEIGHT = 300
MIN_LISTING_CONTENT_WIDTH = 240
MIN_LISTING_CONTENT_HEIGHT = 240
_NON_WHITE_THRESHOLD = 12
_VISIBLE_ALPHA_THRESHOLD = 16


@dataclass(slots=True, frozen=True)
class ListingImageInspection:
    path: Path
    width: int
    height: int
    content_width: int = 0
    content_height: int = 0
    content_bbox: tuple[int, int, int, int] | None = None

    @property
    def meets_resolution(self) -> bool:
        return (
            self.width >= MIN_LISTING_IMAGE_WIDTH
            and self.height >= MIN_LISTING_IMAGE_HEIGHT
        )

    @property
    def meets_content_coverage(self) -> bool:
        return (
            self.content_width >= MIN_LISTING_CONTENT_WIDTH
            and self.content_height >= MIN_LISTING_CONTENT_HEIGHT
        )

    @property
    def meets_cms_requirements(self) -> bool:
        return self.meets_resolution and self.meets_content_coverage

    def as_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "width": self.width,
            "height": self.height,
            "minimum_width": MIN_LISTING_IMAGE_WIDTH,
            "minimum_height": MIN_LISTING_IMAGE_HEIGHT,
            "meets_resolution": self.meets_resolution,
            "content_width": self.content_width,
            "content_height": self.content_height,
            "content_bbox": list(self.content_bbox) if self.content_bbox else None,
            "minimum_content_width": MIN_LISTING_CONTENT_WIDTH,
            "minimum_content_height": MIN_LISTING_CONTENT_HEIGHT,
            "meets_content_coverage": self.meets_content_coverage,
            "meets_cms_requirements": self.meets_cms_requirements,
        }


def _non_blank_bbox(image: Image.Image) -> tuple[int, int, int, int] | None:
    """Return the visible non-white content bounds used by Makro's CMS preflight.

    Makro documents image coverage as excluding blank/white area. JPEG compression
    can introduce tiny off-white noise around an otherwise white margin, so use a
    small difference threshold instead of treating every non-255 pixel as content.
    Transparent pixels are always excluded from the visible-content mask.
    """

    rgba = image.convert("RGBA")
    rgb = rgba.convert("RGB")
    white = Image.new("RGB", rgb.size, (255, 255, 255))
    difference = ImageChops.difference(rgb, white).convert("L")
    non_white = difference.point(
        lambda value: 255 if value >= _NON_WHITE_THRESHOLD else 0,
        mode="1",
    ).convert("L")

    alpha = rgba.getchannel("A")
    alpha_min, _alpha_max = alpha.getextrema()
    if alpha_min < 255:
        visible_alpha = alpha.point(
            lambda value: 255 if value > _VISIBLE_ALPHA_THRESHOLD else 0,
            mode="1",
        ).convert("L")
        non_white = ImageChops.multiply(non_white, visible_alpha)

    return non_white.getbbox()


def inspect_listing_image(path: str | Path) -> ListingImageInspection:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"商品图片不存在或不是文件：{source}")
    try:
        with Image.open(source) as opened:
            image = ImageOps.exif_transpose(opened)
            image.load()
            width, height = (int(value) for value in image.size)
            bbox = _non_blank_bbox(image)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValueError(f"商品图片无法解码：{source} ({exc})") from exc
    if width <= 0 or height <= 0:
        raise ValueError(f"商品图片尺寸无效：{source} ({width}x{height})")

    if bbox is None:
        content_width = 0
        content_height = 0
    else:
        left, top, right, bottom = bbox
        content_width = max(0, int(right) - int(left))
        content_height = max(0, int(bottom) - int(top))

    return ListingImageInspection(
        source,
        width,
        height,
        content_width=content_width,
        content_height=content_height,
        content_bbox=bbox,
    )


def listing_image_resolution_error(inspection: ListingImageInspection) -> str | None:
    if inspection.meets_resolution:
        return None
    return (
        "图片低于 Makro Product Photos 最低分辨率；"
        f"actual={inspection.width}x{inspection.height}, "
        f"minimum={MIN_LISTING_IMAGE_WIDTH}x{MIN_LISTING_IMAGE_HEIGHT}。"
    )


def listing_image_coverage_error(inspection: ListingImageInspection) -> str | None:
    if inspection.meets_content_coverage:
        return None
    return (
        "图片有效内容覆盖面积低于 Makro CMS 要求（已排除白色/透明空白区域）；"
        f"content={inspection.content_width}x{inspection.content_height}, "
        f"minimum={MIN_LISTING_CONTENT_WIDTH}x{MIN_LISTING_CONTENT_HEIGHT}, "
        f"image={inspection.width}x{inspection.height}。"
    )


def listing_image_cms_error(inspection: ListingImageInspection) -> str | None:
    return listing_image_resolution_error(inspection) or listing_image_coverage_error(inspection)


__all__ = [
    "ListingImageInspection",
    "MIN_LISTING_CONTENT_HEIGHT",
    "MIN_LISTING_CONTENT_WIDTH",
    "MIN_LISTING_IMAGE_HEIGHT",
    "MIN_LISTING_IMAGE_WIDTH",
    "inspect_listing_image",
    "listing_image_cms_error",
    "listing_image_coverage_error",
    "listing_image_resolution_error",
]
