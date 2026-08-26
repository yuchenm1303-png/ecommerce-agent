from __future__ import annotations

import inspect
from types import SimpleNamespace

from gui.batch_link_telemetry import BatchLinkTelemetryController, _job_ordinal


def _job(job_id: str):
    return SimpleNamespace(job_id=job_id)


def test_job_ordinal_is_identity_metadata_not_current_list_position() -> None:
    assert _job_ordinal("JOB-001") == 1
    assert _job_ordinal("JOB-003") == 3
    assert _job_ordinal("job-120") == 120
    assert _job_ordinal("JOB-X") == 0


def test_batch_coordinates_do_not_shrink_when_scheduler_view_loses_jobs() -> None:
    owner = SimpleNamespace(_batch_positions={}, _batch_sizes={})
    resolve = BatchLinkTelemetryController._stable_batch_coordinates

    positions, total = resolve(
        owner,
        "batch-1",
        [_job("JOB-001"), _job("JOB-002"), _job("JOB-003")],
    )
    assert positions == {"JOB-001": 1, "JOB-002": 2, "JOB-003": 3}
    assert total == 3

    positions, total = resolve(
        owner,
        "batch-1",
        [_job("JOB-001"), _job("JOB-003")],
    )
    assert positions["JOB-001"] == 1
    assert positions["JOB-003"] == 3
    assert total == 3

    positions, total = resolve(owner, "batch-1", [_job("JOB-003")])
    assert positions["JOB-003"] == 3
    assert total == 3


def test_result_cache_identity_includes_fixed_batch_coordinates() -> None:
    source = inspect.getsource(BatchLinkTelemetryController._job_result)
    assert 'f"{index}/{total}"' in source
