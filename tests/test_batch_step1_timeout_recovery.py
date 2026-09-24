from __future__ import annotations

from types import SimpleNamespace

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

import makro_batch_job as batch_job


def test_owned_step1_reenters_state_machine_after_transient_navigation_timeout(monkeypatch):
    calls: list[str] = []
    waits: list[int] = []
    page = SimpleNamespace(
        url="https://seller.makro.co.za/index.html#dashboard/home-page",
        wait_for_timeout=lambda value: waits.append(int(value)),
    )

    def prepare(_page):
        calls.append("prepare")
        if len(calls) == 1:
            raise PlaywrightTimeoutError("simulated goto timeout")

    monkeypatch.setattr(batch_job, "prepare_owned_step1_page", prepare)
    monkeypatch.setattr(batch_job, "_listing_stage", lambda _page: "pre_step1")

    assert batch_job._prepare_owned_step1_with_recovery(page) is page
    assert calls == ["prepare", "prepare"]
    assert waits == [batch_job._STEP1_TRANSIENT_BACKOFF_MS]


def test_owned_step1_timeout_does_not_push_an_advanced_owned_tab_backwards(monkeypatch):
    page = SimpleNamespace(
        url="https://seller.makro.co.za/index.html#dashboard/addListings/single?vertical=x",
        wait_for_timeout=lambda _value: None,
    )

    def prepare(_page):
        raise PlaywrightTimeoutError("transition timeout")

    monkeypatch.setattr(batch_job, "prepare_owned_step1_page", prepare)
    monkeypatch.setattr(batch_job, "_listing_stage", lambda _page: "step2")

    assert batch_job._prepare_owned_step1_with_recovery(page) is page


def test_owned_step1_timeout_remains_bounded(monkeypatch):
    page = SimpleNamespace(url="about:blank", wait_for_timeout=lambda _value: None)
    calls = 0

    def prepare(_page):
        nonlocal calls
        calls += 1
        raise PlaywrightTimeoutError("persistent timeout")

    monkeypatch.setattr(batch_job, "prepare_owned_step1_page", prepare)
    monkeypatch.setattr(batch_job, "_listing_stage", lambda _page: "pre_step1")

    with pytest.raises(PlaywrightTimeoutError):
        batch_job._prepare_owned_step1_with_recovery(page)
    assert calls == batch_job._STEP1_TRANSIENT_ATTEMPTS
