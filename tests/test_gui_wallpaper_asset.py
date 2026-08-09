from __future__ import annotations

import base64
from pathlib import Path


ASSET = (
    Path(__file__).resolve().parents[1]
    / "gui"
    / "assets"
    / "fuji_sakura_wallpaper.jpg.b64"
)


def test_gui_wallpaper_asset_is_complete_jpeg() -> None:
    compact = "".join(ASSET.read_text(encoding="ascii").split())
    data = base64.b64decode(compact, validate=True)

    assert len(data) > 100_000
    assert data.startswith(b"\xff\xd8\xff")
    assert data.endswith(b"\xff\xd9")
