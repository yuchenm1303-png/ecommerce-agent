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


def build_icon(output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(io.BytesIO(application_icon_bytes())) as source:
        source.convert("RGBA").save(output, format="ICO", sizes=ICON_SIZES)
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
