from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

import makro_gui_workflow as workflow


def test_listing_stage_propagates_inspection_failure_instead_of_assuming_step1(monkeypatch) -> None:
    page = SimpleNamespace(url="https://seller.makro.co.za/listing/example")

    def broken(_page):
        raise RuntimeError("DOM detached during inspection")

    monkeypatch.setattr(workflow, "is_product_info_step", broken)

    with pytest.raises(RuntimeError, match="refusing to assume pre-Step1"):
        workflow._listing_stage(page)


def test_listing_stage_requires_positive_later_stage_evidence(monkeypatch) -> None:
    page = SimpleNamespace(url="https://seller.makro.co.za/listing/example")
    monkeypatch.setattr(workflow, "is_product_info_step", lambda _page: False)
    monkeypatch.setattr(workflow, "is_brand_step", lambda _page: False)
    assert workflow._listing_stage(page) == "pre_step1"

    monkeypatch.setattr(workflow, "is_brand_step", lambda _page: True)
    assert workflow._listing_stage(page) == "step2"

    monkeypatch.setattr(workflow, "is_product_info_step", lambda _page: True)
    assert workflow._listing_stage(page) == "step3"


def test_single_gui_bootstrap_uses_the_same_process_local_listing_intent() -> None:
    source = inspect.getsource(workflow.main)

    resolve_pos = source.index("listing_intent = current_listing_intent()")
    bootstrap_pos = source.index("hints = infer_listing_bootstrap(")
    handoff_pos = source.index("listing_intent=listing_intent")

    assert resolve_pos < bootstrap_pos < handoff_pos
    assert '"listing_intent": listing_intent' in source
