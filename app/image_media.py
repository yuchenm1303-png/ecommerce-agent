from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class ImageMediaError(ValueError):
    """Raised when bytes do not establish a supported raster image media type."""


@dataclass(slots=True, frozen=True)
class ImageMediaType:
    mime_type: str
    extension: str
    format_name: str


_JPEG = ImageMediaType("image/jpeg", ".jpg", "JPEG")
_PNG = ImageMediaType("image/png", ".png", "PNG")
_GIF = ImageMediaType("image/gif", ".gif", "GIF")
_WEBP = ImageMediaType("image/webp", ".webp", "WEBP")
_AVIF = ImageMediaType("image/avif", ".avif", "AVIF")


def detect_image_media(data: bytes) -> ImageMediaType | None:
    """Identify supported image media from bytes only, never filename metadata.

    Supplier URLs, response Content-Type headers and local suffixes are advisory at
    best. The transport contract is established from the payload itself so opaque
    files such as ``.img`` cannot lose their real MIME identity downstream.
    """

    payload = bytes(data or b"")
    if payload.startswith(b"\xff\xd8\xff"):
        return _JPEG
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return _PNG
    if payload.startswith((b"GIF87a", b"GIF89a")):
        return _GIF
    if len(payload) >= 12 and payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return _WEBP

    # AVIF is an ISO-BMFF family. Major/compatible brands live in the ftyp box.
    if len(payload) >= 16 and payload[4:8] == b"ftyp":
        brands = payload[8:64]
        if b"avif" in brands or b"avis" in brands:
            return _AVIF
    return None


def require_image_media(data: bytes, *, source: str = "image payload") -> ImageMediaType:
    media = detect_image_media(data)
    if media is None:
        raise ImageMediaError(f"unsupported or unrecognized image media: {source}")
    return media


def read_image_media(path_value: str | Path) -> tuple[bytes, ImageMediaType]:
    path = Path(path_value)
    if not path.is_file():
        raise ImageMediaError(f"image file does not exist: {path}")
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ImageMediaError(f"unable to read image file: {path}") from exc
    return data, require_image_media(data, source=path.name)


__all__ = [
    "ImageMediaError",
    "ImageMediaType",
    "detect_image_media",
    "read_image_media",
    "require_image_media",
]
