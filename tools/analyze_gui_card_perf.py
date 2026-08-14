from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path


MIN_REPLACEMENT_SCORE_IMPROVEMENT_PERCENT = 2.0
REFERENCE_ONLY = {"frozen_fast", "no_scale_control"}


def _median(rows: list[dict], key: str) -> float:
    values = [float(row.get(key, 0.0)) for row in rows]
    return statistics.median(values) if values else 0.0


def _load_parity(path: Path | None) -> dict[str, bool]:
    if path is None:
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise SystemExit("parity file must be a JSON object mapping strategy -> true/false")
    return {str(key): bool(value) for key, value in raw.items()}


def _aggregate(paths: list[Path]) -> dict[str, dict[str, object]]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        for row in payload.get("runs", []):
            groups[str(row["strategy"])].append(row)

    result: dict[str, dict[str, object]] = {}
    for strategy, rows in groups.items():
        scenarios = sorted({str(row.get("scenario", "single")) for row in rows})
        eligible_default = all(bool(row.get("eligible_default", strategy not in REFERENCE_ONLY)) for row in rows)
        result[strategy] = {
            "runs": len(rows),
            "scenarios": scenarios,
            "eligible_default": eligible_default,
            "budget": _median(rows, "frame_budget_ms") or 16.6667,
            "p50": _median(rows, "frame_median_ms"),
            "p95": _median(rows, "frame_p95_ms"),
            "p99": _median(rows, "frame_p99_ms"),
            "max": _median(rows, "frame_max_ms"),
            "long_rate": _median(rows, "long_1_5x_rate"),
            "long_2x_rate": _median(rows, "long_2x_rate"),
            "cpu": _median(rows, "cpu_core_percent"),
            "prepare_p95": _median(rows, "transition_prepare_p95_ms"),
            "start_gap_p95": _median(rows, "transition_start_gap_p95_ms"),
            "tick_work_p95": _median(rows, "tick_work_p95_ms"),
        }
    return result


def _score(row: dict[str, object]) -> float:
    budget = max(1e-6, float(row["budget"]))
    return (
        0.32 * float(row["p95"]) / budget
        + 0.22 * float(row["p99"]) / budget
        + 0.16 * float(row["start_gap_p95"]) / budget
        + 0.10 * float(row["prepare_p95"]) / budget
        + 0.08 * float(row["tick_work_p95"]) / budget
        + 0.06 * float(row["cpu"]) / 100.0
        + 2.0 * float(row["long_rate"])
        + 1.0 * float(row["long_2x_rate"])
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Rank Listing Studio card-motion benchmark results")
    parser.add_argument("results", nargs="+", type=Path)
    parser.add_argument("--parity", type=Path, help="JSON mapping strategy -> true/false after side-by-side visual/input checks")
    parser.add_argument("--reject", default="", help="comma-separated strategies to reject regardless of metrics")
    parser.add_argument("--min-replacement-improvement", type=float, default=MIN_REPLACEMENT_SCORE_IMPROVEMENT_PERCENT)
    parser.add_argument("--write", type=Path, help="optional recommendation JSON output")
    args = parser.parse_args()

    aggregate = _aggregate(args.results)
    parity = _load_parity(args.parity)
    rejected = {part.strip() for part in args.reject.split(",") if part.strip()}
    baseline = aggregate.get("baseline_frozen")
    baseline_score = _score(baseline) if baseline else 0.0
    baseline_p95 = float(baseline["p95"]) if baseline else 0.0

    ranked = []
    for name, row in aggregate.items():
        score = _score(row)
        parity_value = parity.get(name)
        eligible = bool(row["eligible_default"]) and name not in rejected
        if args.parity is not None:
            eligible = eligible and parity_value is True
        p95_improvement = (baseline_p95 - float(row["p95"])) / baseline_p95 * 100.0 if baseline_p95 else 0.0
        score_improvement = (baseline_score - score) / baseline_score * 100.0 if baseline_score else 0.0
        ranked.append((score, name, row, eligible, parity_value, p95_improvement, score_improvement))
    ranked.sort(key=lambda item: item[0])

    print("\nGUI CARD MOTION PERFORMANCE RANKING · lower score is better")
    print("strategy             score    p95    p99   long%   CPU%  start-gap  parity      eligible  score-vs-base")
    print("-" * 111)
    for score, name, row, eligible, parity_value, _p95_imp, score_imp in ranked:
        parity_text = "PASS" if parity_value is True else "FAIL" if parity_value is False else "UNVERIFIED"
        print(
            f"{name:<20} {score:6.3f} {float(row['p95']):6.2f} {float(row['p99']):6.2f} "
            f"{float(row['long_rate']) * 100:6.1f} {float(row['cpu']):6.1f} {float(row['start_gap_p95']):10.2f} "
            f"{parity_text:<11} {str(eligible):<8} {score_imp:+10.1f}%"
        )

    eligible_rows = [item for item in ranked if item[3]]
    winner = eligible_rows[0] if eligible_rows else None
    baseline_row = next((item for item in eligible_rows if item[1] == "baseline_frozen"), None)
    kept_baseline_for_noise = False
    if winner is not None and winner[1] != "baseline_frozen" and baseline_row is not None:
        if winner[6] < max(0.0, args.min_replacement_improvement):
            winner = baseline_row
            kept_baseline_for_noise = True

    final = args.parity is not None and winner is not None
    print()
    if winner is None:
        print("No eligible candidate. Check parity/reject settings.")
    elif kept_baseline_for_noise:
        print(
            f"KEEP BASELINE: best challenger did not clear the {args.min_replacement_improvement:.1f}% "
            "replacement margin; treat the difference as benchmark noise / insufficient benefit."
        )
    elif final:
        print(f"FINAL CANDIDATE: {winner[1]} · score={winner[0]:.3f}")
        print("Still require one full real-GUI A/B run before replacing production rendering.")
    else:
        print(f"PROVISIONAL LEADER: {winner[1]} · score={winner[0]:.3f}")
        print("Not final: use --compare-demo for parity, then pass --parity.")

    if args.write:
        args.write.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "final": final,
            "winner": winner[1] if winner else None,
            "score": winner[0] if winner else None,
            "kept_baseline_for_noise": kept_baseline_for_noise,
            "min_replacement_score_improvement_percent": args.min_replacement_improvement,
            "parity_file": str(args.parity) if args.parity else None,
            "ranking": [
                {
                    "strategy": name,
                    "score": score,
                    "eligible": eligible,
                    "parity": parity_value,
                    "vs_baseline_p95_percent": p95_imp,
                    "vs_baseline_score_percent": score_imp,
                    **row,
                }
                for score, name, row, eligible, parity_value, p95_imp, score_imp in ranked
            ],
        }
        args.write.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Recommendation JSON: {args.write}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
