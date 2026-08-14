from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import app.makro.sections as sections
import makro_gui_workflow as workflow


class _FakePage:
    def __init__(self) -> None:
        self.url = "about:blank"
        self.timeout = 0
        self.goto_calls: list[tuple[str, str, int]] = []
        self.waits: list[int] = []

    def set_default_timeout(self, value: int) -> None:
        self.timeout = value

    def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
        self.goto_calls.append((url, wait_until, timeout))
        self.url = url

    def wait_for_timeout(self, value: int) -> None:
        self.waits.append(value)


class _FakeContext:
    def __init__(self, page: _FakePage) -> None:
        self.created = page
        self.new_page_calls = 0

    def new_page(self) -> _FakePage:
        self.new_page_calls += 1
        return self.created


class _FakeHarness:
    def __init__(self, page: _FakePage) -> None:
        self.context = _FakeContext(page)
        self.page = None


def test_fresh_full_run_creates_dedicated_owned_makro_tab(monkeypatch) -> None:
    page = _FakePage()
    harness = _FakeHarness(page)
    monkeypatch.setattr(workflow, "page_target_id", lambda current: "target-fresh-123")

    owned, target_id = workflow._create_fresh_owned_page(harness)

    assert owned is page
    assert target_id == "target-fresh-123"
    assert harness.context.new_page_calls == 1
    assert harness.page is page
    assert page.timeout == 15_000
    assert page.goto_calls == [(workflow.MAKRO_HOME_URL, "commit", 20_000)]


def test_owned_checkpoint_refreshes_target_after_page_replacement(monkeypatch, tmp_path: Path) -> None:
    page = _FakePage()
    page.url = "https://seller.makro.co.za/#dashboard/addListings/single?vertical=table_lamp&brand=Gritin"
    manifest = {
        "ownership_mode": "fresh_dedicated_tab",
        "makro_target_id": "target-before-transition",
    }
    monkeypatch.setattr(workflow, "page_target_id", lambda current: "target-after-transition")

    manifest_path = tmp_path / "run-manifest.json"
    workflow._record_listing_checkpoint(
        manifest_path,
        manifest,
        page=page,
        status="step2_complete",
        vertical="table_lamp",
        brand="Gritin",
    )

    assert manifest["makro_target_id"] == "target-after-transition"
    assert manifest["page_url"] == page.url


def test_fill_plan_command_binds_exact_owned_target() -> None:
    args = SimpleNamespace(
        product_url="https://example.com/product",
        profile_dir="browser_profiles/makro-edge",
        cdp_port=9222,
        scroll_wait_ms=250,
        max_scroll_steps=200,
    )
    resolver_manifest = {
        "primary_product_url": args.product_url,
        "outputs": {
            "final_decisions": "decisions.json",
            "primary_source_snapshot": "source-snapshot.json",
            "primary_source_product_images": ["image-1.jpg"],
        },
    }

    command = workflow._plan_command(
        args,
        live_schema=Path("live-schema.json"),
        vertical="table_lamp",
        resolver_manifest=resolver_manifest,
        output_root=Path("plan-output"),
        makro_target_id="target-fresh-123",
    )

    index = command.index("--makro-target-id")
    assert command[index + 1] == "target-fresh-123"


class _ScanPage:
    def __init__(self) -> None:
        self.waits: list[int] = []

    def wait_for_timeout(self, value: int) -> None:
        self.waits.append(value)


def test_section_scan_waits_until_two_consecutive_full_contracts_match(monkeypatch) -> None:
    page = _ScanPage()
    passes = iter(
        [
            [{"sig": "a"}],
            [{"sig": "a"}, {"sig": "b"}],
            [{"sig": "a"}, {"sig": "b"}],
        ]
    )
    calls = 0

    def fake_scan_once(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return next(passes)

    monkeypatch.setattr(sections, "_scan_section_once", fake_scan_once)
    monkeypatch.setattr(
        sections,
        "_section_scan_signature",
        lambda controls: tuple(item["sig"] for item in controls),
    )

    result = sections.scan_section_fields(page, "#additional-description", wait_ms=10)

    assert [item["sig"] for item in result] == ["a", "b"]
    assert calls == 3


def test_section_scan_refuses_schema_that_never_stabilizes(monkeypatch) -> None:
    page = _ScanPage()
    passes = iter(
        [
            [{"sig": "a"}],
            [{"sig": "a"}, {"sig": "b"}],
            [{"sig": "a"}, {"sig": "b"}, {"sig": "c"}],
            [{"sig": "a"}, {"sig": "b"}, {"sig": "c"}, {"sig": "d"}],
        ]
    )
    monkeypatch.setattr(sections, "_scan_section_once", lambda *_args, **_kwargs: next(passes))
    monkeypatch.setattr(
        sections,
        "_section_scan_signature",
        lambda controls: tuple(item["sig"] for item in controls),
    )

    with pytest.raises(RuntimeError, match="did not stabilize"):
        sections.scan_section_fields(page, "#additional-description", wait_ms=10)


def test_real_execution_preserves_read_only_target_ownership_contract() -> None:
    source = (Path(__file__).resolve().parents[1] / "gui" / "real_execution.py").read_text(
        encoding="utf-8"
    )

    assert 'workflow_manifest_path = run_dir / "run-manifest.json"' in source
    assert 'args.extend(["--makro-target-id", makro_target_id])' in source
    assert '"fresh_dedicated_tab", "resume_exact_page"' in source
