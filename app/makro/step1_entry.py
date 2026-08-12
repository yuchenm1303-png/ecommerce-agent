"""Structural entry gate for Makro Step 1 / Select Vertical.

Makro is a hash-routed React SPA.  The page can already expose a safe Vertical
search/taxonomy interaction surface while the coarse stage detector still
reports UNKNOWN for a short period.  Workflow entry therefore uses an
*operability* contract rather than treating one stage enum as the only source of
truth.

This module does not select a category.  It only returns a page that is safe for
``vertical_selection.select_vertical`` or fails closed when the only listing tab
is already Step 2/3.
"""

from __future__ import annotations

import json
import time
from typing import Any

from .listing import MAKRO_SINGLE_LISTING_ROUTE, parse_makro_listing_url
from .listing_creation import MAKRO_NEW_LISTING_URL, is_brand_step, is_product_info_step
from .portal_adapter import MakroPortalAdapter
from .taxonomy_resilient import ResilientMakroTaxonomyBrowser
from .vertical_selection import is_vertical_interaction_ready


def _is_single_listing_route(url: str) -> bool:
    try:
        parse_makro_listing_url(str(url or ""))
        return True
    except (ValueError, AttributeError):
        return False


def _has_password(page: Any) -> bool:
    try:
        return page.locator('input[type="password"]').count() > 0
    except Exception:
        return False


def _diagnostics(page: Any) -> str:
    payload: dict[str, Any] = {"url": str(getattr(page, "url", "") or "")}
    try:
        payload["stage"] = MakroPortalAdapter(page).detect_stage().value
    except Exception as exc:
        payload["stage_error"] = type(exc).__name__
    try:
        columns = ResilientMakroTaxonomyBrowser(page).columns()
        payload["taxonomy_columns"] = len(columns)
        payload["taxonomy_sizes"] = [len(column) for column in columns]
    except Exception as exc:
        payload["taxonomy_error"] = type(exc).__name__
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _wait_until_vertical_operable(page: Any, *, timeout_s: float = 30.0) -> bool:
    deadline = time.monotonic() + max(0.5, float(timeout_s))
    while time.monotonic() < deadline:
        if _has_password(page):
            raise RuntimeError("Makro 登录状态无效；请先在长期 Edge 中人工登录，再重试。")
        if is_vertical_interaction_ready(page):
            return True
        try:
            if is_product_info_step(page) or is_brand_step(page):
                return False
        except Exception:
            pass
        page.wait_for_timeout(250)
    return is_vertical_interaction_ready(page)


def prepare_single_step1_page(harness: Any):
    """Return the unique Single Listing page when Step 1 is structurally usable."""

    if harness.context is None:
        raise RuntimeError("Makro Edge context is unavailable")

    listing_pages = [
        page for page in harness.context.pages if _is_single_listing_route(getattr(page, "url", ""))
    ]
    if len(listing_pages) > 1:
        raise RuntimeError(
            f"检测到 {len(listing_pages)} 个 Add a Single Listing 标签页；拒绝猜目标。请只保留一个。"
        )

    if listing_pages:
        page = listing_pages[0]
        page.set_default_timeout(15_000)
        if _wait_until_vertical_operable(page, timeout_s=5.0):
            return page
        if is_product_info_step(page) or is_brand_step(page):
            raise RuntimeError(
                "当前唯一 Add Listing 标签页已经进入 Step 2/3。为避免接管未知 draft，程序不会自动改动它；"
                "请先处理/关闭该 draft，再开始新的商品。"
            )
        raise RuntimeError(
            "当前 Add Listing 标签页未出现可安全操作的 Step 1 Vertical 界面。"
            f" diagnostics={_diagnostics(page)}"
        )

    page = harness.ensure_page()
    page.set_default_timeout(15_000)
    page.goto(MAKRO_NEW_LISTING_URL, wait_until="domcontentloaded", timeout=45_000)
    if _wait_until_vertical_operable(page, timeout_s=30.0):
        return page

    raise RuntimeError(
        "自动进入 Add a Single Listing 后，页面未形成可安全操作的 Step 1 Vertical 界面。"
        f" diagnostics={_diagnostics(page)}"
    )


def prepare_owned_step1_page(page: Any) -> None:
    """Prepare one Batch-owned tab using the same structural readiness contract."""

    page.set_default_timeout(15_000)
    page.goto(MAKRO_NEW_LISTING_URL, wait_until="domcontentloaded", timeout=45_000)
    if _wait_until_vertical_operable(page, timeout_s=30.0):
        return
    raise RuntimeError(
        "Batch owned Makro tab did not form a safely operable Step 1 Vertical surface. "
        f"diagnostics={_diagnostics(page)}"
    )


__all__ = ["prepare_owned_step1_page", "prepare_single_step1_page"]
