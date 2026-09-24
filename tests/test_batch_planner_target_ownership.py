from __future__ import annotations

import inspect

import pytest

import makro_batch_job
import makro_plan_listing
from app import batch_step3_prepare


PRODUCT_URL = "https://example.test/product/123"


def _plan_args() -> list[str]:
    return [
        "--decision-packet",
        "ai-decisions.json",
        "--live-schema",
        "live-schema.json",
        "--product-url",
        PRODUCT_URL,
        "--expected-vertical",
        "massager",
    ]


class FakePage:
    def __init__(self) -> None:
        self.timeout = 0

    def set_default_timeout(self, value: int) -> None:
        self.timeout = value


class FakeHarness:
    def __init__(self) -> None:
        self.context = object()
        self.page = None


class FakeAdapter:
    def __init__(self, page, *, listing: bool = True) -> None:
        self.page = page
        self.listing = listing

    def is_listing_page(self) -> bool:
        return self.listing


def test_planner_inherits_batch_owned_target_as_metadata(monkeypatch) -> None:
    monkeypatch.setenv(makro_plan_listing._BATCH_TARGET_ENV, "target-job-002")
    args = makro_plan_listing.build_parser().parse_args(_plan_args())
    assert args.makro_target_id == "target-job-002"


def test_explicit_planner_target_overrides_environment(monkeypatch) -> None:
    monkeypatch.setenv(makro_plan_listing._BATCH_TARGET_ENV, "target-env")
    args = makro_plan_listing.build_parser().parse_args(
        _plan_args() + ["--makro-target-id", "target-cli"]
    )
    assert args.makro_target_id == "target-cli"


def test_owned_planner_resolves_exact_target_and_sets_harness_page(monkeypatch) -> None:
    harness = FakeHarness()
    page = FakePage()
    seen: list[tuple[object, str]] = []

    def find_exact(context, target_id):
        seen.append((context, target_id))
        return page

    monkeypatch.setattr(makro_plan_listing, "find_page_by_target_id", find_exact)
    monkeypatch.setattr(
        makro_plan_listing,
        "MakroDomainAdapter",
        lambda current: FakeAdapter(current, listing=True),
    )

    resolved = makro_plan_listing._owned_listing_page(harness, "target-owned")

    assert resolved is page
    assert harness.page is page
    assert page.timeout == 15_000
    assert seen == [(harness.context, "target-owned")]


def test_owned_planner_never_falls_back_when_target_cannot_be_resolved(monkeypatch) -> None:
    harness = FakeHarness()

    def missing(_context, _target_id):
        raise RuntimeError("target missing")

    monkeypatch.setattr(makro_plan_listing, "find_page_by_target_id", missing)

    with pytest.raises(RuntimeError, match="target missing"):
        makro_plan_listing._owned_listing_page(harness, "gone")
    assert harness.page is None


def test_owned_planner_rejects_target_that_is_no_longer_a_listing(monkeypatch) -> None:
    harness = FakeHarness()
    page = FakePage()
    monkeypatch.setattr(
        makro_plan_listing,
        "find_page_by_target_id",
        lambda _context, _target_id: page,
    )
    monkeypatch.setattr(
        makro_plan_listing,
        "MakroDomainAdapter",
        lambda current: FakeAdapter(current, listing=False),
    )

    with pytest.raises(RuntimeError, match="no longer points at an Add Listing page"):
        makro_plan_listing._owned_listing_page(harness, "wrong-page")


def test_scan_mode_keeps_unique_listing_tab_guard() -> None:
    source = inspect.getsource(makro_plan_listing._scan_live_schema)
    assert "if args.makro_target_id:" in source
    assert "_owned_listing_page(harness, args.makro_target_id)" in source
    assert "_assert_single_listing_tab(harness.context)" in source


def test_final_plan_mode_cannot_reenter_batch_browser_transport() -> None:
    source = inspect.getsource(makro_plan_listing._plan_from_captured_schema)
    assert "EdgeHarness" not in source
    assert "sync_playwright" not in source
    assert "connect_over_cdp" not in source


def test_batch_refreshes_target_then_releases_browser_before_final_planning() -> None:
    source = inspect.getsource(makro_batch_job.main)
    assert "owned_target_id = page_target_id(page)" in source
    assert 'manifest["makro_target_id"] = owned_target_id' in source
    assert "capture_batch_step3_schema(" in source
    assert "harness.detach()" in source
    assert "complete_batch_step3_from_schema(" in source
    assert source.index("harness.detach()") < source.index("complete_batch_step3_from_schema(")

    planner = inspect.getsource(batch_step3_prepare.complete_batch_step3_from_schema)
    assert 'makro_target_id=str(manifest.get("makro_target_id") or "")' in planner
    assert "EdgeHarness" not in planner
    assert "sync_playwright" not in planner
