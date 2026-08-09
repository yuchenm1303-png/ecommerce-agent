from __future__ import annotations

import json
import math
import statistics
import time
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from typing import Any


_MAX_SERIES = 4096
_MAX_SAMPLES = 1600


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * max(0.0, min(1.0, percentile))
    lower = int(math.floor(position))
    upper = min(len(ordered) - 1, lower + 1)
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


class VisualPerfRecorder:
    """Low-overhead in-memory profiler for the GUI presentation hot path.

    No disk IO happens while a capture is active. Hot-path instrumentation only
    increments counters or appends floats to bounded deques. JSON files are
    written once when the capture stops, so the profiler itself should not
    introduce the stalls it is trying to measure.
    """

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.output_root = self.project_root / "logs" / "gui-visual-perf"
        self.active = False
        self.started_at = 0.0
        self.started_cpu = 0.0
        self.started_wall = ""
        self.session_dir: Path | None = None
        self.counters: dict[str, int] = defaultdict(int)
        self.series: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=_MAX_SERIES))
        self.gauges: dict[str, Any] = {}
        self.samples: deque[dict[str, Any]] = deque(maxlen=_MAX_SAMPLES)
        self.latest_summary: dict[str, Any] | None = None
        self.latest_summary_path: Path | None = None
        self.latest_samples_path: Path | None = None

    def start(self, metadata: dict[str, Any] | None = None) -> Path:
        if self.active:
            return self.session_dir or self.output_root
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.session_dir = self.output_root / f"capture-{stamp}"
        self.counters = defaultdict(int)
        self.series = defaultdict(lambda: deque(maxlen=_MAX_SERIES))
        self.gauges = dict(metadata or {})
        self.samples = deque(maxlen=_MAX_SAMPLES)
        self.latest_summary = None
        self.latest_summary_path = None
        self.latest_samples_path = None
        self.started_at = time.perf_counter()
        self.started_cpu = time.process_time()
        self.started_wall = datetime.now().isoformat(timespec="seconds")
        self.active = True
        self.sample("capture.start", {"wall_time": self.started_wall})
        return self.session_dir

    def stop(self) -> dict[str, Any]:
        if not self.active:
            return self.latest_summary or {}
        elapsed = max(1e-9, time.perf_counter() - self.started_at)
        cpu_elapsed = max(0.0, time.process_time() - self.started_cpu)
        self.active = False

        summary = {
            "started_at": self.started_wall,
            "elapsed_s": round(elapsed, 4),
            "process_cpu_s": round(cpu_elapsed, 4),
            "process_cpu_vs_wall_pct": round(cpu_elapsed / elapsed * 100.0, 2),
            "counters": dict(sorted(self.counters.items())),
            "gauges": dict(sorted(self.gauges.items())),
            "series": {
                key: self._series_summary(list(values))
                for key, values in sorted(self.series.items())
                if values
            },
            "sample_count": len(self.samples),
        }
        self.latest_summary = summary

        if self.session_dir is not None:
            self.session_dir.mkdir(parents=True, exist_ok=True)
            self.latest_summary_path = self.session_dir / "summary.json"
            self.latest_samples_path = self.session_dir / "samples.jsonl"
            self.latest_summary_path.write_text(
                json.dumps(summary, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            with self.latest_samples_path.open("w", encoding="utf-8") as handle:
                for sample in self.samples:
                    handle.write(json.dumps(sample, ensure_ascii=False) + "\n")
        return summary

    def counter(self, name: str, amount: int = 1) -> None:
        if self.active:
            self.counters[name] += int(amount)

    def value(self, name: str, value: float) -> None:
        if self.active and math.isfinite(float(value)):
            self.series[name].append(float(value))

    def timing_ms(self, name: str, started_perf: float) -> None:
        if self.active:
            self.value(name, (time.perf_counter() - started_perf) * 1000.0)

    def gauge(self, name: str, value: Any) -> None:
        if self.active:
            self.gauges[name] = value

    def sample(self, kind: str, payload: dict[str, Any] | None = None) -> None:
        if not self.active:
            return
        self.samples.append(
            {
                "t_ms": round((time.perf_counter() - self.started_at) * 1000.0, 3),
                "kind": kind,
                **(payload or {}),
            }
        )

    def snapshot(self) -> dict[str, Any]:
        elapsed = max(0.0, time.perf_counter() - self.started_at) if self.active else 0.0
        return {
            "active": self.active,
            "elapsed_s": elapsed,
            "session_dir": str(self.session_dir) if self.session_dir else "",
            "counters": dict(self.counters),
            "gauges": dict(self.gauges),
            "series": {
                key: self._series_summary(list(values))
                for key, values in self.series.items()
                if values
            },
            "latest_summary_path": str(self.latest_summary_path) if self.latest_summary_path else "",
            "latest_samples_path": str(self.latest_samples_path) if self.latest_samples_path else "",
        }

    @staticmethod
    def _series_summary(values: list[float]) -> dict[str, float | int]:
        if not values:
            return {"count": 0}
        return {
            "count": len(values),
            "mean": round(statistics.fmean(values), 4),
            "p50": round(_percentile(values, 0.50), 4),
            "p95": round(_percentile(values, 0.95), 4),
            "p99": round(_percentile(values, 0.99), 4),
            "max": round(max(values), 4),
        }
