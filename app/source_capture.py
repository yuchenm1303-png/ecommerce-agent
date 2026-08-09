from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

from .browser_session import cdp_endpoint, is_cdp_ready, launch_detached_edge
from .source_snapshot import SourceAccessBlocked, SourceSnapshot, capture_page_snapshot, write_source_snapshot


DEFAULT_SOURCE_CDP_PORT = 9333


@dataclass(slots=True, frozen=True)
class CapturedProductSource:
    snapshot_path: Path
    screenshot_path: Path
    snapshot: SourceSnapshot
    launched_now: bool


def validate_source_url(value: str) -> str:
    url = value.strip()
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("product URL 必须是完整 http/https URL。")
    return url


def _connect_source_edge(playwright, *, profile_dir: Path, port: int, start_url: str):
    launched_now = not is_cdp_ready(port)
    if launched_now:
        launch_detached_edge(profile_dir=profile_dir, port=port, start_url=start_url)
    browser = playwright.chromium.connect_over_cdp(cdp_endpoint(port))
    contexts = list(browser.contexts)
    if not contexts:
        raise RuntimeError("已连接 source Edge，但没有 browser context。")
    context = contexts[0]
    pages = list(context.pages)
    page = pages[-1] if pages else context.new_page()
    return browser, context, page, launched_now


def _load_lazy_page(page, *, initial_wait_ms: int, scroll_wait_ms: int, max_scroll_steps: int) -> None:
    if initial_wait_ms:
        page.wait_for_timeout(initial_wait_ms)
    stable_rounds = 0
    previous_height = 0
    for _ in range(max_scroll_steps):
        state = page.evaluate(
            """() => ({
                y: window.scrollY,
                h: Math.max(document.body?.scrollHeight || 0, document.documentElement?.scrollHeight || 0),
                vh: window.innerHeight || 800
            })"""
        )
        height = int(state.get("h") or 0)
        y = int(state.get("y") or 0)
        viewport = max(400, int(state.get("vh") or 800))
        if height <= 0:
            break
        if y + viewport >= height - 8:
            stable_rounds = stable_rounds + 1 if height == previous_height else 0
            if stable_rounds >= 2:
                break
        page.evaluate("(step) => window.scrollBy(0, step)", max(300, int(viewport * 0.85)))
        if scroll_wait_ms:
            page.wait_for_timeout(scroll_wait_ms)
        previous_height = height
    page.evaluate("() => window.scrollTo(0, 0)")
    if scroll_wait_ms:
        page.wait_for_timeout(scroll_wait_ms)


def capture_product_source(
    url: str,
    *,
    output_dir: str | Path,
    profile_dir: str | Path = "browser_profiles/source-edge",
    cdp_port: int = DEFAULT_SOURCE_CDP_PORT,
    initial_wait_ms: int = 1800,
    scroll_wait_ms: int = 180,
    max_scroll_steps: int = 120,
    max_visible_text_chars: int = 120_000,
    use_current_page: bool = False,
) -> CapturedProductSource:
    """Capture one exact supplier product page as text/table data plus a full-page image.

    This is mechanical browser collection only. It never interprets product facts and never
    touches the Makro browser profile. CAPTCHA/risk controls are not bypassed.
    """

    source_url = validate_source_url(url)
    if initial_wait_ms < 0 or scroll_wait_ms < 0:
        raise ValueError("source wait 参数不能为负数。")
    if max_scroll_steps < 1:
        raise ValueError("max_scroll_steps 必须 >= 1。")
    if max_visible_text_chars < 1_000:
        raise ValueError("max_visible_text_chars 不能小于 1000。")

    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        _, _, page, launched_now = _connect_source_edge(
            playwright,
            profile_dir=Path(profile_dir).resolve(),
            port=int(cdp_port),
            start_url=source_url,
        )
        page.set_default_timeout(15_000)
        if use_current_page:
            if page.url in {"", "about:blank"}:
                raise RuntimeError("--source-use-current-page 时 source Edge 没有已打开网页。")
        else:
            page.goto(source_url, wait_until="domcontentloaded", timeout=45_000)

        _load_lazy_page(
            page,
            initial_wait_ms=int(initial_wait_ms),
            scroll_wait_ms=int(scroll_wait_ms),
            max_scroll_steps=int(max_scroll_steps),
        )
        snapshot = capture_page_snapshot(
            page,
            requested_url=source_url,
            max_visible_text_chars=int(max_visible_text_chars),
        )
        snapshot_path = write_source_snapshot(snapshot, target_dir / "source-snapshot.json")
        screenshot_path = target_dir / "source-page.png"
        page.screenshot(path=str(screenshot_path), full_page=True)

    return CapturedProductSource(
        snapshot_path=snapshot_path,
        screenshot_path=screenshot_path,
        snapshot=snapshot,
        launched_now=launched_now,
    )


__all__ = [
    "CapturedProductSource",
    "DEFAULT_SOURCE_CDP_PORT",
    "SourceAccessBlocked",
    "capture_product_source",
    "validate_source_url",
]
