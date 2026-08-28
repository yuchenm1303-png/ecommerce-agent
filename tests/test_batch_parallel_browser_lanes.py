from __future__ import annotations

from pathlib import Path

import gui.batch_browser_pool as browser_pool
from gui.batch_browser_pool import (
    bind_batch_browser_lanes,
    build_batch_browser_lanes,
    pop_next_lane_ready_job,
)
from gui.batch_model import BatchJob, BatchRun


ROOT = Path(__file__).resolve().parents[1]


def _batch(tmp_path: Path, count: int, concurrency: int) -> BatchRun:
    jobs = [
        BatchJob(
            job_id=f"JOB-{index + 1:03d}",
            product_url=f"https://supplier.test/{index}",
            run_dir=str(tmp_path / f"job-{index}" / "workflow"),
        )
        for index in range(count)
    ]
    return BatchRun(
        batch_id="batch-test",
        root_dir=str(tmp_path),
        jobs=jobs,
        prepare_concurrency=concurrency,
        execute_concurrency=concurrency,
    )


def test_browser_lanes_use_distinct_ports_and_profiles(tmp_path: Path) -> None:
    lanes = build_batch_browser_lanes(tmp_path, base_port=9222, count=4)

    assert [lane.cdp_port for lane in lanes] == [9222, 9223, 9224, 9225]
    assert len({lane.profile_dir for lane in lanes}) == 4
    assert lanes[0].profile_dir == tmp_path.resolve() / "browser_profiles" / "makro-edge"
    assert lanes[1].profile_dir.name == "makro-edge-worker-02"


def test_jobs_keep_same_persisted_lane_across_prepare_and_execute(tmp_path: Path) -> None:
    batch = _batch(tmp_path, count=8, concurrency=3)

    lanes = bind_batch_browser_lanes(batch, project_root=tmp_path, base_port=9222)

    assert len(lanes) == 3
    assert [job.browser_lane for job in batch.jobs] == [0, 1, 2, 0, 1, 2, 0, 1]
    assert [job.makro_cdp_port for job in batch.jobs] == [9222, 9223, 9224, 9222, 9223, 9224, 9222, 9223]
    assert all(job.makro_profile_dir for job in batch.jobs)

    restored = BatchRun.from_dict(batch.as_dict())
    assert [job.browser_lane for job in restored.jobs] == [job.browser_lane for job in batch.jobs]
    assert [job.makro_cdp_port for job in restored.jobs] == [job.makro_cdp_port for job in batch.jobs]
    assert [job.makro_profile_dir for job in restored.jobs] == [job.makro_profile_dir for job in batch.jobs]


def test_lane_scheduler_skips_busy_lane_instead_of_creating_cdp_wait(tmp_path: Path) -> None:
    batch = _batch(tmp_path, count=3, concurrency=2)
    bind_batch_browser_lanes(batch, project_root=tmp_path, base_port=9222)
    queue = ["JOB-003", "JOB-002"]  # JOB-003 shares lane 0 with active JOB-001.
    processes = {object(): ("JOB-001", "prepare")}

    selected = pop_next_lane_ready_job(
        queue,
        jobs=batch.jobs,
        processes=processes,
        stage="prepare",
        concurrency=2,
    )

    assert selected == "JOB-002"
    assert queue == ["JOB-003"]


def test_lane_scheduler_is_exclusive_across_prepare_and_execute(tmp_path: Path) -> None:
    batch = _batch(tmp_path, count=3, concurrency=2)
    bind_batch_browser_lanes(batch, project_root=tmp_path, base_port=9222)
    queue = ["JOB-003", "JOB-002"]  # JOB-003 shares lane 0 with active prepare JOB-001.
    processes = {object(): ("JOB-001", "prepare")}

    selected = pop_next_lane_ready_job(
        queue,
        jobs=batch.jobs,
        processes=processes,
        stage="execute",
        concurrency=2,
    )

    assert selected == "JOB-002"
    assert queue == ["JOB-003"]


def test_source_capture_does_not_consume_a_makro_browser_lane(tmp_path: Path) -> None:
    batch = _batch(tmp_path, count=3, concurrency=2)
    bind_batch_browser_lanes(batch, project_root=tmp_path, base_port=9222)
    queue = ["JOB-003"]
    processes = {object(): ("JOB-001", "source")}

    selected = pop_next_lane_ready_job(
        queue,
        jobs=batch.jobs,
        processes=processes,
        stage="prepare",
        concurrency=2,
    )

    assert selected == "JOB-003"
    assert queue == []


def test_pool_growth_seeds_only_new_workers_without_navigating_live_lanes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    ready = {9222, 9223}
    launched: list[int] = []
    seeded: list[int] = []
    exported: list[int] = []

    def fake_ready(port: int, timeout_s: float = 0.0) -> bool:
        del timeout_s
        return int(port) in ready

    def fake_launch(*, profile_dir: Path, port: int, start_url: str) -> None:
        del profile_dir, start_url
        launched.append(int(port))
        ready.add(int(port))

    def fake_export(port: int) -> dict[str, object]:
        exported.append(int(port))
        return {"cookies": [], "local_storage": []}

    def fake_import(lane, state) -> None:
        del state
        seeded.append(int(lane.cdp_port))

    monkeypatch.setattr(browser_pool, "is_cdp_ready", fake_ready)
    monkeypatch.setattr(browser_pool, "launch_detached_edge", fake_launch)
    monkeypatch.setattr(browser_pool, "_export_primary_login_state", fake_export)
    monkeypatch.setattr(browser_pool, "_import_primary_login_state", fake_import)
    monkeypatch.setattr(
        browser_pool,
        "browser_lane_token",
        lambda port: f"token-{int(port)}" if int(port) in ready else "",
    )

    lanes = browser_pool.ensure_batch_browser_lanes(
        tmp_path,
        base_port=9222,
        count=3,
    )

    assert [lane.cdp_port for lane in lanes] == [9222, 9223, 9224]
    assert launched == [9224]
    assert exported == [9222]
    assert seeded == [9224]


def test_parallel_runtime_is_installed_after_managed_browser() -> None:
    launcher = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")
    runtime = (ROOT / "gui" / "batch_parallel_runtime.py").read_text(encoding="utf-8")

    assert launcher.index("install_managed_makro_browser(window)") < launcher.index(
        "install_batch_parallel_runtime(window)"
    )
    assert 'stage in {"prepare", "execute"}' in runtime
    assert '"--cdp-port"' in runtime
    assert '"--profile-dir"' in runtime
    assert "pop_next_lane_ready_job" in runtime
    assert "_assert_prepared_lanes_alive" in runtime
    assert "self._prepared_tokens.setdefault(port, token)" in runtime
    assert "Send to QC" not in runtime
