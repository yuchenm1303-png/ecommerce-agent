from __future__ import annotations

from pathlib import Path

import pytest

from gui.batch_browser_session import (
    BatchSharedBrowserOwner,
    bind_batch_shared_browser,
    shared_batch_browser,
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


def test_all_jobs_share_one_edge_port_and_profile_but_keep_job_ownership(tmp_path: Path) -> None:
    batch = _batch(tmp_path, count=6, concurrency=4)
    browser = shared_batch_browser(tmp_path, cdp_port=9222)

    bind_batch_shared_browser(batch, browser)

    assert {job.browser_lane for job in batch.jobs} == {0}
    assert {job.makro_cdp_port for job in batch.jobs} == {9222}
    assert {job.makro_profile_dir for job in batch.jobs} == {
        str(tmp_path.resolve() / "browser_profiles" / "makro-edge")
    }
    assert batch.prepare_concurrency == 4


def test_shared_browser_binding_survives_batch_serialization(tmp_path: Path) -> None:
    batch = _batch(tmp_path, count=3, concurrency=3)
    browser = shared_batch_browser(tmp_path, cdp_port=9222)
    bind_batch_shared_browser(batch, browser)
    for index, job in enumerate(batch.jobs):
        job.makro_target_id = f"target-{index}"

    restored = BatchRun.from_dict(batch.as_dict())

    assert [job.makro_target_id for job in restored.jobs] == [
        "target-0",
        "target-1",
        "target-2",
    ]
    assert {job.makro_cdp_port for job in restored.jobs} == {9222}
    assert {job.browser_lane for job in restored.jobs} == {0}


def test_shared_owner_acquires_one_parent_lease_and_detects_browser_restart(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import gui.batch_browser_session as shared

    released: list[bool] = []

    class FakeLease:
        def release(self) -> None:
            released.append(True)

    token = {"value": "ws://browser/one"}
    acquire_calls: list[int] = []
    monkeypatch.setattr(shared, "is_cdp_ready", lambda _port, **_kwargs: True)
    monkeypatch.setattr(shared, "browser_instance_token", lambda _port: token["value"])
    monkeypatch.setattr(
        shared,
        "acquire_cdp_session_lease",
        lambda port: acquire_calls.append(int(port)) or FakeLease(),
    )

    owner = BatchSharedBrowserOwner(shared_batch_browser(tmp_path, cdp_port=9222))
    owner.acquire()
    owner.acquire()

    assert acquire_calls == [9222]
    assert owner.acquired is True
    assert owner.instance_token == "ws://browser/one"

    token["value"] = "ws://browser/restarted"
    with pytest.raises(RuntimeError, match="重启"):
        owner.assert_alive()

    owner.release()
    assert released == [True]


def test_cdp_session_owner_token_is_reentrant_for_inherited_child_contract(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import app.browser_session as browser_session

    monkeypatch.setattr(browser_session, "_cdp_lock_root", lambda: tmp_path)
    port = 59222
    first = browser_session.acquire_cdp_session_lease(port)
    try:
        inherited = browser_session.acquire_cdp_session_lease(port)
        try:
            assert inherited.inherited is True
            assert inherited.token == first.token
        finally:
            inherited.release()
    finally:
        first.release()


def test_runtime_uses_single_browser_owner_and_no_worker_edge_pool() -> None:
    launcher = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")
    runtime = (ROOT / "gui" / "batch_parallel_runtime.py").read_text(encoding="utf-8")
    shared = (ROOT / "gui" / "batch_browser_session.py").read_text(encoding="utf-8")

    assert launcher.index("install_managed_makro_browser(window)") < launcher.index(
        "install_batch_parallel_runtime(window)"
    )
    assert "BatchSharedBrowserOwner" in runtime
    assert "bind_batch_shared_browser" in runtime
    assert "BROWSER_TAB" in runtime
    assert '"--cdp-port"' in runtime
    assert '"--profile-dir"' in runtime
    assert "acquire_cdp_session_lease" in shared
    assert "targetId" in runtime
    assert "makro-edge-worker" not in runtime
    assert "makro-edge-worker" not in shared
    assert "pop_next_lane_ready_job" not in runtime
    assert "ensure_batch_browser_lanes" not in runtime
    assert "Send to QC" not in runtime
