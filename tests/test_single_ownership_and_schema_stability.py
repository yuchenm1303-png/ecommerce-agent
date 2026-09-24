from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

import app.makro.sections as sections
import makro_gui_workflow as workflow


class _FakePage:
    def __init__(self) -> None:
        self.url = "about:blank"
        self.timeout = 0
        self.goto_calls: list[tuple[str, str, int]] = []
        self.waits: list[int] = []
        self.closed = False

    def set_default_timeout(self, value: int) -> None:
        self.timeout = value

    def goto(self, url: str, *, wait_until: str, timeout: int):
        self.goto_calls.append((url, wait_until, timeout))
        self.url = url
        return SimpleNamespace(from_service_worker=False)

    def wait_for_timeout(self, value: int) -> None:
        self.waits.append(value)

    def close(self) -> None:
        self.closed = True


class _FakeCdpSession:
    def __init__(self) -> None:
        self.commands: list[tuple[str, object | None]] = []
        self.detached = False

    def on(self, _event: str, _handler) -> None:
        return None

    def send(self, command: str, payload=None) -> None:
        self.commands.append((command, payload))

    def detach(self) -> None:
        self.detached = True


class _FakeContext:
    def __init__(self, page: _FakePage) -> None:
        self.created = page
        self.new_page_calls = 0
        self.pages: list[_FakePage] = []
        self.cdp_sessions: list[_FakeCdpSession] = []

    def new_page(self) -> _FakePage:
        self.new_page_calls += 1
        if self.created not in self.pages:
            self.pages.append(self.created)
        return self.created

    def new_cdp_session(self, _page: _FakePage) -> _FakeCdpSession:
        session = _FakeCdpSession()
        self.cdp_sessions.append(session)
        return session


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
    assert harness.context.pages == [page]
    assert harness.page is page
    assert page.timeout == 15_000
    assert page.goto_calls == [(workflow.MAKRO_HOME_URL, "commit", 20_000)]
    assert harness.context.cdp_sessions[0].commands == [
        ("Network.enable", None),
        ("Network.setBypassServiceWorker", {"bypass": True}),
    ]
    assert harness.context.cdp_sessions[0].detached is True


def test_fresh_owned_tab_retries_with_new_target_and_closes_failed_blank(monkeypatch) -> None:
    class _FailingPage(_FakePage):
        def goto(self, url: str, *, wait_until: str, timeout: int):
            self.goto_calls.append((url, wait_until, timeout))
            raise TimeoutError("simulated stalled navigation")

    class _SequenceContext:
        def __init__(self, pages: list[_FakePage]) -> None:
            self.pending = list(pages)
            self.pages: list[_FakePage] = []
            self.cdp_sessions: list[_FakeCdpSession] = []

        def new_page(self) -> _FakePage:
            page = self.pending.pop(0)
            self.pages.append(page)
            return page

        def new_cdp_session(self, _page: _FakePage) -> _FakeCdpSession:
            session = _FakeCdpSession()
            self.cdp_sessions.append(session)
            return session

    failed = _FailingPage()
    recovered = _FakePage()
    context = _SequenceContext([failed, recovered])
    harness = SimpleNamespace(context=context, page=None)
    monkeypatch.setattr(workflow, "page_target_id", lambda current: "target-recovered")

    owned, target_id = workflow._create_fresh_owned_page(harness)

    assert failed.closed is True
    assert recovered.closed is False
    assert owned is recovered
    assert target_id == "target-recovered"
    assert len(context.cdp_sessions) == 2
    assert all(session.detached for session in context.cdp_sessions)


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


def test_section_scan_accumulates_window_and_nested_container_observations() -> None:
    source = inspect.getsource(sections.scan_section_fields)

    assert "capture_controls(page" in source
    assert "scroll_window(page)" in source
    assert "find_scroll_containers(page)" in source
    assert "scroll_container(page, container_path)" in source
    assert "merged = merge_scans(scans)" in source
    assert "item.get(\"path\", \"\").startswith(prefix)" in source


def test_section_scan_has_no_retired_consecutive_snapshot_gate() -> None:
    source = inspect.getsource(sections)

    # Current scanner mechanically unions all observations gathered while scrolling.
    # Stable schema identity is enforced later at the planner/executor boundary,
    # so the retired per-section _scan_section_once snapshot gate must stay gone.
    assert "def _scan_section_once" not in source
    assert "def _section_scan_signature" not in source
    assert "merge_scans(scans)" in source


def test_real_execution_preserves_read_only_target_ownership_contract() -> None:
    source = (Path(__file__).resolve().parents[1] / "gui" / "real_execution.py").read_text(
        encoding="utf-8"
    )

    assert 'workflow_manifest_path = run_dir / "run-manifest.json"' in source
    assert 'args.extend(["--makro-target-id", makro_target_id])' in source
    assert '"fresh_dedicated_tab", "resume_exact_page"' in source
