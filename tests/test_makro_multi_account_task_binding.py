from __future__ import annotations

from pathlib import Path

from gui.batch_browser_session import shared_batch_browser
from gui.batch_model import BatchJob, BatchRun


ROOT = Path(__file__).resolve().parents[1]
BATCH_RUNTIME_PATH = ROOT / "gui" / "batch_parallel_runtime.py"
CHANNEL_BROWSER_PATH = ROOT / "gui" / "channel_account_browser.py"


def test_batch_job_round_trip_preserves_makro_account_identity() -> None:
    job = BatchJob(
        job_id="JOB-001",
        product_url="https://example.test/product",
        makro_account_id="account-b",
        makro_account_label="Makro 店铺 B",
    )
    batch = BatchRun(
        batch_id="batch-test",
        root_dir="/tmp/batch-test",
        jobs=[job],
        makro_account_id="account-b",
        makro_account_label="Makro 店铺 B",
    )

    restored = BatchRun.from_dict(batch.as_dict())

    assert restored.makro_account_id == "account-b"
    assert restored.makro_account_label == "Makro 店铺 B"
    assert restored.jobs[0].makro_account_id == "account-b"
    assert restored.jobs[0].makro_account_label == "Makro 店铺 B"


def test_legacy_batch_without_account_fields_remains_loadable() -> None:
    payload = {
        "batch_id": "legacy-batch",
        "root_dir": "/tmp/legacy-batch",
        "jobs": [
            {
                "job_id": "JOB-001",
                "product_url": "https://example.test/product",
            }
        ],
    }

    restored = BatchRun.from_dict(payload)

    assert restored.makro_account_id == ""
    assert restored.makro_account_label == ""
    assert restored.jobs[0].makro_account_id == ""
    assert restored.jobs[0].makro_account_label == ""


def test_shared_batch_browser_uses_explicit_account_profile(tmp_path: Path) -> None:
    account_profile = tmp_path / "browser_profiles" / "channels" / "makro" / "account-b"

    browser = shared_batch_browser(
        tmp_path,
        cdp_port=9444,
        profile_dir=account_profile,
    )

    assert browser.cdp_port == 9444
    assert browser.profile_dir == account_profile.resolve()
    assert browser.profile_dir != (tmp_path / "browser_profiles" / "makro-edge").resolve()


def test_batch_runtime_binds_before_first_job_and_rejects_cross_account_reuse() -> None:
    source = BATCH_RUNTIME_PATH.read_text(encoding="utf-8")

    assert "self._starting_account" in source
    assert "self._stamp_batch_account(batch, account)" in source
    assert "这个 Batch 属于" in source
    assert "程序不会跨店铺复用已准备任务" in source
    assert "profile_dir=requested_profile" in source


def test_single_prepared_task_is_invalidated_by_account_switch() -> None:
    source = CHANNEL_BROWSER_PATH.read_text(encoding="utf-8")

    assert "_single_prepared_account_id" in source
    assert "_single_prepared_invalidated_by_account_switch" in source
    assert "这个 Single 任务是在另一个 Makro 店铺下准备的" in source
    assert "程序不会把旧店铺的准备结果用于当前账号" in source


def test_multi_account_task_sources_compile() -> None:
    for path in (
        ROOT / "gui" / "batch_model.py",
        ROOT / "gui" / "batch_browser_session.py",
        BATCH_RUNTIME_PATH,
        CHANNEL_BROWSER_PATH,
    ):
        source = path.read_text(encoding="utf-8")
        compile(source, str(path), "exec")
