from __future__ import annotations

import inspect

import pytest

import makro_batch_job
import makro_plan_listing


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


def test_planner_inherits_batch_owned_target_from_job_environment(monkeypatch) -> None:
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


def test_single_mode_keeps_unique_listing_tab_guard() -> None:
    source = inspect.getsource(makro_plan_listing.main)
    assert "if args.makro_target_id:" in source
    assert "_owned_listing_page(harness, args.makro_target_id)" in source
    assert "_assert_single_listing_tab(harness.context)" in source


def test_batch_refreshes_target_before_propagating_planner_ownership() -> None:
    source = inspect.getsource(makro_batch_job.main)
    assert "owned_target_id = page_target_id(page)" in source
    assert 'manifest["makro_target_id"] = owned_target_id' in source
    assert "os.environ[_BATCH_TARGET_ENV] = owned_target_id" in source
    assert "_prepare_step3(args, run_dir=run_dir, page=page, manifest=manifest)" in source
    assert "os.environ.pop(_BATCH_TARGET_ENV, None)" in source
