from __future__ import annotations

import inspect

import makro_fill
import makro_probe
from app.answer_resolver import NEEDS_REVIEW, resolve_field
from app.source_bundle import ProductSourceBundle


def _business_field(key: str, label: str) -> dict:
    return {
        "attribute_key": key,
        "label": label,
        "multi_value": False,
        "controls": [
            {
                "name": f"{key}_0_value",
                "field_kind": "select",
                "options": [],
            }
        ],
    }


def test_probe_default_path_reuses_edge_harness_and_never_closes_browser() -> None:
    args = makro_probe.build_parser().parse_args([])
    assert args.browser == "edge"
    assert args.cdp_port == 9222

    source = inspect.getsource(makro_probe.main)
    assert "EdgeHarness(" in source
    assert "launch_persistent_context" not in source
    assert "context.close()" not in source
    assert "browser.close()" not in source
    assert "harness.detach()" in source


def test_fill_rebinds_domain_adapter_after_page_recovery() -> None:
    source = inspect.getsource(makro_fill.main)
    recovery = "page = harness.ensure_page()"
    rebind = "adapter = MakroDomainAdapter(page)"

    recovery_index = source.index(recovery)
    rebind_index = source.index(rebind, recovery_index)
    assert rebind_index > recovery_index


def test_fulfillment_business_fields_never_reach_fallback() -> None:
    calls: list[str] = []

    class ExplodingFallback:
        name = "must-not-run"

        def try_resolve(self, semantic_field, bundle):
            calls.append(str(semantic_field.get("attribute_key")))
            raise AssertionError("business field must never reach semantic fallback")

    bundle = ProductSourceBundle()
    for key, label in (
        ("service_profile", "Fulfillment By"),
        ("forbid_shipping", "Selling region preference"),
    ):
        answer = resolve_field(
            _business_field(key, label),
            bundle,
            fallback=ExplodingFallback(),
        )
        assert answer.status == NEEDS_REVIEW
        assert "经营字段" in answer.detail

    assert calls == []
