from __future__ import annotations

import math
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError


class ImageMediaError(ValueError):
    """Raised when an image cannot be converted into the provider transport format."""


@dataclass(slots=True, frozen=True)
class ImageMediaType:
    mime_type: str
    extension: str
    format_name: str


_JPEG = ImageMediaType("image/jpeg", ".jpg", "JPEG")
_DEFAULT_MIN_DIMENSION = 11
_DEFAULT_MAX_LONG_EDGE = 2048
_DEFAULT_MAX_PIXEL_AREA = 4_000_000
_DEFAULT_MAX_BYTES = 4 * 1024 * 1024
_DEFAULT_JPEG_QUALITY = 88


def has_decodable_image_pixels(path_value: str | Path) -> bool:
    """Return whether a local artifact contains pixels accepted by AI transport.

    This is a transport-boundary check only. It does not classify the image, rank it,
    inspect product meaning, or decide whether the image is useful evidence. Files
    that cannot produce a provider-valid raster cannot enter multimodal requests.
    """

    path = Path(path_value)
    if not path.is_file():
        return False
    try:
        with Image.open(path) as opened:
            if (
                int(opened.width) < _DEFAULT_MIN_DIMENSION
                or int(opened.height) < _DEFAULT_MIN_DIMENSION
            ):
                return False
            opened.load()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        return False
    return True


def normalize_image_media(
    data: bytes,
    *,
    source: str = "image payload",
    min_dimension: int = _DEFAULT_MIN_DIMENSION,
    max_long_edge: int = _DEFAULT_MAX_LONG_EDGE,
    max_pixel_area: int = _DEFAULT_MAX_PIXEL_AREA,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    jpeg_quality: int = _DEFAULT_JPEG_QUALITY,
) -> tuple[bytes, ImageMediaType]:
    """Decode arbitrary raster bytes and emit one bounded provider-valid JPEG.

    Filenames, URL suffixes, response Content-Type headers and handwritten magic-byte
    tables are not evidence about what a product image means. Pillow is used only as
    the technical decoder. Any technically valid raster is normalized to the one
    image format sent to the multimodal provider; semantic usefulness remains
    entirely the model's responsibility.

    Provider transport has a mechanical minimum of 11 pixels on each edge. Images
    below that boundary, or extreme aspect ratios that would fall below it after
    bounded downscaling, are rejected here rather than being allowed to poison an
    otherwise valid multimodal batch. We never upscale tiny evidence to manufacture
    pixels that were not present in the supplier artifact.
    """

    payload = bytes(data or b"")
    if not payload:
        raise ImageMediaError(f"empty image payload: {source}")
    if min_dimension <= 0 or max_long_edge <= 0 or max_pixel_area <= 0 or max_bytes <= 0:
        raise ValueError("image transport bounds must be positive")
    if not 1 <= int(jpeg_quality) <= 95:
        raise ValueError("jpeg_quality must be in 1..95")

    try:
        with Image.open(BytesIO(payload)) as opened:
            frame = ImageOps.exif_transpose(opened)
            width, height = int(frame.width), int(frame.height)
            if width < int(min_dimension) or height < int(min_dimension):
                raise ImageMediaError(
                    f"image dimensions {width}x{height} are below provider minimum "
                    f"{int(min_dimension)}x{int(min_dimension)}: {source}"
                )
            frame.load()
    except ImageMediaError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ImageMediaError(f"image payload is not technically decodable: {source}") from exc

    scale = min(
        1.0,
        float(max_long_edge) / max(width, height),
        math.sqrt(float(max_pixel_area) / (width * height)),
    )
    target_size = (
        max(1, int(round(width * scale))),
        max(1, int(round(height * scale))),
    )
    if min(target_size) < int(min_dimension):
        raise ImageMediaError(
            f"normalized image dimensions {target_size[0]}x{target_size[1]} would be below "
            f"provider minimum {int(min_dimension)}x{int(min_dimension)}: {source}"
        )
    if target_size != frame.size:
        resampling = getattr(Image, "Resampling", Image).LANCZOS
        frame = frame.resize(target_size, resampling)

    has_alpha = "A" in frame.getbands() or (
        frame.mode == "P" and "transparency" in frame.info
    )
    if has_alpha:
        rgba = frame.convert("RGBA")
        flattened = Image.new("RGB", rgba.size, "white")
        flattened.paste(rgba, mask=rgba.getchannel("A"))
        frame = flattened
    else:
        frame = frame.convert("RGB")

    quality = int(jpeg_quality)

    def encode() -> bytes:
        buffer = BytesIO()
        frame.save(buffer, format="JPEG", quality=quality, optimize=True)
        return buffer.getvalue()

    encoded = encode()
    while len(encoded) > max_bytes and max(frame.size) > 768:
        next_size = (
            max(1, int(round(frame.width * 0.82))),
            max(1, int(round(frame.height * 0.82))),
        )
        if min(next_size) < int(min_dimension):
            raise ImageMediaError(
                f"image cannot meet byte budget without dropping below provider minimum "
                f"{int(min_dimension)}x{int(min_dimension)}: {source}"
            )
        resampling = getattr(Image, "Resampling", Image).LANCZOS
        frame = frame.resize(next_size, resampling)
        quality = max(68, quality - 5)
        encoded = encode()

    if len(encoded) > max_bytes:
        raise ImageMediaError(
            f"canonical image exceeds {max_bytes} byte transport budget: {source}"
        )
    return encoded, _JPEG


def detect_image_media(data: bytes) -> ImageMediaType | None:
    """Compatibility helper: report transportability, not source-file semantics."""

    try:
        _encoded, media = normalize_image_media(data)
    except ImageMediaError:
        return None
    return media


def require_image_media(data: bytes, *, source: str = "image payload") -> ImageMediaType:
    """Compatibility helper for callers that only need the normalized media type."""

    _encoded, media = normalize_image_media(data, source=source)
    return media


def read_image_media(path_value: str | Path) -> tuple[bytes, ImageMediaType]:
    """Read and normalize a local image for multimodal provider transport."""

    path = Path(path_value)
    if not path.is_file():
        raise ImageMediaError(f"image file does not exist: {path}")
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ImageMediaError(f"unable to read image file: {path}") from exc
    return normalize_image_media(data, source=path.name)


__all__ = [
    "ImageMediaError",
    "ImageMediaType",
    "detect_image_media",
    "has_decodable_image_pixels",
    "normalize_image_media",
    "read_image_media",
    "require_image_media",
]
