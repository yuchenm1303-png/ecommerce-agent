from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMMIT = (ROOT / "gui" / "workspace_layout_commit.py").read_text(encoding="utf-8")
TOGGLE = (ROOT / "gui" / "mode_toggle.py").read_text(encoding="utf-8")
BATCH = (ROOT / "gui" / "batch_card_responsive.py").read_text(encoding="utf-8")
STARTUP = (ROOT / "gui" / "startup_entrance_stability.py").read_text(encoding="utf-8")


def test_mode_change_commits_layout_synchronously_before_transition_can_capture() -> None:
    assert "class WorkspaceLayoutCommitter(QObject)" in COMMIT
    assert "self.stack.currentChanged.connect(self.commit_current)" in COMMIT
    assert "stack_layout.invalidate()" in COMMIT
    assert "stack_layout.activate()" in COMMIT
    assert "_activate_layout_tree(page)" in COMMIT
    assert "processEvents" not in COMMIT
    assert "sleep(" not in COMMIT
    assert "install_workspace_layout_commit(window)" in TOGGLE


def test_layout_barrier_handles_single_reflow_and_batch_viewport_ownership() -> None:
    assert "refresh_single_source_layout(self.window)" in COMMIT
    assert 'getattr(responsive, "commit_now", None)' in COMMIT
    assert "def commit_now(self)" in BATCH
    assert "self._sync_width()" in BATCH
    assert "def _refresh(self)" in BATCH
    assert "self.commit_now()" in BATCH


def test_startup_never_samples_or_reveals_uncommitted_widget_geometry() -> None:
    assert "def _commit_workspace_layout(self)" in STARTUP
    probe = STARTUP.split("def _probe_layout", 1)[1].split("def _flush_native_background", 1)[0]
    assert "self._commit_workspace_layout()" in probe
    prime = STARTUP.split("def _prime_static_runtime", 1)[1].split("def _stage_finish", 1)[0]
    assert prime.index("self._commit_workspace_layout()") < prime.index("self._flush_native_background()")
    handoff = STARTUP.split("def _commit_overlay_handoff", 1)[1].split("def _resume_effects", 1)[0]
    assert handoff.index("self._commit_workspace_layout()") < handoff.index("self._flush_native_background()")
    assert handoff.index("self._flush_native_background()") < handoff.index("overlay.hide()")


def test_layout_commit_sources_compile_without_importing_pyside() -> None:
    compile(COMMIT, "gui/workspace_layout_commit.py", "exec")
    compile(TOGGLE, "gui/mode_toggle.py", "exec")
    compile(BATCH, "gui/batch_card_responsive.py", "exec")
    compile(STARTUP, "gui/startup_entrance_stability.py", "exec")
