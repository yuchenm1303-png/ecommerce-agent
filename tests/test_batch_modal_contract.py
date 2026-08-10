from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = (ROOT / "gui" / "batch_workspace.py").read_text(encoding="utf-8")


def test_batch_job_details_reuse_the_shared_glass_modal() -> None:
    assert "_JobDetailDialog" not in WORKSPACE
    assert "QDialog" not in WORKSPACE
    assert 'getattr(self.window(), "_card_details", None)' in WORKSPACE
    assert 'open_custom = getattr(details, "open_custom", None)' in WORKSPACE
    assert "def _open_job_in_shared_modal" in WORKSPACE
    assert "ratio=_BATCH_DETAIL_RATIO" in WORKSPACE
    assert "self._open_job_in_shared_modal(job)" in WORKSPACE
    assert "cardDetailTextView" in WORKSPACE
    assert "modalPrimaryButton" in WORKSPACE


def test_batch_modal_change_is_presentation_only() -> None:
    assert "self.controller.start_prepare(" in WORKSPACE
    assert "self.controller.start_execution(" in WORKSPACE
    assert "self.controller.stop" in WORKSPACE
    assert "self.table.doubleClicked.connect(self._open_selected_job)" in WORKSPACE


def test_batch_workspace_source_compiles_without_importing_pyside() -> None:
    compile(WORKSPACE, str(ROOT / "gui" / "batch_workspace.py"), "exec")
