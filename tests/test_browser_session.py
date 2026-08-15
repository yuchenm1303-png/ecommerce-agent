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


# ---------------------------------------------------------------------------
# EdgeHarness: Browser-Harness-style session abstraction (offline fakes).
# ---------------------------------------------------------------------------

class FakeBrowser:
    def __init__(self, context):
        self.contexts = [context]
        self.closed = False

    def close(self):
        self.closed = True


class FakeContext:
    def __init__(self, pages):
        self.pages = pages


class FakePlaywright:
    def __init__(self, browser):
        self.attach_calls: list[tuple[str, int | None]] = []

        def connect_over_cdp(endpoint, *, timeout=None):
            self.attach_calls.append((endpoint, timeout))
            return browser

        self.chromium = SimpleNamespace(connect_over_cdp=connect_over_cdp)


def _fake_page(url="https://seller.makro.co.za/"):
    return SimpleNamespace(url=url, is_closed=lambda: False)


def test_harness_launches_detached_edge_when_cdp_absent(monkeypatch, tmp_path: Path):
    import app.browser_session as bs

    launched = {}
    monkeypatch.setattr(bs, "is_cdp_ready", lambda port=9222, **kw: False)
    monkeypatch.setattr(
        bs,
        "launch_detached_edge",
        lambda *, profile_dir, port, start_url: launched.update(
            profile_dir=profile_dir, port=port, start_url=start_url
        ),
    )
    listing = _fake_page(
        "https://seller.makro.co.za/index.html#dashboard/addListings/single?vertical=x"
    )
    browser = FakeBrowser(FakeContext([_fake_page(), listing]))
    playwright = FakePlaywright(browser)

    # launch_detached_edge is a fake, so make CDP become ready once launch has
    # been requested; the harness must then attach rather than start again.
    monkeypatch.setattr(
        bs,
        "is_cdp_ready",
        lambda port=9222, **kw: bool(launched),
    )

    harness = bs.EdgeHarness(
        playwright, profile_dir=tmp_path / "makro-edge", port=9333,
        start_url="https://seller.makro.co.za/",
    )

    assert harness.launched_now is True
    assert launched["port"] == 9333
    assert launched["profile_dir"] == (tmp_path / "makro-edge").resolve()
    assert harness.page is listing


def test_harness_attaches_without_launch_when_cdp_ready(monkeypatch, tmp_path: Path):
    import app.browser_session as bs

    calls = []
    monkeypatch.setattr(bs, "is_cdp_ready", lambda port=9222, **kw: True)
    monkeypatch.setattr(
        bs, "launch_detached_edge", lambda **kw: calls.append(kw)
    )
    page = _fake_page()
    browser = FakeBrowser(FakeContext([page]))
    playwright = FakePlaywright(browser)
    harness = bs.EdgeHarness(
        playwright, profile_dir=tmp_path / "p", port=19222
    )

    assert harness.launched_now is False
    assert calls == []
    assert harness.page is page
    assert playwright.attach_calls == [
        ("http://127.0.0.1:19222", bs._CDP_ATTACH_TIMEOUT_MS)
    ]
    assert bs._CDP_ATTACH_TIMEOUT_MS < 180_000


def test_harness_retries_transient_cdp_attach_without_restarting_edge(monkeypatch, tmp_path: Path):
    import app.browser_session as bs

    browser = FakeBrowser(FakeContext([_fake_page()]))
    attach_calls: list[int | None] = []
    launch_calls: list[object] = []

    def connect_over_cdp(_endpoint, *, timeout=None):
        attach_calls.append(timeout)
        if len(attach_calls) == 1:
            raise RuntimeError("simulated CDP attach stall after websocket connect")
        return browser

    playwright = SimpleNamespace(
        chromium=SimpleNamespace(connect_over_cdp=connect_over_cdp)
    )
    monkeypatch.setattr(bs, "is_cdp_ready", lambda port=9222, **kw: True)
    monkeypatch.setattr(bs, "launch_detached_edge", lambda **kw: launch_calls.append(kw))
    monkeypatch.setattr(bs.time, "sleep", lambda _seconds: None)

    harness = bs.EdgeHarness(
        playwright,
        profile_dir=tmp_path / "p",
        port=19223,
    )

    assert harness.page is browser.contexts[0].pages[0]
    assert attach_calls == [bs._CDP_ATTACH_TIMEOUT_MS, bs._CDP_ATTACH_TIMEOUT_MS]
    assert launch_calls == []


def test_harness_detach_never_closes_external_browser(monkeypatch, tmp_path: Path):
    import app.browser_session as bs

    monkeypatch.setattr(bs, "is_cdp_ready", lambda port=9222, **kw: True)
    browser = FakeBrowser(FakeContext([_fake_page()]))
    harness = bs.EdgeHarness(FakePlaywright(browser), profile_dir=tmp_path / "p", port=19224)

    harness.detach()

    assert browser.closed is False
    assert harness.browser is None
    assert harness.context is None
    assert harness.page is None


def test_harness_ensure_page_reconnects_when_current_page_closed(monkeypatch, tmp_path: Path):
    import app.browser_session as bs

    monkeypatch.setattr(bs, "is_cdp_ready", lambda port=9222, **kw: True)
    old_page = _fake_page()
    new_page = _fake_page(
        "https://seller.makro.co.za/index.html#dashboard/addListings/single?vertical=y"
    )
    old_page.is_closed = lambda: True
    browser = FakeBrowser(FakeContext([new_page]))
    harness = bs.EdgeHarness(FakePlaywright(browser), profile_dir=tmp_path / "p", port=19225)
    harness.page = old_page

    page = harness.ensure_page()

    assert page is new_page


def test_harness_health_check_reflects_cdp(monkeypatch, tmp_path: Path):
    import app.browser_session as bs

    state = {"ready": True}
    monkeypatch.setattr(bs, "is_cdp_ready", lambda port=9222, **kw: state["ready"])
    harness = bs.EdgeHarness(
        FakePlaywright(FakeBrowser(FakeContext([_fake_page()]))),
        profile_dir=tmp_path / "p",
        port=19226,
    )
    assert harness.health_check() is True

    state["ready"] = False
    assert harness.health_check() is False


def test_choose_page_alias_kept_for_backward_compat():
    from app.browser_session import _choose_page, select_listing_page

    assert _choose_page is select_listing_page
