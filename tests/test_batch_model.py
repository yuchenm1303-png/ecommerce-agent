from __future__ import annotations

from pathlib import Path

import pytest

from gui.batch_model import (
    create_batch_run,
    load_batch_run,
    normalize_batch_urls,
    save_batch_run,
)


def test_batch_urls_are_validated_and_deduplicated() -> None:
    urls = normalize_batch_urls(
        "https://detail.1688.com/offer/1.html\n"
        "\n"
        "https://detail.1688.com/offer/2.html\n"
        "https://detail.1688.com/offer/1.html\n"
    )
    assert urls == [
        "https://detail.1688.com/offer/1.html",
        "https://detail.1688.com/offer/2.html",
    ]

    with pytest.raises(ValueError):
        normalize_batch_urls("not-a-url")


def test_batch_run_round_trips_and_summarizes_jobs(tmp_path: Path) -> None:
    batch = create_batch_run(
        tmp_path,
        [
            "https://detail.1688.com/offer/1.html",
            "https://detail.1688.com/offer/2.html",
            "https://detail.1688.com/offer/3.html",
        ],
        prepare_concurrency=2,
        execute_concurrency=2,
    )
    assert [job.job_id for job in batch.jobs] == ["JOB-001", "JOB-002", "JOB-003"]
    assert all(Path(job.run_dir).parent.is_dir() for job in batch.jobs)

    batch.jobs[0].status = "READY"
    batch.jobs[0].ready = 22
    batch.jobs[1].status = "DONE"
    batch.jobs[2].status = "REVIEW"
    save_batch_run(batch)

    loaded = load_batch_run(batch.root_dir)
    assert loaded.jobs[0].ready == 22
    assert loaded.jobs[0].status == "READY"
    assert loaded.summary() == {
        "total": 3,
        "processing": 0,
        "ready": 1,
        "done": 1,
        "review": 1,
        "failed": 0,
    }
    assert loaded.send_to_qc is False
