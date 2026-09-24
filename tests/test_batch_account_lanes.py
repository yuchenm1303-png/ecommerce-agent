from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LANES_PATH = ROOT / "gui" / "batch_account_lanes.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("batch_account_lanes_contract", LANES_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


def test_lane_registry_keeps_batch_state_independent() -> None:
    module = _load_module()
    registry = module.BatchAccountLaneRegistry()

    registry.select("account-a")
    registry.state().source_queue.append("JOB-001")
    registry.state().extras["value"] = "A"

    registry.select("account-b")
    assert registry.state().source_queue == []
    assert registry.state().extras == {}
    registry.state().source_queue.append("JOB-002")

    with registry.use("account-a"):
        assert registry.current_key() == "account-a"
        assert registry.state().source_queue == ["JOB-001"]
        assert registry.state().extras["value"] == "A"

    assert registry.current_key() == "account-b"
    assert registry.state().source_queue == ["JOB-002"]


def test_process_map_filters_duplicate_job_ids_by_account_lane() -> None:
    module = _load_module()
    registry = module.BatchAccountLaneRegistry()
    processes = module.LaneProcessMap(registry.current_key)
    buffers = module.LaneProcessDataMap(processes, registry.current_key)

    process_a = object()
    process_b = object()

    registry.select("account-a")
    processes[process_a] = ("JOB-001", "prepare")
    buffers[process_a] = "A"

    registry.select("account-b")
    processes[process_b] = ("JOB-001", "prepare")
    buffers[process_b] = "B"

    assert list(processes.values()) == [("JOB-001", "prepare")]
    assert buffers[process_b] == "B"
    buffers.clear()

    with registry.use("account-a"):
        assert list(processes.values()) == [("JOB-001", "prepare")]
        assert buffers[process_a] == "A"

    assert processes.owner_for(process_a) == "account-a"
    assert processes.owner_for(process_b) == "account-b"


def test_lane_sources_compile_without_qt() -> None:
    source = LANES_PATH.read_text(encoding="utf-8")
    compile(source, str(LANES_PATH), "exec")
