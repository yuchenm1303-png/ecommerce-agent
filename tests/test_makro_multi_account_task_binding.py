from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BATCH_MODEL_PATH = ROOT / "gui" / "batch_model.py"
BATCH_LANES_PATH = ROOT / "gui" / "batch_account_lanes.py"
BATCH_BROWSER_PATH = ROOT / "gui" / "batch_browser_session.py"
BATCH_RUNTIME_PATH = ROOT / "gui" / "batch_parallel_runtime.py"
BATCH_SLOT_STORE_PATH = ROOT / "gui" / "batch_account_slot_store.py"
BATCH_WORKSPACE_PATH = ROOT / "gui" / "batch_workspace.py"
CHANNEL_BROWSER_PATH = ROOT / "gui" / "channel_account_browser.py"
CHANNEL_SURFACE_PATH = ROOT / "gui" / "channel_account_surface.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


def _load_batch_model():
    return _load_module("makro_multi_account_batch_model", BATCH_MODEL_PATH)


def _load_batch_slot_store():
    return _load_module("makro_multi_account_batch_slot_store", BATCH_SLOT_STORE_PATH)


def test_batch_job_round_trip_preserves_makro_account_and_browser_identity() -> None:
    model = _load_batch_model()
    job = model.BatchJob(
        job_id="JOB-001",
        product_url="https://example.test/product",
        makro_account_id="account-b",
        makro_account_label="Makro 店铺 B",
        makro_browser_instance_token="ws://edge-b/devtools/browser/generation-1",
    )
    batch = model.BatchRun(
        batch_id="batch-test",
        root_dir="/tmp/batch-test",
        jobs=[job],
        makro_account_id="account-b",
        makro_account_label="Makro 店铺 B",
        makro_browser_instance_token="ws://edge-b/devtools/browser/generation-1",
    )

    restored = model.BatchRun.from_dict(batch.as_dict())

    assert restored.makro_account_id == "account-b"
    assert restored.makro_account_label == "Makro 店铺 B"
    assert restored.makro_browser_instance_token.endswith("generation-1")
    assert restored.jobs[0].makro_account_id == "account-b"
    assert restored.jobs[0].makro_account_label == "Makro 店铺 B"
    assert restored.jobs[0].makro_browser_instance_token.endswith("generation-1")


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
    assert restored.makro_browser_instance_token == ""
    assert restored.jobs[0].makro_account_id == ""
    assert restored.jobs[0].makro_account_label == ""
    assert restored.jobs[0].makro_browser_instance_token == ""


def test_batch_slot_store_persists_runtime_relative_latest_batch(tmp_path: Path) -> None:
    module = _load_batch_slot_store()
    batch_root = tmp_path / "logs" / "batch-runs" / "batch-a"
    batch_root.mkdir(parents=True)
    (batch_root / "batch.json").write_text("{}", encoding="utf-8")

    store = module.BatchAccountSlotStore(tmp_path, scope_token="scope-a")
    store.remember("account-a", batch_root)

    restored = module.BatchAccountSlotStore(tmp_path, scope_token="scope-a")
    assert restored.batch_path("account-a") == (batch_root / "batch.json").resolve()

    payload = json.loads(restored.state_path.read_text(encoding="utf-8"))
    stored_path = payload["slots"]["account-a"]
    assert not Path(stored_path).is_absolute()
    assert stored_path.startswith("logs/batch-runs/")


def test_batch_slot_store_rejects_paths_outside_managed_batch_root(tmp_path: Path) -> None:
    module = _load_batch_slot_store()
    store = module.BatchAccountSlotStore(tmp_path, scope_token="scope-a")
    outside = tmp_path / "other" / "batch-a"
    outside.mkdir(parents=True)

    try:
        store.remember("account-a", outside)
    except ValueError as exc:
        assert "logs/batch-runs" in str(exc)
    else:
        raise AssertionError("outside Batch path must be rejected")


def test_shared_batch_browser_accepts_explicit_account_profile_and_generation() -> None:
    source = BATCH_BROWSER_PATH.read_text(encoding="utf-8")

    assert "profile_dir: str | Path | None = None" in source
    assert "Path(profile_dir).resolve()" in source
    assert 'root / "browser_profiles" / "makro-edge"' in source
    assert "profile_dir=resolved_profile" in source
    assert "batch.makro_browser_instance_token" in source
    assert "job.makro_browser_instance_token" in source


def test_batch_controller_has_account_lane_process_context() -> None:
    source = (ROOT / "gui" / "batch_runner.py").read_text(encoding="utf-8")

    assert "BatchAccountLaneRegistry" in source
    assert "def activate_account_lane" in source
    assert "def account_lane_snapshot" in source
    assert "def _dispatch_finished" in source
    assert "lane_state_changed = Signal" in source
    assert "_source_process_active_any_lane" in source


def test_batch_runtime_binds_before_first_job_and_rejects_cross_account_reuse() -> None:
    source = BATCH_RUNTIME_PATH.read_text(encoding="utf-8")

    assert "self._starting_accounts: dict[str, Any]" in source
    assert "_starting_accountss" not in source
    assert "self._stamp_batch_account(batch, account)" in source
    assert "这个 Batch 属于" in source
    assert "程序不会跨店铺复用已准备任务" in source
    assert "profile_dir=requested_profile" in source


def test_batch_runtime_keeps_independent_account_slots() -> None:
    runtime_source = BATCH_RUNTIME_PATH.read_text(encoding="utf-8")
    browser_source = CHANNEL_BROWSER_PATH.read_text(encoding="utf-8")

    assert "self._account_slots" in runtime_source
    assert "def activate_account_slot" in runtime_source
    assert "self._remember_current_slot()" in runtime_source
    assert "activate_account_lane" in runtime_source
    assert "install_account_lane" in runtime_source
    assert "account_lane_snapshot" in runtime_source
    assert "activate_account_slot" in browser_source


def test_multiple_accounts_can_keep_independent_batch_lanes_running() -> None:
    runtime_source = BATCH_RUNTIME_PATH.read_text(encoding="utf-8")
    browser_source = CHANNEL_BROWSER_PATH.read_text(encoding="utf-8")
    runner_source = (ROOT / "gui" / "batch_runner.py").read_text(encoding="utf-8")

    assert "self._owners: dict[str, BatchSharedBrowserOwner]" in runtime_source
    assert "_parallelism_by_account" in runtime_source
    assert "def _current_lane_id" in runtime_source
    assert "def account_lane_running" in runner_source
    assert "def any_account_lane_running" in runner_source
    assert "def channel_account_change_blocked" in browser_source
    assert "独立 Batch 不受此限制" in browser_source


def test_shared_supplier_source_remains_serialized_across_accounts() -> None:
    source = (ROOT / "gui" / "batch_runner.py").read_text(encoding="utf-8")

    assert "def _source_process_active_any_lane" in source
    assert "def _source_interaction_waiting_any_lane" in source
    assert "def _pump_waiting_source_lanes" in source
    assert "self._source_process_active_any_lane()" in source


def test_account_center_exposes_per_account_batch_slot_overview() -> None:
    runtime_source = BATCH_RUNTIME_PATH.read_text(encoding="utf-8")
    surface_source = CHANNEL_SURFACE_PATH.read_text(encoding="utf-8")

    assert "def account_slot_snapshot" in runtime_source
    assert "\"has_batch\": False" in runtime_source
    assert "账号任务概览" in surface_source
    assert "snapshot_getter(account.account_id)" in surface_source
    assert "暂无 Batch" in surface_source


def test_batch_cards_show_makro_account_ownership() -> None:
    source = BATCH_WORKSPACE_PATH.read_text(encoding="utf-8")

    assert "job.makro_account_label" in source
    assert "Makro account ID:" in source
    assert "Makro CDP lane:" in source
    assert "Makro profile:" in source


def test_batch_runtime_restores_latest_account_slots_across_restart() -> None:
    source = BATCH_RUNTIME_PATH.read_text(encoding="utf-8")

    assert "BatchAccountSlotStore" in source
    assert "def _restore_persisted_slots" in source
    assert "load_batch_run(path)" in source
    assert "self._slot_store.remember" in source
    assert "应用重启，原运行任务已停止" in source
    assert "def _ensure_controller_config" in source


def test_restored_batch_rejects_stale_browser_generation() -> None:
    source = BATCH_RUNTIME_PATH.read_text(encoding="utf-8")

    assert "makro_browser_instance_token" in source
    assert "stored_token != current_token" in source
    assert "batch_token != current_token" in source
    assert "旧 targetId 已失效" in source
    assert "旧 owned targetId 全部失效" in source


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


def test_usage_telemetry_is_scoped_per_account_lane() -> None:
    source = (ROOT / "gui" / "usage_telemetry.py").read_text(encoding="utf-8")

    assert "_batch_lane_states" in source
    assert "def _batch_lane_key" in source
    assert "lane_running_changed" in source
    assert "def _on_batch_lane_running" in source
    assert "def _on_batch_lane_state_changed" in source
    assert '"makro_account_id"' in source
    assert '"makro_cdp_port"' in source


def test_multi_account_task_sources_compile() -> None:
    for path in (
        BATCH_MODEL_PATH,
        BATCH_LANES_PATH,
        BATCH_BROWSER_PATH,
        BATCH_RUNTIME_PATH,
        BATCH_SLOT_STORE_PATH,
        BATCH_WORKSPACE_PATH,
        CHANNEL_BROWSER_PATH,
        CHANNEL_SURFACE_PATH,
    ):
        source = path.read_text(encoding="utf-8")
        compile(source, str(path), "exec")
