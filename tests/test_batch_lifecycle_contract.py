from pathlib import Path


def _source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_batch_lifecycle_module_is_syntax_valid_and_fail_closed() -> None:
    source = _source("gui/batch_lifecycle.py")
    compile(source, "gui/batch_lifecycle.py", "exec")

    assert '_REMOVABLE_STATUSES = {"DONE", "REVIEW", "FAILED", "STOPPED"}' in source
    assert 'if self.controller.is_running:' in source
    assert 'set_read_only(False)' in source
    assert 'QPushButton("移除任务", card)' in source
    assert 'QPushButton("清理已结束")' in source
    assert 'QPushButton("新批次")' in source


def test_new_batch_keeps_artifacts_and_resets_only_runtime_ownership() -> None:
    source = _source("gui/batch_lifecycle.py")

    assert "self.controller.batch = None" in source
    assert "self.controller.config = None" in source
    assert "self.controller._source_queue.clear()" in source
    assert "self.controller._prepare_queue.clear()" in source
    assert "self.controller._execute_queue.clear()" in source
    assert "顶部链接和 SKU 规格不会清空" in source
    assert "Makro 页面都会保留" in source
    assert "rmtree" not in source
    assert "unlink(" not in source


def test_terminal_job_removal_updates_current_batch_without_touching_browser() -> None:
    source = _source("gui/batch_lifecycle.py")

    assert "batch.jobs[:] =" in source
    assert "self.controller._persist_emit()" in source
    assert "terminate(" not in source
    assert "kill(" not in source
    assert "close(" not in source


def test_listing_offer_hardening_installs_batch_lifecycle_last() -> None:
    source = _source("gui/listing_offer_hardening.py")
    compile(source, "gui/listing_offer_hardening.py", "exec")

    assert "from .batch_lifecycle import install_batch_lifecycle" in source
    assert "install_batch_lifecycle(window.batch_workspace)" in source
