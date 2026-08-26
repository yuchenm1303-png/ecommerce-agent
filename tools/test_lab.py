#!/usr/bin/env python3
"""Zero-cost, no-retailer regression launcher for ecommerce-agent.

This command deliberately runs only curated offline tests. It strips paid AI
credentials, blocks external sockets inside pytest, and points HTTP(S) proxy
variables at a closed loopback port so accidental child-process web traffic also
fails locally. Real Makro/Amazon smoke tests are intentionally outside this tool.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SUITES: dict[str, tuple[str, ...]] = {
    "source": (
        "tests/test_supplier_url_identity.py",
        "tests/test_source_capture_cache_identity.py",
    ),
    "vertical": (
        "tests/test_makro_vertical_search_boundary.py",
        "tests/test_makro_vertical_click_binding.py",
        "tests/test_makro_vertical_constraint_guard.py",
        "tests/test_makro_vertical_relation_policy.py",
        "tests/test_makro_vertical_resolution.py",
        "tests/test_vertical_incident_replay.py",
    ),
    "brand": (
        "tests/test_brand_selection.py",
        "tests/test_step3_transition_recovery.py",
    ),
    "ownership": (
        "tests/test_browser_page_owner.py",
        "tests/test_batch_planner_target_ownership.py",
        "tests/test_makro_workflow_state_machine.py",
    ),
    "cdp": (
        "tests/test_browser_session.py",
        "tests/test_browser_session_lease_contract.py",
        "tests/test_browser_session_lease_multiprocess.py",
    ),
    "schema": (
        "tests/test_live_schema.py",
        "tests/test_live_schema_draft_identity.py",
        "tests/test_makro_plan_listing.py",
    ),
    "resolver": (
        "tests/test_makro_resolve_ai.py",
        "tests/test_transient_ai_retry.py",
        "tests/test_transient_ai_transport_contract.py",
    ),
    "fill_plan": (
        "tests/test_fill_plan.py",
    ),
    "batch": (
        "tests/test_batch_architecture_contract.py",
        "tests/test_batch_step1_timeout_recovery.py",
        "tests/test_batch_planner_target_ownership.py",
    ),
    "diagnostics": (
        "tests/test_task_failure_diagnostics.py",
        "tests/test_task_failure_telemetry_contract.py",
    ),
}

_SECRET_ENV_KEYS = {
    "AI_API_KEY",
    "OPENAI_API_KEY",
    "DASHSCOPE_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run curated ecommerce-agent regression suites with zero external network and zero paid AI credentials."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--all", action="store_true", help="Run every curated offline suite.")
    mode.add_argument("--list", action="store_true", help="List available suites and exit.")
    parser.add_argument(
        "--suite",
        action="append",
        choices=tuple(SUITES),
        default=[],
        help="Run one suite; repeat to run several. With no selection, --all is assumed.",
    )
    parser.add_argument(
        "--stress-rounds",
        type=int,
        default=3,
        help="Cross-process CDP serialization rounds used by the cdp suite (default: 3).",
    )
    parser.add_argument("--fail-fast", action="store_true", help="Stop after the first failing suite.")
    parser.add_argument("--verbose", action="store_true", help="Show normal pytest verbosity instead of -q.")
    parser.add_argument("--json-report", default="", help="Optional path for a machine-readable Test Lab summary.")
    return parser


def _safe_environment(stress_rounds: int) -> dict[str, str]:
    env = dict(os.environ)
    for key in _SECRET_ENV_KEYS:
        env.pop(key, None)
    env["ECOMMERCE_TEST_LAB"] = "1"
    env["ECOMMERCE_TEST_LAB_NO_EXTERNAL"] = "1"
    env["ECOMMERCE_TEST_LAB_STRESS_ROUNDS"] = str(max(1, min(int(stress_rounds), 100)))
    # Child Python processes that do not load the pytest plugin still cannot use
    # normal HTTP(S) clients to escape: their proxy target is a closed loopback port.
    dead_proxy = "http://127.0.0.1:9"
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env[key] = dead_proxy
    env["NO_PROXY"] = "127.0.0.1,localhost,::1"
    env["no_proxy"] = env["NO_PROXY"]
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    return env


def _selected_suites(args: argparse.Namespace) -> list[str]:
    if args.all or not args.suite:
        return list(SUITES)
    seen: set[str] = set()
    result: list[str] = []
    for name in args.suite:
        if name not in seen:
            seen.add(name)
            result.append(name)
    return result


def _suite_nodes(name: str) -> list[str]:
    missing = [path for path in SUITES[name] if not (ROOT / path).exists()]
    if missing:
        raise RuntimeError(f"Test Lab suite {name!r} references missing tests: {missing}")
    return list(SUITES[name])


def _run_suite(name: str, *, env: dict[str, str], verbose: bool) -> dict[str, object]:
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-p",
        "app.test_lab_pytest_plugin",
        "--disable-warnings",
    ]
    if not verbose:
        command.append("-q")
    command.extend(_suite_nodes(name))

    print(f"\n===== TEST LAB · {name.upper()} =====", flush=True)
    started = time.monotonic()
    result = subprocess.run(command, cwd=ROOT, env=env, check=False)
    elapsed = round(time.monotonic() - started, 3)
    status = "PASS" if result.returncode == 0 else "FAIL"
    print(f"TEST_LAB_SUITE {name} {status} elapsed_s={elapsed}", flush=True)
    return {
        "suite": name,
        "status": status,
        "returncode": int(result.returncode),
        "elapsed_seconds": elapsed,
        "tests": list(SUITES[name]),
    }


def _write_report(path: str, payload: dict[str, object]) -> None:
    target = Path(path)
    if not target.is_absolute():
        target = ROOT / target
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"JSON report: {target.resolve()}")


def main() -> int:
    args = _parser().parse_args()
    if args.stress_rounds < 1 or args.stress_rounds > 100:
        raise SystemExit("--stress-rounds must be between 1 and 100")

    if args.list:
        print("Available zero-cost Test Lab suites:")
        for name, tests in SUITES.items():
            print(f"  {name:<12} {len(tests)} test module(s)")
        print("\nReal Amazon/Makro smoke tests are intentionally not part of Test Lab.")
        return 0

    selected = _selected_suites(args)
    env = _safe_environment(args.stress_rounds)
    started = time.monotonic()
    results: list[dict[str, object]] = []

    print("===== ECOMMERCE-AGENT TEST LAB =====")
    print("mode=offline_replay external_network=BLOCKED paid_ai_credentials=STRIPPED makro_writes=0")
    print(f"suites={','.join(selected)} cdp_stress_rounds={args.stress_rounds}")

    for name in selected:
        result = _run_suite(name, env=env, verbose=args.verbose)
        results.append(result)
        if result["status"] == "FAIL" and args.fail_fast:
            break

    elapsed = round(time.monotonic() - started, 3)
    failed = [item["suite"] for item in results if item["status"] != "PASS"]
    summary: dict[str, object] = {
        "schema_version": 1,
        "mode": "offline_replay",
        "external_network": "blocked",
        "paid_ai_credentials": "stripped",
        "real_amazon_requests": 0,
        "real_makro_writes": 0,
        "stress_rounds": args.stress_rounds,
        "elapsed_seconds": elapsed,
        "status": "PASS" if not failed and len(results) == len(selected) else "FAIL",
        "failed_suites": failed,
        "results": results,
    }
    if args.json_report:
        _write_report(args.json_report, summary)

    print("\n===== TEST LAB SUMMARY =====")
    for item in results:
        print(f"{str(item['suite']).upper():<14} {item['status']}")
    if len(results) < len(selected):
        print(f"NOT_RUN        {len(selected) - len(results)} suite(s) (fail-fast)")
    print("EXTERNAL NET   BLOCKED")
    print("PAID AI KEYS   STRIPPED")
    print("AMAZON REQUEST 0")
    print("MAKRO WRITES   0")
    print(f"OVERALL        {summary['status']} ({elapsed}s)")
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
