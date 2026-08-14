from __future__ import annotations

import threading
import time

from app.task_control import (
    PAUSED,
    PAUSE_REQUESTED,
    RUNNING,
    command_path,
    control_path,
    initialize_task_control,
    request_pause,
    request_resume,
    safe_pause_point,
    task_control_state,
)


def test_pause_command_and_worker_state_are_separate(tmp_path) -> None:
    initialize_task_control(tmp_path, task_id="job-1", reset=True)
    request_pause(tmp_path, resume_kind="prepare")

    assert control_path(tmp_path) != command_path(tmp_path)
    assert task_control_state(tmp_path)["state"] == PAUSE_REQUESTED
    assert task_control_state(tmp_path)["resume_kind"] == "prepare"


def test_safe_pause_blocks_until_resume_and_records_checkpoint(tmp_path) -> None:
    initialize_task_control(tmp_path, task_id="job-2", reset=True)
    request_pause(tmp_path, resume_kind="execute")
    result: list[bool] = []

    thread = threading.Thread(
        target=lambda: result.append(
            safe_pause_point(
                tmp_path,
                "section:Product Description",
                context={"page_url": "https://seller.makro.co.za/example"},
                poll_seconds=0.01,
            )
        ),
        daemon=True,
    )
    thread.start()

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if task_control_state(tmp_path).get("state") == PAUSED:
            break
        time.sleep(0.01)

    paused = task_control_state(tmp_path)
    assert paused["state"] == PAUSED
    assert paused["checkpoint"] == "section:Product Description"
    assert paused["checkpoint_context"]["page_url"].startswith("https://seller.makro.co.za/")

    request_resume(tmp_path)
    thread.join(timeout=2.0)

    assert not thread.is_alive()
    assert result == [True]
    assert task_control_state(tmp_path)["state"] == RUNNING


def test_safe_checkpoint_without_pause_never_blocks(tmp_path) -> None:
    initialize_task_control(tmp_path, reset=True)
    started = time.monotonic()
    resumed = safe_pause_point(tmp_path, "step1", poll_seconds=0.01)

    assert resumed is False
    assert time.monotonic() - started < 0.5
    assert task_control_state(tmp_path)["checkpoint"] == "step1"
