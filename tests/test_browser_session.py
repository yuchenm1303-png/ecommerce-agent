from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.browser_session import build_edge_command, cdp_endpoint, _choose_page


def test_cdp_endpoint_is_localhost_only():
    assert cdp_endpoint(9222) == "http://127.0.0.1:9222"


def test_edge_command_uses_dedicated_profile_and_local_cdp(tmp_path: Path):
    exe = tmp_path / "msedge.exe"
    profile = tmp_path / "makro-edge"
    command = build_edge_command(
        executable=exe,
        profile_dir=profile,
        port=9333,
        start_url="https://seller.makro.co.za/",
    )

    joined = " ".join(command)
    assert "--remote-debugging-port=9333" in command
    assert "--remote-debugging-address=127.0.0.1" in command
    assert any(item.startswith("--user-data-dir=") for item in command)
    assert str(profile.resolve()) in joined


def test_choose_page_prefers_existing_listing():
    home = SimpleNamespace(url="https://seller.makro.co.za/")
    listing = SimpleNamespace(
        url="https://seller.makro.co.za/index.html#dashboard/addListings/single?vertical=vehicle_camera_system"
    )
    other = SimpleNamespace(url="https://example.com/")
    context = SimpleNamespace(pages=[home, listing, other])

    assert _choose_page(context) is listing


def test_choose_page_falls_back_to_makro_tab():
    makro = SimpleNamespace(url="https://seller.makro.co.za/")
    other = SimpleNamespace(url="https://example.com/")
    context = SimpleNamespace(pages=[makro, other])

    assert _choose_page(context) is makro
