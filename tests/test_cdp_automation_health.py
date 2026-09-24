from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import app.cdp_automation_health as health


class _PlaywrightContext:
    def __init__(self, browser) -> None:
        self._browser = browser

    def __enter__(self):
        chromium = SimpleNamespace(connect_over_cdp=lambda *_args, **_kwargs: self._browser)
        return SimpleNamespace(chromium=chromium)

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        return None


def test_endpoint_alive_is_not_automation_ready_until_playwright_attach(monkeypatch) -> None:
    monkeypatch.setattr(health, "cdp_endpoint_token", lambda _port: "ws://generation-a")
    monkeypatch.setattr(health, "cdp_attach_guard", lambda *_a, **_k: nullcontext())

    class BrokenPlaywrightContext:
        def __enter__(self):
            chromium = SimpleNamespace(
                connect_over_cdp=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    TimeoutError("connect_over_cdp handshake timed out")
                )
            )
            return SimpleNamespace(chromium=chromium)

        def __exit__(self, _exc_type, _exc, _tb) -> None:
            return None

    monkeypatch.setattr(health, "sync_playwright", lambda: BrokenPlaywrightContext())

    probe = health.probe_cdp_automation(9222, timeout_ms=1000, transport_lane_owned=True)

    assert probe.endpoint_alive is True
    assert probe.automation_ready is False
    assert probe.state == health.POISONED
    assert "connect_over_cdp" in probe.error


def test_probe_requires_real_context_inventory(monkeypatch) -> None:
    monkeypatch.setattr(health, "cdp_endpoint_token", lambda _port: "ws://generation-b")
    monkeypatch.setattr(health, "cdp_attach_guard", lambda *_a, **_k: nullcontext())
    browser = SimpleNamespace(contexts=[], is_connected=lambda: True)
    monkeypatch.setattr(health, "sync_playwright", lambda: _PlaywrightContext(browser))

    probe = health.probe_cdp_automation(9222, timeout_ms=1000, transport_lane_owned=True)

    assert probe.state == health.POISONED
    assert "without a browser context" in probe.error


def test_successful_probe_is_automation_ready(monkeypatch) -> None:
    monkeypatch.setattr(health, "cdp_endpoint_token", lambda _port: "ws://generation-c")
    monkeypatch.setattr(health, "cdp_attach_guard", lambda *_a, **_k: nullcontext())
    context = SimpleNamespace(pages=[object(), object()])
    browser = SimpleNamespace(contexts=[context], is_connected=lambda: True)
    monkeypatch.setattr(health, "sync_playwright", lambda: _PlaywrightContext(browser))

    probe = health.probe_cdp_automation(9222, timeout_ms=1000, transport_lane_owned=True)

    assert probe.state == health.AUTOMATION_READY
    assert probe.context_count == 1
    assert probe.page_count == 2


def test_normal_probe_acquires_transport_lane(monkeypatch) -> None:
    entered: list[int] = []

    class Lane:
        def __enter__(self):
            entered.append(9222)
            return None

        def __exit__(self, _exc_type, _exc, _tb):
            return None

    monkeypatch.setattr(health, "cdp_endpoint_token", lambda _port: "ws://generation-lane")
    monkeypatch.setattr(health, "exclusive_cdp_transport_lane", lambda port: Lane())
    monkeypatch.setattr(health, "cdp_attach_guard", lambda *_a, **_k: nullcontext())
    context = SimpleNamespace(pages=[])
    browser = SimpleNamespace(contexts=[context], is_connected=lambda: True)
    monkeypatch.setattr(health, "sync_playwright", lambda: _PlaywrightContext(browser))

    probe = health.probe_cdp_automation(9222, timeout_ms=1000)

    assert probe.state == health.AUTOMATION_READY
    assert entered == [9222]


def test_poison_marker_is_bound_to_one_browser_generation(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "poison.json"
    monkeypatch.setattr(health, "_poison_path", lambda _port: path)

    health.mark_cdp_poisoned(
        9222,
        endpoint_token="ws://generation-old",
        reason="attach failed",
    )
    assert health.poison_matches_current_generation(9222, "ws://generation-old") is True

    assert health.poison_matches_current_generation(9222, "ws://generation-new") is False
    assert not path.exists()


def test_ownership_rejection_is_not_browser_poison() -> None:
    assert health.looks_like_cdp_transport_failure(
        "CDP transport lane 9222 已由当前进程树中的父级浏览器阶段持有；禁止嵌套启动第二个浏览器控制阶段。"
    ) is False
    assert health.looks_like_cdp_transport_failure(
        "Makro Edge CDP 9222 已由当前进程树中的父级 Playwright transport 控制；禁止在其仍存活时再次创建独立 EdgeHarness/connect_over_cdp。"
    ) is False
    assert health.looks_like_cdp_transport_failure(
        "BrowserType.connect_over_cdp: Timeout 25000ms exceeded after <ws connected>"
    ) is True
