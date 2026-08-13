"""Shared portal entry gate for Makro Step 1 / Select Vertical.

Single and Batch share one bounded pre-Step-1 state machine. Makro currently
exposes more than one legitimate navigation shape, so the state machine follows
observed portal state rather than assuming one fixed menu label sequence:

Legacy:
    Dashboard -> Listings -> Add New Listings -> Listing Creation
              -> Add New Listing -> Add Single Listing -> Step 1

Current:
    Dashboard -> Listings -> Listings Management
              -> Add New Listing -> Add Single Listing -> Step 1

A tab already sitting on Listings Management is a valid resumable pre-Step-1
state. Step 2/3 ownership remains fail-closed and is never pushed backward.
Known transient ``#dashboard/page-not-found`` faults retain bounded Home recovery.
This module never clicks Send to QC.
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
from .vertical_selection import _vertical_search_semantics_visible, is_vertical_interaction_ready


_LISTINGS = "Listings"
_ADD_NEW_LISTINGS = "Add New Listings"
_LISTINGS_MANAGEMENT = "Listings Management"
_LISTING_CREATION = "Listing Creation"
_ADD_NEW_LISTING = "Add New Listing"
_ADD_SINGLE_LISTING = "Add Single Listing"
_DASHBOARD = "Your Dashboard"
_LISTINGS_MANAGEMENT_ROUTE = "#dashboard/listings-management"
_PAGE_NOT_FOUND_ROUTE = "#dashboard/page-not-found"
_MAX_PAGE_NOT_FOUND_RECOVERIES = 2
_ACTION_STABLE_SAMPLES = 3
_ACTION_STABLE_INTERVAL_MS = 150


class _PortalPageNotFound(RuntimeError):
    """Internal signal for Makro's transient SPA page-not-found route."""


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
    """Return True only when the exact surface needed by select_vertical exists."""

    try:
        if not _is_single_listing_route(getattr(page, "url", "")):
            return False
        if not is_vertical_interaction_ready(page):
            return False
        if not _vertical_search_semantics_visible(page):
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


def _is_listings_management(page: Any) -> bool:
    """Recognize the current Listings Management hub structurally.

    The route is the primary signal because the exact heading can render later
    than the top-right Add New Listing control in Makro's SPA.
    """

    if not _is_makro_host(page):
        return False
    try:
        url = str(getattr(page, "url", "") or "").casefold()
    except Exception:
        url = ""
    if _LISTINGS_MANAGEMENT_ROUTE in url:
        return True
    return bool(
        _visible_exact_text(page, _LISTINGS_MANAGEMENT)
        and _has_exact_action(page, _ADD_NEW_LISTING)
    )


def _is_page_not_found(page: Any) -> bool:
    try:
        return _PAGE_NOT_FOUND_ROUTE in str(getattr(page, "url", "") or "").casefold()
    except Exception:
        return False


def _is_listing_creation(page: Any) -> bool:
    if _is_step1_operable(page):
        return False
    return bool(
        _visible_exact_text(page, _LISTING_CREATION)
        and (
            _has_exact_action(page, _ADD_NEW_LISTING)
            or _has_exact_action(page, _ADD_SINGLE_LISTING)
        )
    )


def _is_listing_hub(page: Any) -> bool:
    return _is_listings_management(page) or _is_listing_creation(page)


def _diagnostics(page: Any) -> str:
    payload: dict[str, Any] = {
        "url": str(getattr(page, "url", "") or ""),
        "dashboard": _is_dashboard(page),
        "listings_management": _is_listings_management(page),
        "page_not_found": _is_page_not_found(page),
        "listing_creation": _is_listing_creation(page),
        "step1_operable": _is_step1_operable(page),
        "safe_pre_step1_single_route": _is_safe_pre_step1_single_route(page),
        "listings_action": _has_exact_action(page, _LISTINGS),
        "add_new_listings_action": _has_exact_action(page, _ADD_NEW_LISTINGS),
        "add_new_listing_action": _has_exact_action(page, _ADD_NEW_LISTING),
        "add_single_listing_action": _has_exact_action(page, _ADD_SINGLE_LISTING),
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


def _wait_until_or_page_not_found(page: Any, predicate, *, timeout_s: float) -> bool:
    """Wait for one expected transition while failing fast on Makro SPA 404."""

    deadline = time.monotonic() + max(0.5, float(timeout_s))
    while time.monotonic() < deadline:
        if predicate():
            return True
        if _is_page_not_found(page):
            raise _PortalPageNotFound(
                "Makro Seller Portal entered #dashboard/page-not-found during pre-Step-1 navigation."
            )
        page.wait_for_timeout(200)
    if predicate():
        return True
    if _is_page_not_found(page):
        raise _PortalPageNotFound(
            "Makro Seller Portal entered #dashboard/page-not-found during pre-Step-1 navigation."
        )
    return False


def _wait_for_stable_action(page: Any, label: str, *, timeout_s: float) -> bool:
    """Require an action to remain visible across several SPA render samples."""

    deadline = time.monotonic() + max(0.5, float(timeout_s))
    stable = 0
    while time.monotonic() < deadline:
        if _is_page_not_found(page):
            raise _PortalPageNotFound(
                f"Makro Seller Portal entered page-not-found while waiting for {label!r}."
            )
        if _has_password(page):
            raise RuntimeError("Makro 登录状态无效；请先在长期 Edge 中人工登录，再重试。")
        if _has_exact_action(page, label):
            stable += 1
            if stable >= _ACTION_STABLE_SAMPLES:
                return True
        else:
            stable = 0
        page.wait_for_timeout(_ACTION_STABLE_INTERVAL_MS)
    return False


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
            "请保留现场，后续由共享任务状态机处理。"
        )


def _dashboard_navigation_ready(page: Any) -> bool:
    if _has_password(page):
        raise RuntimeError("Makro 登录状态无效；请先在长期 Edge 中人工登录，再重试。")
    return bool(
        _is_listing_hub(page)
        or _is_step1_operable(page)
        or _has_exact_action(page, _ADD_NEW_LISTINGS)
        or _has_exact_action(page, _LISTINGS)
    )


def _dashboard_next_state_ready(page: Any) -> bool:
    """Accept either legacy submenu or current Listings Management route."""

    return bool(
        _is_listing_hub(page)
        or _is_step1_operable(page)
        or _has_exact_action(page, _ADD_NEW_LISTINGS)
    )


def _open_listing_creation_from_dashboard(page: Any) -> None:
    """Advance Dashboard to whichever supported listing hub Makro renders."""

    if _is_listing_hub(page) or _is_step1_operable(page):
        return

    if not _is_dashboard(page):
        raise RuntimeError(
            "当前 Makro 页面不是可识别的 Dashboard/Listings Management，无法安全进入新建商品。 "
            f"diagnostics={_diagnostics(page)}"
        )

    if not _wait_until_or_page_not_found(
        page,
        lambda: _dashboard_navigation_ready(page),
        timeout_s=15.0,
    ):
        raise RuntimeError(
            "Makro Dashboard route 已打开，但 Listings 导航在等待窗口内仍未就绪。 "
            f"diagnostics={_diagnostics(page)}"
        )

    # Some portal builds expose Add New Listings directly from Dashboard. Keep
    # that path, but do not require it after clicking Listings: current Makro
    # navigates straight to Listings Management and exposes singular Add New Listing.
    if not _has_exact_action(page, _ADD_NEW_LISTINGS):
        if not _wait_for_stable_action(page, _LISTINGS, timeout_s=5.0):
            raise RuntimeError(
                "Makro Dashboard 的 Listings 没有形成稳定可点击状态。 "
                f"diagnostics={_diagnostics(page)}"
            )
        _click_exact_action(page, _LISTINGS)
        if not _wait_until_or_page_not_found(
            page,
            lambda: _dashboard_next_state_ready(page),
            timeout_s=12.0,
        ):
            raise RuntimeError(
                "点击 Listings 后既没有进入 Listings Management，也没有出现 Add New Listings。 "
                f"diagnostics={_diagnostics(page)}"
            )

    if _is_listing_hub(page) or _is_step1_operable(page):
        return

    if not _wait_for_stable_action(page, _ADD_NEW_LISTINGS, timeout_s=5.0):
        raise RuntimeError(
            "Makro 的 Add New Listings 没有形成稳定可点击状态。 "
            f"diagnostics={_diagnostics(page)}"
        )
    _click_exact_action(page, _ADD_NEW_LISTINGS)
    if not _wait_until_or_page_not_found(
        page,
        lambda: _is_listing_hub(page) or _is_step1_operable(page),
        timeout_s=20.0,
    ):
        raise RuntimeError(
            "点击 Add New Listings 后没有进入 Listing Creation/Listings Management。 "
            f"diagnostics={_diagnostics(page)}"
        )


def _open_step1_from_listing_creation(page: Any) -> None:
    """Advance either Listing Creation or Listings Management to Single Step 1."""

    if _is_step1_operable(page):
        return
    if not _is_listing_hub(page):
        raise RuntimeError(
            "当前 Makro 页面不是可识别的 Listing Creation/Listings Management，无法安全创建 Single Listing。 "
            f"diagnostics={_diagnostics(page)}"
        )

    if not _has_exact_action(page, _ADD_SINGLE_LISTING):
        if not _wait_for_stable_action(page, _ADD_NEW_LISTING, timeout_s=8.0):
            raise RuntimeError(
                "当前 listing hub 的 Add New Listing 没有形成稳定可点击状态。 "
                f"diagnostics={_diagnostics(page)}"
            )
        _click_exact_action(page, _ADD_NEW_LISTING)
        if not _wait_for_stable_action(page, _ADD_SINGLE_LISTING, timeout_s=10.0):
            raise RuntimeError(
                "点击 Add New Listing 后没有出现稳定且唯一可见的 Add Single Listing。 "
                f"diagnostics={_diagnostics(page)}"
            )
    elif not _wait_for_stable_action(page, _ADD_SINGLE_LISTING, timeout_s=5.0):
        raise RuntimeError(
            "当前 listing hub 的 Add Single Listing 没有形成稳定可点击状态。 "
            f"diagnostics={_diagnostics(page)}"
        )

    _click_exact_action(page, _ADD_SINGLE_LISTING)
    if not _wait_until_or_page_not_found(
        page,
        lambda: _is_step1_operable(page) or _is_dashboard(page),
        timeout_s=25.0,
    ):
        raise RuntimeError(
            "点击 Add Single Listing 后没有进入 Step 1 Vertical，也没有回到可恢复的 Dashboard。 "
            f"diagnostics={_diagnostics(page)}"
        )


def _recover_page_not_found_to_home(page: Any) -> None:
    """Recover only Makro's known SPA 404 route back to a recognized pre-Step-1 state."""

    page.goto(MAKRO_HOME_URL, wait_until="domcontentloaded", timeout=45_000)
    if not _wait_until_or_page_not_found(
        page,
        lambda: (
            _is_dashboard(page)
            or _is_listing_hub(page)
            or _is_step1_operable(page)
        ),
        timeout_s=20.0,
    ):
        raise RuntimeError(
            "从 Makro page-not-found 回到 Home 后仍没有形成可识别的 Dashboard/listing hub/Step 1。 "
            f"diagnostics={_diagnostics(page)}"
        )


def _prepare_new_listing_step1_page(page: Any) -> None:
    """Bring a new/owned Makro page to Step 1 through the real Seller Portal UI."""

    page.set_default_timeout(15_000)
    page_not_found_recoveries = 0

    for _ in range(12):
        try:
            if _has_password(page):
                raise RuntimeError("Makro 登录状态无效；请先在长期 Edge 中人工登录，再重试。")
            if _is_page_not_found(page):
                raise _PortalPageNotFound(
                    "Makro Seller Portal is currently on #dashboard/page-not-found."
                )
            if _is_step1_operable(page):
                return
            _reject_later_listing_stage(page)

            # Both old Listing Creation and current Listings Management are
            # resumable hubs. Never send them backward through Home/Dashboard.
            if _is_listing_hub(page):
                _open_step1_from_listing_creation(page)
                if _is_step1_operable(page):
                    return
                _reject_later_listing_stage(page)
                continue

            if _is_dashboard(page):
                _open_listing_creation_from_dashboard(page)
                continue

            current_url = str(getattr(page, "url", "") or "")
            if (
                not _is_makro_host(page)
                or not _is_single_listing_route(current_url)
                or _is_safe_pre_step1_single_route(page)
            ):
                page.goto(MAKRO_HOME_URL, wait_until="domcontentloaded", timeout=45_000)
                if not _wait_until_or_page_not_found(
                    page,
                    lambda: (
                        _is_dashboard(page)
                        or _is_listing_hub(page)
                        or _is_step1_operable(page)
                    ),
                    timeout_s=15.0,
                ):
                    raise RuntimeError(
                        "打开 Makro Seller Portal Home 后没有形成可识别的 Dashboard/listing hub/Step 1。 "
                        f"diagnostics={_diagnostics(page)}"
                    )
                continue

            raise RuntimeError(
                "当前 Add a Single Listing route 未形成可安全操作的 Step 1 Vertical 界面。 "
                f"diagnostics={_diagnostics(page)}"
            )
        except _PortalPageNotFound as exc:
            if page_not_found_recoveries >= _MAX_PAGE_NOT_FOUND_RECOVERIES:
                raise RuntimeError(
                    "Makro pre-Step-1 导航连续进入 #dashboard/page-not-found，"
                    f"已完成 {_MAX_PAGE_NOT_FOUND_RECOVERIES} 次自动 Home 恢复仍失败；已停止避免循环。 "
                    f"diagnostics={_diagnostics(page)}"
                ) from exc
            page_not_found_recoveries += 1
            _recover_page_not_found_to_home(page)
            continue

    raise RuntimeError(
        "Makro pre-Step-1 导航超过安全转换上限，仍未到达 Select Vertical。 "
        f"diagnostics={_diagnostics(page)}"
    )


def prepare_single_step1_page(harness: Any):
    """Return the unique Single Listing workflow page."""

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
            return page
        if _is_safe_pre_step1_single_route(page):
            _prepare_new_listing_step1_page(page)
            return page
        raise RuntimeError(
            "当前 Add Listing 标签页未形成可识别的 Step 1/2/3 工作流界面。"
            f" diagnostics={_diagnostics(page)}"
        )

    page = harness.ensure_page()
    _prepare_new_listing_step1_page(page)
    return page


def prepare_owned_step1_page(page: Any) -> None:
    """Prepare one Batch-owned tab through the same shared pre-Step-1 UI path."""

    _prepare_new_listing_step1_page(page)


__all__ = ["prepare_owned_step1_page", "prepare_single_step1_page"]
