from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw, ImageFilter, ImageFont

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


def _font(size: int, *, semibold: bool = False):
    candidates = (
        "C:/Windows/Fonts/seguisb.ttf" if semibold else "C:/Windows/Fonts/segoeui.ttf",
        "Segoe UI Semibold" if semibold else "Segoe UI",
        "arialbd.ttf" if semibold else "arial.ttf",
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


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


def _canonical_icon() -> Image.Image:
    with Image.open(io.BytesIO(application_icon_bytes())) as source:
        return _enlarge_icon_artwork(source).convert("RGBA")


def build_icon(output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    _canonical_icon().save(output, format="ICO", sizes=ICON_SIZES)
    return output


def _vertical_gradient(size: tuple[int, int], top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    width, height = size
    image = Image.new("RGB", size, top)
    pixels = image.load()
    for y in range(height):
        ratio = y / max(1, height - 1)
        color = tuple(round(top[i] * (1.0 - ratio) + bottom[i] * ratio) for i in range(3))
        for x in range(width):
            pixels[x, y] = color
    return image


def _glass_card(base: Image.Image, box: tuple[int, int, int, int], *, radius: int) -> None:
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rounded_rectangle(box, radius=radius, fill=(255, 255, 255, 17), outline=(255, 255, 255, 33), width=1)
    base.alpha_composite(overlay)


def build_installer_splash(output: Path, *, version: str) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    size = (760, 440)
    background = _vertical_gradient(size, (13, 18, 30), (24, 31, 50)).convert("RGBA")

    glow = Image.new("RGBA", size, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse((440, -160, 900, 300), fill=(76, 139, 255, 78))
    glow_draw.ellipse((-180, 220, 250, 650), fill=(142, 91, 255, 45))
    glow = glow.filter(ImageFilter.GaussianBlur(70))
    background.alpha_composite(glow)

    _glass_card(background, (42, 38, 718, 386), radius=28)
    icon = _canonical_icon().resize((92, 92), Image.Resampling.LANCZOS)
    background.alpha_composite(icon, (72, 72))

    draw = ImageDraw.Draw(background)
    draw.text((188, 76), "Listing Studio", font=_font(34, semibold=True), fill=(247, 249, 255, 255))
    draw.text((190, 123), f"Version {version}", font=_font(15), fill=(183, 195, 220, 255))
    draw.text((72, 204), "Ready for your next listing run.", font=_font(22, semibold=True), fill=(236, 241, 252, 255))
    draw.text(
        (72, 243),
        "Installing the secure desktop workspace and update service…",
        font=_font(15),
        fill=(173, 187, 216, 255),
    )
    draw.rounded_rectangle((72, 318, 688, 322), radius=2, fill=(255, 255, 255, 28))
    draw.text((72, 344), "Smirel  ·  Listing Studio", font=_font(13, semibold=True), fill=(151, 167, 198, 255))

    background.convert("RGB").save(output, format="PNG", optimize=True)
    return output


def build_msi_banner(output: Path, *, version: str) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    size = (493, 58)
    image = _vertical_gradient(size, (16, 22, 36), (29, 38, 62)).convert("RGBA")
    icon = _canonical_icon().resize((38, 38), Image.Resampling.LANCZOS)
    image.alpha_composite(icon, (18, 10))
    draw = ImageDraw.Draw(image)
    draw.text((68, 10), "Listing Studio", font=_font(17, semibold=True), fill=(246, 249, 255, 255))
    draw.text((69, 33), f"v{version}  ·  Smirel", font=_font(10), fill=(175, 190, 220, 255))
    image.convert("RGB").save(output, format="BMP")
    return output


def build_msi_logo(output: Path, *, version: str) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    size = (493, 312)
    image = _vertical_gradient(size, (14, 19, 31), (26, 34, 55)).convert("RGBA")

    glow = Image.new("RGBA", size, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse((250, -80, 610, 270), fill=(69, 132, 255, 76))
    glow = glow.filter(ImageFilter.GaussianBlur(56))
    image.alpha_composite(glow)

    _glass_card(image, (28, 28, 465, 284), radius=22)
    icon = _canonical_icon().resize((76, 76), Image.Resampling.LANCZOS)
    image.alpha_composite(icon, (54, 58))
    draw = ImageDraw.Draw(image)
    draw.text((154, 64), "Listing Studio", font=_font(29, semibold=True), fill=(247, 249, 255, 255))
    draw.text((156, 105), f"Version {version}", font=_font(13), fill=(179, 193, 222, 255))
    draw.text((54, 174), "Install once. Update quietly.", font=_font(20, semibold=True), fill=(232, 238, 251, 255))
    draw.text((54, 210), "A polished desktop workflow by Smirel.", font=_font(13), fill=(164, 179, 209, 255))
    draw.text((54, 254), "SMIREL", font=_font(11, semibold=True), fill=(138, 157, 194, 255))
    image.convert("RGB").save(output, format="BMP")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Listing Studio Windows branding assets")
    parser.add_argument("--output", type=Path, default=ROOT / "packaging" / "app_icon.ico")
    parser.add_argument("--splash", type=Path, default=None)
    parser.add_argument("--msi-banner", type=Path, default=None)
    parser.add_argument("--msi-logo", type=Path, default=None)
    parser.add_argument("--version", default="")
    args = parser.parse_args()

    icon_path = build_icon(args.output.resolve())
    print(f"Generated application icon: {icon_path}")

    version = str(args.version or "").strip().lstrip("v") or "latest"
    if args.splash is not None:
        path = build_installer_splash(args.splash.resolve(), version=version)
        print(f"Generated installer splash: {path}")
    if args.msi_banner is not None:
        path = build_msi_banner(args.msi_banner.resolve(), version=version)
        print(f"Generated MSI banner: {path}")
    if args.msi_logo is not None:
        path = build_msi_logo(args.msi_logo.resolve(), version=version)
        print(f"Generated MSI logo: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
