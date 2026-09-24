from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PIL import Image

from app.app_branding import application_icon_bytes


ICON_SIZES = (
    (16, 16),
    (24, 24),
    (32, 32),
    (48, 48),
    (64, 64),
    (128, 128),
    (256, 256),
)
ICON_VISUAL_SCALE = 1.12
ICON_MASTER_SIZE = max(width for width, _height in ICON_SIZES)


def _enlarge_icon_artwork(source: Image.Image) -> Image.Image:
    rgba = source.convert("RGBA")
    width, height = rgba.size
    scaled = rgba.resize(
        (round(width * ICON_VISUAL_SCALE), round(height * ICON_VISUAL_SCALE)),
        Image.Resampling.LANCZOS,
    )
    left = max(0, (scaled.width - width) // 2)
    top = max(0, (scaled.height - height) // 2)
    return scaled.crop((left, top, left + width, top + height))


def _icon_master(source: Image.Image) -> Image.Image:
    """Return a 256px master so Pillow can actually emit every requested ICO frame."""

    artwork = _enlarge_icon_artwork(source)
    if artwork.width != artwork.height:
        raise RuntimeError(f"Application icon source must be square, got {artwork.size}")
    if artwork.width == ICON_MASTER_SIZE:
        return artwork
    return artwork.resize(
        (ICON_MASTER_SIZE, ICON_MASTER_SIZE),
        Image.Resampling.LANCZOS,
    )


def _validate_icon(output: Path) -> None:
    with Image.open(output) as generated:
        generated_sizes = set(generated.info.get("sizes") or ())
    expected_sizes = set(ICON_SIZES)
    if generated_sizes != expected_sizes:
        missing = sorted(expected_sizes - generated_sizes)
        unexpected = sorted(generated_sizes - expected_sizes)
        raise RuntimeError(
            "Generated Windows ICO frame set is invalid: "
            f"missing={missing}, unexpected={unexpected}, actual={sorted(generated_sizes)}"
        )


def build_icon(output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(io.BytesIO(application_icon_bytes())) as source:
        _icon_master(source).save(output, format="ICO", sizes=ICON_SIZES)
    _validate_icon(output)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the Windows EcommerceAgent icon")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "packaging" / "app_icon.ico",
    )
    args = parser.parse_args()
    path = build_icon(args.output.resolve())
    print(f"Generated application icon: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
