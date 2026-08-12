"""Shared portal entry gate for Makro Step 1 / Select Vertical.

The normal listing workflow starts at Step 1, but a fresh Makro tab may land on
the Seller Portal Dashboard instead of the Add a Single Listing route. Single
and Batch therefore share the same bounded pre-Step-1 navigation:

Dashboard -> Listings -> Add New Listings -> Listing Creation
          -> Add New Listing -> Add Single Listing -> Step 1

Once Step 1 is reached, the existing structural operability contract remains the
source of truth. This module does not select a Vertical and does not resume a
known Step 2/3 draft; those states still fail closed here.
"""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import urlparse

from .listing import MAKRO_HOME_URL, MAKRO_HOST, MAKRO_SINGLE_LISTING_ROUTE, parse_makro_listing_url
from .listing_creation import _vertical_search_input, is_brand_step, is_product_info_step
from .portal_adapter import MakroPortalAdapter
from .taxonomy_resilient import ResilientMakroTaxonomyBrowser
from .vertical_selection import is_vertical_interaction_ready


_LISTINGS = "Listings"
_ADD_NEW_LISTINGS = "Add New Listings"
_LISTING_CREATION = "Listing Creation"
_ADD_NEW_LISTING = "Add New Listing"
_ADD_SINGLE_LISTING = "Add Single Listing"
_DASHBOARD = "Your Dashboard"


def _is_single_listing_route(url: str) -> bool:
    try:
        parse_makro_listing_url(str(url or ""))
        return True
    except (ValueError, AttributeError):
        return False


def _is_safe_pre_step1_single_route(page: Any) -> bool:
    """True only for an empty Single Listing route with no draft identity yet."""

    try:
        target = parse_makro_listing_url(str(getattr(page, "url", "") or ""))
    except (ValueError, AttributeError):
        return False
    return not any(
        str(value or "").strip()
        for value in (target.vertical, target.brand, target.request_id, target.vid)
    )


def _has_password(page: Any) -> bool:
    try:
        return page.locator('input[type="password"]').count() > 0
    except Exception:
        return False


def _is_step1_operable(page: Any) -> bool:
    """Return True only when Step 1 readiness implies select_vertical can run.

    ``is_vertical_interaction_ready`` deliberately accepts several structural
    signals because Makro's SPA stage enum can lag. Some non-Step-1 portal
    surfaces, however, can resemble taxonomy columns closely enough to trigger
    those broad signals. The production selector always requires the actual
    Vertical search input, so the entry gate must require it too. This keeps the
    readiness contract aligned with ``select_vertical`` and prevents Dashboard
    or Listing Creation chrome from being accepted as Step 1.
    """

    try:
        if not is_vertical_interaction_ready(page):
            return False
        _vertical_search_input(page)
        return True
    except Exception:
        return False


def _visible(locator: Any) -> list[Any]:
    output: list[Any] = []
    try:
        count = locator.count()
    except Exception:
        return output
    for index in range(count):
        candidate = locator.nth(index)
        try:
            if candidate.is_visible():
                output.append(candidate)
        except Exception:
            continue
    return output


def _visible_exact_roles(page: Any, label: str) -> list[Any]:
    output: list[Any] = []
    for role in ("link", "button"):
        try:
            output.extend(_visible(page.get_by_role(role, name=label, exact=True)))
        except Exception:
            continue
    return output


def _visible_exact_text(page: Any, label: str) -> list[Any]:
    try:
        return _visible(page.get_by_text(label, exact=True))
    except Exception:
        return []


def _has_exact_action(page: Any, label: str) -> bool:
    if _visible_exact_roles(page, label):
        return True
    return bool(_visible_exact_text(page, label))


def _click_exact_action(page: Any, label: str) -> None:
    """Click one exact visible portal action and refuse ambiguous targets."""

    matches = _visible_exact_roles(page, label)
    if len(matches) > 1:
        raise RuntimeError(
            f"Makro portal action {label!r} matched {len(matches)} visible link/button controls; refusing to guess."
        )
    if len(matches) == 1:
        matches[0].click(timeout=5_000)
        return

    text_matches = _visible_exact_text(page, label)
    if len(text_matches) > 1:
        raise RuntimeError(
            f"Makro portal action {label!r} matched {len(text_matches)} visible exact-text nodes; refusing to guess."
        )
    if len(text_matches) == 1:
        text_matches[0].click(timeout=5_000)
        return

    raise RuntimeError(f"Makro portal action {label!r} is not uniquely available.")


def _is_makro_host(page: Any) -> bool:
    try:
        return urlparse(str(getattr(page, "url", "") or "")).hostname == MAKRO_HOST
    except ValueError:
        return False


def _is_dashboard(page: Any) -> bool:
    try:
        url = str(getattr(page, "url", "") or "").casefold()
    except Exception:
        url = ""
    if "#dashboard/home-page" in url:
        return True
    return bool(_visible_exact_text(page, _DASHBOARD))


def _is_listing_creation(page: Any) -> bool:
    # The Step 1 page is also a listing-creation flow, so rule it out first.
    if _is_step1_operable(page):
        return False
    return bool(
        _visible_exact_text(page, _LISTING_CREATION)
        and (
            _has_exact_action(page, _ADD_NEW_LISTING)
            or _has_exact_action(page, _ADD_SINGLE_LISTING)
        )
    )


def _diagnostics(page: Any) -> str:
    payload: dict[str, Any] = {
        "url": str(getattr(page, "url", "") or ""),
        "dashboard": _is_dashboard(page),
        "listing_creation": _is_listing_creation(page),
        "step1_operable": _is_step1_operable(page),
        "safe_pre_step1_single_route": _is_safe_pre_step1_single_route(page),
    }
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


def _wait_until(page: Any, predicate, *, timeout_s: float) -> bool:
    deadline = time.monotonic() + max(0.5, float(timeout_s))
    while time.monotonic() < deadline:
        if predicate():
            return True
        page.wait_for_timeout(200)
    return bool(predicate())


def _wait_until_vertical_operable(page: Any, *, timeout_s: float = 30.0) -> bool:
    deadline = time.monotonic() + max(0.5, float(timeout_s))
    while time.monotonic() < deadline:
        if _has_password(page):
            raise RuntimeError("Makro 登录状态无效；请先在长期 Edge 中人工登录，再重试。")
        if _is_step1_operable(page):
            return True
        try:
            if is_product_info_step(page) or is_brand_step(page):
                return False
        except Exception:
            pass
        page.wait_for_timeout(250)
    return _is_step1_operable(page)


def _reject_later_listing_stage(page: Any) -> None:
    try:
        later = is_product_info_step(page) or is_brand_step(page)
    except Exception:
        later = False
    if later:
        raise RuntimeError(
            "当前 Add Listing 页面已经进入 Step 2/3。第一阶段入口导航不会接管或倒退未知 draft；"
            "请保留现场，后续由独立的任务恢复状态机处理。"
        )


def _open_listing_creation_from_dashboard(page: Any) -> None:
    if _is_listing_creation(page) or _is_step1_operable(page):
        return

    if not _is_dashboard(page):
        raise RuntimeError(
            "当前 Makro 页面不是可识别的 Dashboard，无法安全执行 Listings -> Add New Listings。 "
            f"diagnostics={_diagnostics(page)}"
        )

    if not _has_exact_action(page, _ADD_NEW_LISTINGS):
        _click_exact_action(page, _LISTINGS)
        if not _wait_until(
            page,
            lambda: _has_exact_action(page, _ADD_NEW_LISTINGS),
            timeout_s=5.0,
        ):
            raise RuntimeError("点击 Listings 后没有出现唯一可见的 Add New Listings。")

    _click_exact_action(page, _ADD_NEW_LISTINGS)
    if not _wait_until(
        page,
        lambda: _is_listing_creation(page) or _is_step1_operable(page),
        timeout_s=15.0,
    ):
        raise RuntimeError(
            "点击 Add New Listings 后没有进入 Listing Creation。 "
            f"diagnostics={_diagnostics(page)}"
        )


def _open_step1_from_listing_creation(page: Any) -> None:
    if _is_step1_operable(page):
        return
    if not _is_listing_creation(page):
        raise RuntimeError(
            "当前 Makro 页面不是可识别的 Listing Creation，无法安全创建 Single Listing。 "
            f"diagnostics={_diagnostics(page)}"
        )

    if not _has_exact_action(page, _ADD_SINGLE_LISTING):
        _click_exact_action(page, _ADD_NEW_LISTING)
        if not _wait_until(
            page,
            lambda: _has_exact_action(page, _ADD_SINGLE_LISTING),
            timeout_s=5.0,
        ):
            raise RuntimeError("点击 Add New Listing 后没有出现唯一可见的 Add Single Listing。")

    _click_exact_action(page, _ADD_SINGLE_LISTING)
    if not _wait_until(
        page,
        lambda: _is_step1_operable(page) or _is_dashboard(page),
        timeout_s=20.0,
    ):
        raise RuntimeError(
            "点击 Add Single Listing 后没有进入 Step 1 Vertical，也没有回到可恢复的 Dashboard。 "
            f"diagnostics={_diagnostics(page)}"
        )


def _prepare_new_listing_step1_page(page: Any) -> None:
    """Bring a new/owned Makro page to Step 1 through the real Seller Portal UI."""

    page.set_default_timeout(15_000)

    # Bound the recovery to the pre-Step-1 chain only. This intentionally does
    # not become the later task-state machine.
    for _ in range(6):
        if _has_password(page):
            raise RuntimeError("Makro 登录状态无效；请先在长期 Edge 中人工登录，再重试。")
        if _is_step1_operable(page):
            return
        _reject_later_listing_stage(page)

        if _is_listing_creation(page):
            _open_step1_from_listing_creation(page)
            if _is_step1_operable(page):
                return
            _reject_later_listing_stage(page)
            continue

        if _is_dashboard(page):
            _open_listing_creation_from_dashboard(page)
            continue

        # A newly-created Chromium tab is normally about:blank. Likewise, a
        # stale non-listing Seller Portal location or a completely empty
        # single-listing shell may be left over from the previous session. With
        # no Vertical/Brand/requestId/vid there is no draft identity to lose, so
        # it is safe to normalize that pre-Step-1 state back to Home and then
        # traverse the real portal UI.
        current_url = str(getattr(page, "url", "") or "")
        if (
            not _is_makro_host(page)
            or not _is_single_listing_route(current_url)
            or _is_safe_pre_step1_single_route(page)
        ):
            page.goto(MAKRO_HOME_URL, wait_until="domcontentloaded", timeout=45_000)
            if not _wait_until(
                page,
                lambda: (
                    _is_dashboard(page)
                    or _is_listing_creation(page)
                    or _is_step1_operable(page)
                ),
                timeout_s=15.0,
            ):
                raise RuntimeError(
                    "打开 Makro Seller Portal Home 后没有形成可识别的 Dashboard/Listing Creation/Step 1。 "
                    f"diagnostics={_diagnostics(page)}"
                )
            continue

        raise RuntimeError(
            "当前 Add a Single Listing route 未形成可安全操作的 Step 1 Vertical 界面。 "
            f"diagnostics={_diagnostics(page)}"
        )

    raise RuntimeError(
        "Makro pre-Step-1 导航超过安全转换上限，仍未到达 Select Vertical。 "
        f"diagnostics={_diagnostics(page)}"
    )


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
        if _is_safe_pre_step1_single_route(page):
            _prepare_new_listing_step1_page(page)
            return page
        raise RuntimeError(
            "当前 Add Listing 标签页未出现可安全操作的 Step 1 Vertical 界面。"
            f" diagnostics={_diagnostics(page)}"
        )

    page = harness.ensure_page()
    _prepare_new_listing_step1_page(page)
    return page


def prepare_owned_step1_page(page: Any) -> None:
    """Prepare one Batch-owned tab through the same shared pre-Step-1 UI path."""

    _prepare_new_listing_step1_page(page)


__all__ = ["prepare_owned_step1_page", "prepare_single_step1_page"]
