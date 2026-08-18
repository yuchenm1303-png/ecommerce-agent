"""Makro listing recognition, vertical guard and authenticated-page waiting.

Heuristics here are read-only: they never read or record cookies, tokens,
sessionStorage or Authorization data, and they are not an authentication
bypass. Every scan still requires the Add Listing markers via
is_makro_listing_page().
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import Page


MAKRO_HOME_URL = "https://seller.makro.co.za/"
MAKRO_HOST = "seller.makro.co.za"
MAKRO_SINGLE_LISTING_ROUTE = "dashboard/addListings/single"


@dataclass(frozen=True)
class MakroListingTarget:
    url: str
    brand: str | None
    vertical: str | None
    request_id: str | None
    vid: str | None


def parse_makro_listing_url(url: str) -> MakroListingTarget:
    """Validate and parse a Makro Marketplace single-listing hash URL.

    Makro uses a hash-routed SPA. Parameters such as requestId can be
    short-lived, so callers must not treat them as stable product identifiers.
    """

    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Makro listing URL must use http/https")
    if parsed.hostname != MAKRO_HOST:
        raise ValueError(f"Expected host {MAKRO_HOST!r}, got {parsed.hostname!r}")

    route, separator, query_string = parsed.fragment.partition("?")
    if route.rstrip("/") != MAKRO_SINGLE_LISTING_ROUTE:
        raise ValueError(
            "URL is not a Makro Add a Single Listing page: "
            f"expected #{MAKRO_SINGLE_LISTING_ROUTE}"
        )

    params = parse_qs(query_string if separator else "", keep_blank_values=True)

    def first(name: str) -> str | None:
        values = params.get(name)
        return values[0] if values else None

    return MakroListingTarget(
        url=url.strip(),
        brand=first("brand"),
        vertical=first("vertical"),
        request_id=first("requestId"),
        vid=first("vid"),
    )


def is_makro_listing_page(page: Page) -> bool:
    """Return True only when the authenticated single-listing UI is visible."""

    parsed = urlparse(page.url)
    if parsed.hostname != MAKRO_HOST:
        return False

    # Never probe or fill a login form.
    if page.locator('input[type="password"]').count() > 0:
        return False

    markers = (
        "Add a Single Listing",
        "ADD PRODUCT INFO",
        "Please fill all mandatory attributes",
    )
    return any(page.get_by_text(marker, exact=False).count() > 0 for marker in markers)


def assert_expected_vertical(page: Page, expected_vertical: str | None) -> None:
    """Stop before scanning/filling if the current listing is the wrong vertical."""

    if not expected_vertical:
        return
    target = parse_makro_listing_url(page.url)
    actual = (target.vertical or "").strip()
    expected = expected_vertical.strip()
    if actual.casefold() != expected.casefold():
        raise RuntimeError(
            "当前 Add Listing vertical 与商品资料不匹配，已在扫描/填写前停止："
            f" expected={expected!r}, actual={actual or '(missing)'!r}。"
        )
    print(f"vertical 安全校验通过：{actual}")



def _wait_for_listing_page(page: Page, timeout_s: int, poll_s: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if is_makro_listing_page(page):
            return True
        page.wait_for_timeout(int(poll_s * 1000))
    return False

def _is_listing_url(url: str) -> bool:
    """Return True when the URL looks like a valid Makro single-listing URL."""
    try:
        parse_makro_listing_url(url)
        return True
    except ValueError:
        return False

def wait_for_authenticated_listing(
    page: Page,
    initial_url: str | None = None,
    *,
    headless: bool = False,
    timeout_s: int = 30,
    navigate_first: bool = True,
) -> None:
    """Ensure the current page is an authenticated Add a Single Listing page.

    First call (navigate_first=True): open Makro, detect whether a previous
    login in the persistent profile is still valid, and let the user log in /
    navigate manually. Repeat calls (navigate_first=False) reuse the same Edge
    session: the user opens a new Add Listing page and we only verify the
    current page. We never force page.goto() back to stale requestId URLs.
    """

    if navigate_first:
        if initial_url is None:
            initial_url = MAKRO_HOME_URL

        page.goto(initial_url, wait_until="domcontentloaded")
        page.wait_for_timeout(1200)

        # A validated listing URL that already renders markers needs no prompt.
        if _is_listing_url(initial_url) and _wait_for_listing_page(page, timeout_s=timeout_s):
            return

        if headless:
            raise RuntimeError(
                "无头模式无法手动登录。请去掉 --headless，在自动化 Edge 窗口中"
                "手动登录并进入 Add a Single Listing。"
            )

        if _is_logged_in(page):
            print("\n检测到 Makro 登录状态仍有效（复用现有登录，无需重新登录）。")
            print("请在这个自动化浏览器窗口中从页面正常进入")
            print("Add a Single Listing（保持在该页面）；")
        else:
            print("\n已打开 Makro 首页（自动化浏览器，独立 profile）。")
            print("请在这个浏览器窗口中：")
            print("  1. 手动登录 Makro；")
            print("  2. 从页面正常进入 Add a Single Listing（保持在该页面）；")
        print("完成后回到终端按 Enter，程序将直接采集当前页面。")
        input()
    else:
        print("\n请在这个已打开的自动化浏览器窗口中进入新的")
        print("Add a Single Listing 页面（保持在该页面）；")
        print("完成后回到终端按 Enter，程序将直接采集当前页面。")
        input()

    page.wait_for_timeout(1200)
    # Check the CURRENT page only; never jump back to a stale requestId URL.
    if _wait_for_listing_page(page, timeout_s=min(timeout_s, 10)):
        return

    raise RuntimeError(
        "当前页面不是 Add a Single Listing，已停止采集。\n"
        "请确认：已登录，且自动化浏览器的当前标签页停留在\n"
        "Add a Single Listing 页面。\n"
        "程序不会自动跳转旧 requestId URL。"
    )

def _is_makro_host(url: str) -> bool:
    """Return True when the URL points at the Makro seller host."""
    try:
        return urlparse(url).hostname == "seller.makro.co.za"
    except ValueError:
        return False

def _is_logged_in(page: Page) -> bool:
    """Best-effort login-state detection for skipping the manual-login prompt.

    Heuristic only; never reads or records cookies, tokens, sessionStorage or
    Authorization data, and is not an auth bypass: every scan still requires
    the Add Listing markers via is_makro_listing_page(). A wrong guess is
    recoverable because the user can still log in before pressing Enter.
    """

    if not _is_makro_host(page.url):
        return False
    if page.locator('input[type="password"]').count() > 0:
        return False
    try:
        text = page.evaluate(
            "() => (document.body ? (document.body.innerText || '').slice(0, 30000) : '')"
        )
    except Exception:
        return False
    lower = (text or "").lower()
    if not lower.strip():
        return False
    if any(marker in lower for marker in ("sign out", "log out", "logout", "my account")):
        return True
    if any(
        marker in lower
        for marker in ("sign in", "log in", "login", "password", "forgot password", "welcome back")
    ):
        return False
    return True


# Public aliases for the domain layer (probe-internal names kept private).
is_listing_url = _is_listing_url
is_makro_host = _is_makro_host
