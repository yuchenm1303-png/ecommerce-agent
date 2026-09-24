from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BATCH_MODEL_PATH = ROOT / "gui" / "batch_model.py"
BATCH_BROWSER_PATH = ROOT / "gui" / "batch_browser_session.py"
BATCH_RUNTIME_PATH = ROOT / "gui" / "batch_parallel_runtime.py"
CHANNEL_BROWSER_PATH = ROOT / "gui" / "channel_account_browser.py"


def _load_batch_model():
    spec = importlib.util.spec_from_file_location("makro_multi_account_batch_model", BATCH_MODEL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


def test_batch_job_round_trip_preserves_makro_account_identity() -> None:
    model = _load_batch_model()
    job = model.BatchJob(
        job_id="JOB-001",
        product_url="https://example.test/product",
        makro_account_id="account-b",
        makro_account_label="Makro 店铺 B",
    )
    batch = model.BatchRun(
        batch_id="batch-test",
        root_dir="/tmp/batch-test",
        jobs=[job],
        makro_account_id="account-b",
        makro_account_label="Makro 店铺 B",
    )

    restored = model.BatchRun.from_dict(batch.as_dict())

    assert restored.makro_account_id == "account-b"
    assert restored.makro_account_label == "Makro 店铺 B"
    assert restored.jobs[0].makro_account_id == "account-b"
    assert restored.jobs[0].makro_account_label == "Makro 店铺 B"


def test_legacy_batch_without_account_fields_remains_loadable() -> None:
    model = _load_batch_model()
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

    restored = model.BatchRun.from_dict(payload)

    assert restored.makro_account_id == ""
    assert restored.makro_account_label == ""
    assert restored.jobs[0].makro_account_id == ""
    assert restored.jobs[0].makro_account_label == ""


def test_shared_batch_browser_accepts_explicit_account_profile() -> None:
    source = BATCH_BROWSER_PATH.read_text(encoding="utf-8")

    assert "profile_dir: str | Path | None = None" in source
    assert "Path(profile_dir).resolve()" in source
    assert 'root / "browser_profiles" / "makro-edge"' in source
    assert "profile_dir=resolved_profile" in source


def test_batch_runtime_binds_before_first_job_and_rejects_cross_account_reuse() -> None:
    source = BATCH_RUNTIME_PATH.read_text(encoding="utf-8")

    assert "self._starting_account" in source
    assert "self._stamp_batch_account(batch, account)" in source
    assert "这个 Batch 属于" in source
    assert "程序不会跨店铺复用已准备任务" in source
    assert "profile_dir=requested_profile" in source


def test_batch_start_requires_committed_account_browser_identity() -> None:
    runtime_source = BATCH_RUNTIME_PATH.read_text(encoding="utf-8")
    browser_source = CHANNEL_BROWSER_PATH.read_text(encoding="utf-8")

    assert "task_channel_account" in runtime_source
    assert "def task_channel_account" in browser_source
    assert "runtime_identity(self.channel_account)" in browser_source
    assert "self._read_runtime_identity() != expected_identity" in browser_source
    assert "运行时身份确认完成后再启动任务" in browser_source


def test_single_prepared_task_is_invalidated_by_account_switch() -> None:
    source = CHANNEL_BROWSER_PATH.read_text(encoding="utf-8")

    assert "_single_prepared_account_id" in source
    assert "_single_prepared_invalidated_by_account_switch" in source
    assert "这个 Single 任务是在另一个 Makro 店铺下准备的" in source
    assert "程序不会把旧店铺的准备结果用于当前账号" in source


def test_multi_account_task_sources_compile() -> None:
    for path in (
        BATCH_MODEL_PATH,
        BATCH_BROWSER_PATH,
        BATCH_RUNTIME_PATH,
        CHANNEL_BROWSER_PATH,
    ):
        source = path.read_text(encoding="utf-8")
        compile(source, str(path), "exec")
