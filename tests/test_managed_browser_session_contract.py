from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANAGER = (ROOT / "gui" / "browser_session_manager.py").read_text(encoding="utf-8")
RUN_LOCAL = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_formal_gui_installs_one_managed_makro_browser() -> None:
    assert "install_managed_makro_browser" in RUN_LOCAL
    assert "install_managed_makro_browser(window)" in RUN_LOCAL
    assert '"browser_profiles" / "makro-edge"' in MANAGER
    assert "launch_detached_edge(" in MANAGER
    assert "DEFAULT_START_URL" in MANAGER


def test_cdp_is_hidden_from_normal_single_and_batch_ui() -> None:
    assert "makro_port.setVisible(False)" in MANAGER
    assert "batch_port.setVisible(False)" in MANAGER
    assert "Makro Browser · CHECKING" in MANAGER
    assert "shared login / owned tabs" in MANAGER


def test_single_and_batch_share_one_browser_instead_of_spawning_per_job() -> None:
    assert "window.runner.start = self._start_single" in MANAGER
    assert "window.execution_runner.start = self._start_real" in MANAGER
    assert "self._batch_controller.start_prepare = self._start_batch_prepare" in MANAGER
    assert "self._batch_controller.start_execution = self._start_batch_execute" in MANAGER
    assert "prepare_concurrency" in MANAGER
    assert "execute_concurrency" in MANAGER


def test_browser_generation_uses_endpoint_identity_plus_automation_probe() -> None:
    assert "cdp_endpoint_token(" in MANAGER
    assert "probe_cdp_automation(" in MANAGER
    assert "poison_matches_current_generation(" in MANAGER
    assert "self._generation += 1" in MANAGER
    assert "self._single_prepared_generation != self._generation" in MANAGER
    assert "self._batch_prepare_generation != self._generation" in MANAGER
    assert "owned-tab targetId 已失效" in MANAGER


def test_manager_never_directly_closes_playwright_browser_objects() -> None:
    # Browser-process rotation belongs to the GUI lifecycle owner and must go
    # through the PID-verified update_browser_gate helper. Never close a shared
    # Playwright Browser/Context object or kill an arbitrary process directly.
    assert "browser.close(" not in MANAGER
    assert "context.close(" not in MANAGER
    assert "process.kill(" not in MANAGER
    assert "close_managed_browser(" in MANAGER


def test_auto_recovery_does_not_restart_browser_mid_task() -> None:
    poll = MANAGER.split("    def _poll(self) -> None:", 1)[1].split(
        "    def _start_single", 1
    )[0]
    assert "if self._is_busy():" in poll
    assert "当前任务会安全失败，空闲后自动恢复" in poll
    assert "self.ensure_async()" in poll


def test_login_failure_is_presented_as_user_action_not_browser_failure() -> None:
    assert "window.runner.failed.connect(self._observe_failure)" in MANAGER
    assert "window.execution_runner.failed.connect(self._observe_failure)" in MANAGER
    assert "self._batch_controller.failed.connect(self._observe_failure)" in MANAGER
    assert '"LOGIN": "#f4cb7a"' in MANAGER
    assert "请在已打开的 Makro Browser 完成正常登录后直接重试" in MANAGER
    assert 'self._state not in {"READY", "LOGIN"}' in MANAGER
