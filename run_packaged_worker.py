from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path

from makro_batch_job import main as batch_job_main
from makro_batch_source import main as batch_source_main
from makro_execute_listing import main as execute_main
from makro_gui_workflow import main as workflow_main
from makro_one_link import main as one_link_main
from makro_plan_listing import main as plan_listing_main
from makro_resolve_ai import main as resolve_ai_main


_HELPERS: dict[str, tuple[str, Callable[[], int]]] = {
    "workflow": ("makro_gui_workflow.py", workflow_main),
    "execute": ("makro_execute_listing.py", execute_main),
    "batch-source": ("makro_batch_source.py", batch_source_main),
    "batch-job": ("makro_batch_job.py", batch_job_main),
    "resolve-ai": ("makro_resolve_ai.py", resolve_ai_main),
    "plan-listing": ("makro_plan_listing.py", plan_listing_main),
    "one-link": ("makro_one_link.py", one_link_main),
}
_BY_SCRIPT = {target[0].casefold(): target for target in _HELPERS.values()}


def _self_test() -> int:
    # Starting Playwright without launching a browser verifies that its bundled
    # Node driver/package data survived PyInstaller. Production connects to the
    # user's existing Microsoft Edge session, so no Playwright browser image is
    # bundled into the installer.
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        engine = playwright.chromium.name
    print(
        json.dumps(
            {
                "ok": True,
                "worker": Path(sys.executable).name,
                "playwright_engine": engine,
                "helpers": sorted(_HELPERS),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0


def _usage() -> str:
    names = ", ".join(sorted(_HELPERS))
    return (
        "EcommerceAgentWorker is an internal process host.\n"
        f"Usage: EcommerceAgentWorker.exe --helper <{names}> [arguments...]\n"
        "It also accepts the canonical script name as argv[1] for nested workers."
    )


def main() -> int:
    argv = list(sys.argv[1:])
    if not argv or argv[0] in {"-h", "--help"}:
        print(_usage())
        return 0
    if argv[0] == "--self-test":
        return _self_test()

    if argv[0] == "--helper":
        if len(argv) < 2:
            print(_usage(), file=sys.stderr)
            return 2
        target = _HELPERS.get(argv[1].strip().casefold())
        rest = argv[2:]
    else:
        target = _BY_SCRIPT.get(Path(argv[0]).name.casefold())
        rest = argv[1:]

    if target is None:
        print(f"Unknown internal helper: {argv[0]}", file=sys.stderr)
        print(_usage(), file=sys.stderr)
        return 2

    script_name, helper_main = target
    original_argv = sys.argv
    try:
        sys.argv = [script_name, *rest]
        return int(helper_main())
    finally:
        sys.argv = original_argv


if __name__ == "__main__":
    raise SystemExit(main())
