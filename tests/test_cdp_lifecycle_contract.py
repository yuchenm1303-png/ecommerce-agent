from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANAGER = (ROOT / "gui" / "browser_session_manager.py").read_text(encoding="utf-8")
SOURCE = (ROOT / "app" / "source_capture.py").read_text(encoding="utf-8")
HEALTH = (ROOT / "app" / "cdp_automation_health.py").read_text(encoding="utf-8")


def test_endpoint_reachability_is_not_treated_as_automation_health() -> None:
    assert "probe_cdp_automation(" in MANAGER
    assert '"CHECKING", "检测到 Makro Browser · 正在验证自动化控制"' in MANAGER
    assert "connect_over_cdp(" in HEALTH
    assert "AUTOMATION_READY" in HEALTH
    assert "POISONED" in HEALTH


def test_poisoned_makro_generation_rotates_only_at_idle_boundary() -> None:
    recovery = MANAGER.split("    def _recover_poisoned_locked", 1)[1].split(
        "    def ensure_ready", 1
    )[0]
    assert "if self._is_busy():" in recovery
    assert "close_managed_browser(" in recovery
    assert "launch_detached_edge(" in recovery
    assert "profile_dir=self.profile_dir" in recovery
    assert "probe_cdp_automation(" in recovery
    assert "旧 owned tabs 已失效" in recovery


def test_browser_generation_change_invalidates_prepared_target_ids() -> None:
    assert "self._generation += 1" in MANAGER
    assert "_observe_recovered_instance" in MANAGER
    assert "self._single_prepared_generation != self._generation" in MANAGER
    assert "self._batch_prepare_generation != self._generation" in MANAGER
    assert "owned-tab targetId 已失效" in MANAGER


def test_source_edge_has_separate_bounded_recovery_path() -> None:
    assert "looks_like_cdp_transport_failure" in SOURCE
    assert "close_managed_browser(port=port" in SOURCE
    assert "launch_detached_edge(" in SOURCE
    assert "probe_cdp_automation(port, timeout_ms=8_000)" in SOURCE
    assert "SOURCE_CDP RECOVERED" in SOURCE
    assert "if not looks_like_cdp_transport_failure(exc):" in SOURCE
