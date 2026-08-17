from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMMIT = (ROOT / "gui" / "workspace_layout_commit.py").read_text(encoding="utf-8")
TOGGLE = (ROOT / "gui" / "mode_toggle.py").read_text(encoding="utf-8")
BATCH = (ROOT / "gui" / "batch_card_responsive.py").read_text(encoding="utf-8")
STARTUP = (ROOT / "gui" / "startup_entrance_stability.py").read_text(encoding="utf-8")


def test_target_workspace_is_prelaid_before_animated_mode_switch() -> None:
    assert "class WorkspaceLayoutCommitter(QObject)" in COMMIT
    assert "def prepare_page(self, index: int)" in COMMIT
    assert "def prime_all(self)" in COMMIT
    assert "page.setGeometry(target_rect)" in COMMIT
    assert "self.stack.currentChanged.connect" not in COMMIT

    assert "layout_keeper = install_workspace_layout_commit(window)" in TOGGLE
    request = TOGGLE.split("def request_mode", 1)[1].split("toggle.clicked.connect", 1)[0]
    assert request.index("prepare_page(target)") < request.index("request(target)")
    assert "setRetainSizeWhenHidden(True)" in TOGGLE


def test_hidden_workspace_tracks_real_stack_resize_without_current_changed_reflow() -> None:
    assert "self.stack.installEventFilter(self)" in COMMIT
    event_filter = COMMIT.split("def eventFilter", 1)[1].split("def cleanup", 1)[0]
    assert "QEvent.Type.Resize" in event_filter
    assert "QEvent.Type.Show" in event_filter
    assert "self.prime_all()" in event_filter
    assert "currentChanged" not in event_filter
    assert "processEvents" not in COMMIT
    assert "sleep(" not in COMMIT


def test_single_and_batch_specific_geometry_rules_are_settled_while_hidden() -> None:
    assert "refresh_single_source_layout(self.window)" in COMMIT
    assert 'getattr(responsive, "commit_now", None)' in COMMIT
    assert "_activate_layout_tree(page)" in COMMIT
    assert "def commit_now(self)" in BATCH
    assert "self._sync_width()" in BATCH


def test_batch_show_width_fix_is_synchronous_and_never_zero_delay() -> None:
    assert "from PySide6.QtCore import QEvent, QObject, Qt" in BATCH
    assert "QTimer" not in BATCH
    assert "QTimer.singleShot" not in BATCH
    assert "jobs_changed.connect(lambda _jobs: self.commit_now())" in BATCH
    assert "def schedule_refresh(self)" in BATCH
    assert "self.commit_now()" in BATCH

    event_filter = BATCH.split("def eventFilter", 1)[1].split(
        "def install_batch_card_responsive", 1
    )[0]
    assert "QEvent.Type.Resize" in event_filter
    assert "QEvent.Type.Show" in event_filter
    assert "QEvent.Type.LayoutRequest" not in event_filter
    assert "self.commit_now()" in event_filter


def test_startup_stability_gate_remains_compatible_with_geometry_keeper() -> None:
    assert "from .workspace_layout_commit import install_workspace_layout_commit" in STARTUP
    assert "self._workspace_layout_commit = install_workspace_layout_commit(window)" in STARTUP
    assert "def _commit_workspace_layout(self)" in STARTUP
    assert 'getattr(self._workspace_layout_commit, "commit_current", None)' in STARTUP

    prime = STARTUP.split("def _prime_static_runtime", 1)[1].split("def _stage_finish", 1)[0]
    assert prime.index("self._commit_workspace_layout()") < prime.index("self._flush_native_background()")
    handoff = STARTUP.split("def _commit_overlay_handoff", 1)[1].split("def _resume_effects", 1)[0]
    assert handoff.index("self._commit_workspace_layout()") < handoff.index("self._flush_native_background()")
    assert handoff.index("self._flush_native_background()") < handoff.index("overlay.hide()")


def test_layout_sources_compile_without_importing_pyside() -> None:
    compile(COMMIT, "gui/workspace_layout_commit.py", "exec")
    compile(TOGGLE, "gui/mode_toggle.py", "exec")
    compile(BATCH, "gui/batch_card_responsive.py", "exec")
    compile(STARTUP, "gui/startup_entrance_stability.py", "exec")
