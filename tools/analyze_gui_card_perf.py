from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path


MIN_REPLACEMENT_SCORE_IMPROVEMENT_PERCENT = 2.0
MAX_START_GAP_REGRESSION_PERCENT = 15.0
CADENCE_ONLY = {"baseline_60hz", "baseline_72hz", "baseline_90hz"}
REFERENCE_ONLY = {"frozen_fast", "no_scale_control"}
BASELINE = "baseline_frozen"
CROSSOVER_WEIGHT = 0.80
SINGLE_WEIGHT = 0.20


def _median(rows: list[dict], key: str) -> float:
    values = [float(row.get(key, 0.0)) for row in rows]
    return statistics.median(values) if values else 0.0


def _ratio(value: float, baseline: float, *, floor: float = 1e-6) -> float:
    return value / max(floor, baseline)


def _load_parity(path: Path | None) -> dict[str, bool]:
    if path is None:
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise SystemExit("parity file must be a JSON object mapping strategy -> true/false")
    return {str(key): bool(value) for key, value in raw.items()}


def _summaries(paths: list[Path]) -> dict[tuple[str, str, str], dict[str, float]]:
    groups: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        for row in payload.get("runs", []):
            strategy = str(row["strategy"])
            profile = str(row.get("profile", "unknown"))
            scenario = str(row.get("scenario", "single"))
            groups[(strategy, profile, scenario)].append(row)

    result: dict[tuple[str, str, str], dict[str, float]] = {}
    for key, rows in groups.items():
        result[key] = {
            "runs": float(len(rows)),
            "target_hz": _median(rows, "target_hz"),
            "p95": _median(rows, "frame_p95_ms"),
            "p99": _median(rows, "frame_p99_ms"),
            "long_rate": _median(rows, "long_1_5x_rate"),
            "long_2x_rate": _median(rows, "long_2x_rate"),
            "cpu": _median(rows, "cpu_core_percent"),
            "start_gap": _median(rows, "transition_start_gap_p95_ms"),
            "prepare": _median(rows, "transition_prepare_p95_ms"),
            "tick": _median(rows, "tick_work_p95_ms"),
        }
    return result


def _cell_score(candidate: dict[str, float], baseline: dict[str, float], scenario: str) -> float:
    if scenario == "crossover":
        return (
            0.30 * _ratio(candidate["p95"], baseline["p95"])
            + 0.45 * _ratio(candidate["p99"], baseline["p99"])
            + 0.10 * _ratio(candidate["long_rate"] + 0.005, baseline["long_rate"] + 0.005)
            + 0.10 * _ratio(candidate["start_gap"], baseline["start_gap"])
            + 0.05 * _ratio(candidate["cpu"] + 1.0, baseline["cpu"] + 1.0)
        )
    return (
        0.32 * _ratio(candidate["p95"], baseline["p95"])
        + 0.35 * _ratio(candidate["p99"], baseline["p99"])
        + 0.10 * _ratio(candidate["long_rate"] + 0.005, baseline["long_rate"] + 0.005)
        + 0.13 * _ratio(candidate["start_gap"], baseline["start_gap"])
        + 0.10 * _ratio(candidate["cpu"] + 1.0, baseline["cpu"] + 1.0)
    )


def _strategy_report(
    name: str,
    summaries: dict[tuple[str, str, str], dict[str, float]],
) -> dict[str, object]:
    cells: list[dict[str, object]] = []
    hz_mismatch = False
    max_start_gap_regression = 0.0

    baseline_keys = [
        (profile, scenario, row)
        for (strategy, profile, scenario), row in summaries.items()
        if strategy == BASELINE
    ]
    for profile, scenario, base in baseline_keys:
        candidate = summaries.get((name, profile, scenario))
        if candidate is None:
            continue
        hz_delta = abs(candidate["target_hz"] - base["target_hz"])
        if hz_delta > 0.5:
            hz_mismatch = True
        score = _cell_score(candidate, base, scenario)
        start_reg = (
            (candidate["start_gap"] - base["start_gap"]) / base["start_gap"] * 100.0
            if base["start_gap"] > 0.0
            else 0.0
        )
        max_start_gap_regression = max(max_start_gap_regression, start_reg)
        cells.append(
            {
                "profile": profile,
                "scenario": scenario,
                "score": score,
                "score_improvement_percent": (1.0 - score) * 100.0,
                "p95": candidate["p95"],
                "p99": candidate["p99"],
                "long_rate": candidate["long_rate"],
                "start_gap": candidate["start_gap"],
                "cpu": candidate["cpu"],
                "target_hz": candidate["target_hz"],
                "baseline_target_hz": base["target_hz"],
                "hz_delta": hz_delta,
                "start_gap_regression_percent": start_reg,
            }
        )

    scenario_scores: dict[str, float] = {}
    for scenario in ("single", "crossover"):
        scores = [float(cell["score"]) for cell in cells if cell["scenario"] == scenario]
        if scores:
            scenario_scores[scenario] = statistics.median(scores)

    if "crossover" in scenario_scores and "single" in scenario_scores:
        overall = (
            CROSSOVER_WEIGHT * scenario_scores["crossover"]
            + SINGLE_WEIGHT * scenario_scores["single"]
        )
    elif "crossover" in scenario_scores:
        overall = scenario_scores["crossover"]
    elif "single" in scenario_scores:
        overall = scenario_scores["single"]
    else:
        overall = float("inf")

    return {
        "strategy": name,
        "score": overall,
        "score_improvement_percent": (1.0 - overall) * 100.0 if overall != float("inf") else -999.0,
        "scenario_scores": scenario_scores,
        "cells": cells,
        "has_crossover": "crossover" in scenario_scores,
        "has_single": "single" in scenario_scores,
        "hz_mismatch": hz_mismatch,
        "max_start_gap_regression_percent": max_start_gap_regression,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rank Listing Studio renderer benchmarks without mixing cadence experiments"
    )
    parser.add_argument("results", nargs="+", type=Path)
    parser.add_argument("--parity", type=Path)
    parser.add_argument("--reject", default="")
    parser.add_argument(
        "--min-replacement-improvement",
        type=float,
        default=MIN_REPLACEMENT_SCORE_IMPROVEMENT_PERCENT,
    )
    parser.add_argument(
        "--max-start-gap-regression",
        type=float,
        default=MAX_START_GAP_REGRESSION_PERCENT,
    )
    parser.add_argument("--write", type=Path)
    args = parser.parse_args()

    summaries = _summaries(args.results)
    parity = _load_parity(args.parity)
    rejected = {part.strip() for part in args.reject.split(",") if part.strip()}
    strategy_names = sorted({key[0] for key in summaries})
    reports = [_strategy_report(name, summaries) for name in strategy_names]

    for report in reports:
        name = str(report["strategy"])
        parity_value = parity.get(name)
        renderer_candidate = name not in CADENCE_ONLY and name not in REFERENCE_ONLY
        eligible = (
            renderer_candidate
            and name not in rejected
            and bool(report["has_crossover"])
            and not bool(report["hz_mismatch"])
            and float(report["max_start_gap_regression_percent"]) <= args.max_start_gap_regression
        )
        if args.parity is not None:
            eligible = eligible and parity_value is True
        report["parity"] = parity_value
        report["renderer_candidate"] = renderer_candidate
        report["eligible"] = eligible

    renderer_reports = [r for r in reports if bool(r["renderer_candidate"])]
    renderer_reports.sort(key=lambda r: float(r["score"]))

    print("\nRENDERER RANKING · baseline-relative · crossover 80% / single 20%")
    print("strategy             score   improve  cross   single  start-gap-reg  parity      eligible")
    print("-" * 96)
    for row in renderer_reports:
        scenarios = row["scenario_scores"]
        assert isinstance(scenarios, dict)
        cross = scenarios.get("crossover")
        single = scenarios.get("single")
        parity_value = row["parity"]
        parity_text = "PASS" if parity_value is True else "FAIL" if parity_value is False else "UNVERIFIED"
        print(
            f"{str(row['strategy']):<20} {float(row['score']):6.3f} "
            f"{float(row['score_improvement_percent']):+8.1f}% "
            f"{cross if cross is not None else float('nan'):6.3f} "
            f"{single if single is not None else float('nan'):6.3f} "
            f"{float(row['max_start_gap_regression_percent']):+12.1f}% "
            f"{parity_text:<11} {str(bool(row['eligible'])):<8}"
        )

    cadence = [r for r in reports if str(r["strategy"]) in CADENCE_ONLY]
    if cadence:
        print("\nCADENCE EXPERIMENTS · reported separately, never eligible as renderer winners")
        for row in sorted(cadence, key=lambda r: str(r["strategy"])):
            print(
                f"{row['strategy']}: score={float(row['score']):.3f} "
                f"(excluded from renderer decision)"
            )

    eligible = [r for r in renderer_reports if bool(r["eligible"])]
    baseline = next((r for r in renderer_reports if str(r["strategy"]) == BASELINE), None)
    best = eligible[0] if eligible else None
    decision = "no-eligible-candidate"
    winner = None

    if baseline is not None and bool(baseline["eligible"]):
        winner = baseline
        decision = "keep-baseline"
        challenger = next((r for r in eligible if str(r["strategy"]) != BASELINE), None)
        if challenger is not None:
            improvement = float(challenger["score_improvement_percent"])
            if improvement >= args.min_replacement_improvement:
                winner = challenger
                decision = "candidate"
    elif best is not None:
        winner = best
        decision = "candidate"

    print()
    if winner is None:
        print("NO ELIGIBLE RENDERER CANDIDATE")
    elif decision == "keep-baseline":
        print(
            f"KEEP BASELINE: no renderer challenger cleared {args.min_replacement_improvement:.1f}% "
            "with matching cadence and crossover evidence."
        )
    elif args.parity is None:
        print(
            f"FOCUSED CANDIDATE: {winner['strategy']} · "
            f"improvement={float(winner['score_improvement_percent']):+.1f}%"
        )
        print("Run side-by-side parity before any production change.")
    else:
        print(
            f"FINAL BENCHMARK CANDIDATE: {winner['strategy']} · "
            f"improvement={float(winner['score_improvement_percent']):+.1f}%"
        )
        print("Still require one real Listing Studio A/B before production replacement.")

    if args.write:
        args.write.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "decision": decision,
            "winner": str(winner["strategy"]) if winner else None,
            "min_replacement_improvement_percent": args.min_replacement_improvement,
            "max_start_gap_regression_percent": args.max_start_gap_regression,
            "crossover_weight": CROSSOVER_WEIGHT,
            "single_weight": SINGLE_WEIGHT,
            "cadence_only": sorted(CADENCE_ONLY),
            "parity_file": str(args.parity) if args.parity else None,
            "ranking": renderer_reports,
            "cadence_experiments": cadence,
        }
        args.write.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Recommendation JSON: {args.write}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
