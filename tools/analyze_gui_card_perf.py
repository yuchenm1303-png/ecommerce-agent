from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path


REFERENCE_ONLY = {"live_effect", "no_scale_control"}


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


def _aggregate(paths: list[Path]) -> dict[str, dict[str, float]]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        for row in payload.get("runs", []):
            groups[str(row["strategy"])].append(row)

    result: dict[str, dict[str, float]] = {}
    for strategy, rows in groups.items():
        budget = _median(rows, "frame_budget_ms") or 16.6667
        result[strategy] = {
            "runs": float(len(rows)),
            "budget": budget,
            "p50": _median(rows, "frame_median_ms"),
            "p95": _median(rows, "frame_p95_ms"),
            "p99": _median(rows, "frame_p99_ms"),
            "max": _median(rows, "frame_max_ms"),
            "long_rate": _median(rows, "long_1_5x_rate"),
            "long_2x_rate": _median(rows, "long_2x_rate"),
            "cpu": _median(rows, "cpu_core_percent"),
            "prepare_p95": _median(rows, "transition_prepare_p95_ms"),
            "start_gap_p95": _median(rows, "transition_start_gap_p95_ms"),
        }
    return result


def _score(row: dict[str, float]) -> float:
    budget = max(1e-6, row["budget"])
    return (
        0.36 * row["p95"] / budget
        + 0.24 * row["p99"] / budget
        + 0.16 * row["start_gap_p95"] / budget
        + 0.10 * row["prepare_p95"] / budget
        + 0.08 * row["cpu"] / 100.0
        + 2.0 * row["long_rate"]
        + 1.0 * row["long_2x_rate"]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Rank GUI card benchmark results")
    parser.add_argument("results", nargs="+", type=Path)
    parser.add_argument("--parity", type=Path, help="JSON mapping strategy -> true/false after manual visual/input checks")
    parser.add_argument("--reject", default="", help="comma-separated strategies to reject regardless of metrics")
    parser.add_argument("--write", type=Path, help="optional recommendation JSON output")
    args = parser.parse_args()

    aggregate = _aggregate(args.results)
    parity = _load_parity(args.parity)
    rejected = {part.strip() for part in args.reject.split(",") if part.strip()}
    baseline = aggregate.get("baseline_frozen")
    baseline_p95 = baseline["p95"] if baseline else 0.0

    ranked = []
    for name, row in aggregate.items():
        score = _score(row)
        parity_value = parity.get(name)
        eligible = name not in REFERENCE_ONLY and name not in rejected
        if args.parity is not None:
            eligible = eligible and parity_value is True
        improvement = 0.0
        if baseline_p95 > 0.0:
            improvement = (baseline_p95 - row["p95"]) / baseline_p95 * 100.0
        ranked.append((score, name, row, eligible, parity_value, improvement))
    ranked.sort(key=lambda item: item[0])

    print("\nGUI CARD PERFORMANCE RANKING · lower score is better")
    print("strategy             score    p95    p99   long%   CPU%  start-gap  parity      eligible  vs-baseline")
    print("-" * 108)
    for score, name, row, eligible, parity_value, improvement in ranked:
        parity_text = "PASS" if parity_value is True else "FAIL" if parity_value is False else "UNVERIFIED"
        print(
            f"{name:<20} {score:6.3f} {row['p95']:6.2f} {row['p99']:6.2f} "
            f"{row['long_rate'] * 100:6.1f} {row['cpu']:6.1f} {row['start_gap_p95']:10.2f} "
            f"{parity_text:<11} {str(eligible):<8} {improvement:+8.1f}%"
        )

    eligible_rows = [item for item in ranked if item[3]]
    winner = eligible_rows[0] if eligible_rows else None
    final = args.parity is not None and winner is not None
    print()
    if winner is None:
        print("No eligible candidate. Check parity/reject settings.")
    elif final:
        print(f"FINAL CANDIDATE: {winner[1]} · score={winner[0]:.3f}")
        print("Still require one full real-GUI A/B run before replacing production rendering.")
    else:
        print(f"PROVISIONAL LEADER: {winner[1]} · score={winner[0]:.3f}")
        print("Not final: run --demo parity checks and pass --parity before choosing an architecture.")

    if args.write:
        args.write.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "final": final,
            "winner": winner[1] if winner else None,
            "score": winner[0] if winner else None,
            "parity_file": str(args.parity) if args.parity else None,
            "ranking": [
                {
                    "strategy": name,
                    "score": score,
                    "eligible": eligible,
                    "parity": parity_value,
                    "vs_baseline_p95_percent": improvement,
                    **row,
                }
                for score, name, row, eligible, parity_value, improvement in ranked
            ],
        }
        args.write.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Recommendation JSON: {args.write}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
