#!/usr/bin/env python3
"""Zero-cost, no-retailer regression launcher for ecommerce-agent.

``--all`` executes the repository's complete pytest suite except tests explicitly
marked ``probe``. Targeted suites remain available for fast stage-level loops.
Every Test Lab pytest process strips paid AI credentials and installs the socket
safety plugin; real Amazon/Makro smoke tests intentionally live outside this tool.
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
    "fill_plan": ("tests/test_fill_plan.py",),
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
        description="Run ecommerce-agent regressions with external network blocked and paid AI credentials stripped."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--all",
        action="store_true",
        help="Run all repository tests except tests explicitly marked probe (default when no --suite is given).",
    )
    mode.add_argument("--list", action="store_true", help="List targeted suites and exit.")
    parser.add_argument(
        "--suite",
        action="append",
        choices=tuple(SUITES),
        default=[],
        help="Run one targeted suite; repeat to run several.",
    )
    parser.add_argument(
        "--stress-rounds",
        type=int,
        default=3,
        help="Cross-process CDP serialization rounds used by the cdp tests (default: 3).",
    )
    parser.add_argument("--fail-fast", action="store_true", help="Stop pytest / suite dispatch after the first failure.")
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

    # Nested Python workers that do not load the pytest socket plugin still fail
    # normal HTTP(S) traffic locally instead of reaching a paid provider/retailer.
    dead_proxy = "http://127.0.0.1:9"
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env[key] = dead_proxy
    env["NO_PROXY"] = "127.0.0.1,localhost,::1"
    env["no_proxy"] = env["NO_PROXY"]
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    return env


def _targeted_names(args: argparse.Namespace) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for name in args.suite:
        if name not in seen:
            seen.add(name)
            result.append(name)
    return result


def _assert_nodes_exist(nodes: tuple[str, ...]) -> None:
    missing = [path for path in nodes if not (ROOT / path).exists()]
    if missing:
        raise RuntimeError(f"Test Lab references missing test paths: {missing}")


def _pytest_command(
    nodes: tuple[str, ...],
    *,
    verbose: bool,
    fail_fast: bool,
    exclude_probe: bool,
) -> list[str]:
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
    if fail_fast:
        command.append("-x")
    if exclude_probe:
        command.extend(["-m", "not probe"])
    command.extend(nodes)
    return command


def _run_pytest(
    label: str,
    nodes: tuple[str, ...],
    *,
    env: dict[str, str],
    verbose: bool,
    fail_fast: bool,
    exclude_probe: bool,
) -> dict[str, object]:
    _assert_nodes_exist(nodes)
    command = _pytest_command(
        nodes,
        verbose=verbose,
        fail_fast=fail_fast,
        exclude_probe=exclude_probe,
    )
    print(f"\n===== TEST LAB · {label.upper()} =====", flush=True)
    started = time.monotonic()
    result = subprocess.run(command, cwd=ROOT, env=env, check=False)
    elapsed = round(time.monotonic() - started, 3)
    status = "PASS" if result.returncode == 0 else "FAIL"
    print(f"TEST_LAB_SUITE {label} {status} elapsed_s={elapsed}", flush=True)
    return {
        "suite": label,
        "status": status,
        "returncode": int(result.returncode),
        "elapsed_seconds": elapsed,
        "tests": list(nodes),
        "probe_tests_excluded": bool(exclude_probe),
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
        print("Targeted zero-cost Test Lab suites:")
        for name, tests in SUITES.items():
            print(f"  {name:<12} {len(tests)} test module(s)")
        print("\n--all/default runs the complete tests/ tree with '-m not probe'.")
        print("Real Amazon/Makro probe tests are intentionally excluded.")
        return 0

    env = _safe_environment(args.stress_rounds)
    started = time.monotonic()
    results: list[dict[str, object]] = []
    targeted = _targeted_names(args)
    full_offline = bool(args.all or not targeted)

    print("===== ECOMMERCE-AGENT TEST LAB =====")
    print("mode=offline_replay external_network=BLOCKED paid_ai_credentials=STRIPPED makro_writes=0")
    if full_offline:
        print(f"scope=ALL_NON_PROBE_TESTS cdp_stress_rounds={args.stress_rounds}")
        results.append(
            _run_pytest(
                "full_offline",
                ("tests",),
                env=env,
                verbose=args.verbose,
                fail_fast=args.fail_fast,
                exclude_probe=True,
            )
        )
    else:
        print(f"suites={','.join(targeted)} cdp_stress_rounds={args.stress_rounds}")
        for name in targeted:
            result = _run_pytest(
                name,
                SUITES[name],
                env=env,
                verbose=args.verbose,
                fail_fast=args.fail_fast,
                exclude_probe=True,
            )
            results.append(result)
            if result["status"] == "FAIL" and args.fail_fast:
                break

    elapsed = round(time.monotonic() - started, 3)
    failed = [item["suite"] for item in results if item["status"] != "PASS"]
    expected_runs = 1 if full_offline else len(targeted)
    summary: dict[str, object] = {
        "schema_version": 1,
        "mode": "offline_replay",
        "scope": "all_non_probe_tests" if full_offline else "targeted_suites",
        "external_network": "blocked",
        "paid_ai_credentials": "stripped",
        "probe_tests_excluded": True,
        "real_amazon_requests": 0,
        "real_makro_writes": 0,
        "stress_rounds": args.stress_rounds,
        "elapsed_seconds": elapsed,
        "status": "PASS" if not failed and len(results) == expected_runs else "FAIL",
        "failed_suites": failed,
        "results": results,
    }
    if args.json_report:
        _write_report(args.json_report, summary)

    print("\n===== TEST LAB SUMMARY =====")
    for item in results:
        print(f"{str(item['suite']).upper():<14} {item['status']}")
    if not full_offline and len(results) < expected_runs:
        print(f"NOT_RUN        {expected_runs - len(results)} suite(s) (fail-fast)")
    print("PROBE TESTS    EXCLUDED")
    print("EXTERNAL NET   BLOCKED")
    print("PAID AI KEYS   STRIPPED")
    print("AMAZON REQUEST 0")
    print("MAKRO WRITES   0")
    print(f"OVERALL        {summary['status']} ({elapsed}s)")
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
