from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UPDATER = (ROOT / "gui" / "app_updater.py").read_text(encoding="utf-8")
RUNTIME = (ROOT / "app" / "velopack_runtime.py").read_text(encoding="utf-8")


def test_update_discovery_uses_bounded_first_party_metadata_request() -> None:
    assert "PORTAL_RELEASE_URL" in RUNTIME
    assert "_UPDATE_DISCOVERY_TIMEOUT_SECONDS = 8" in RUNTIME
    assert "urllib.request.urlopen(request, timeout=_UPDATE_DISCOVERY_TIMEOUT_SECONDS)" in RUNTIME
    assert "resolve_stable_update_source" in RUNTIME


def test_client_no_longer_spends_github_api_request_when_already_current() -> None:
    assert "advertised_key <= current_key" in UPDATER
    assert "resolve_stable_update_source()" in UPDATER
    assert "create_update_manager(source_url)" in UPDATER
    assert 'self._check_button.setText("网络较慢…")' in UPDATER


def test_confirmed_update_is_downloaded_without_second_release_check() -> None:
    assert UPDATER.count("manager.check_for_updates()") == 1
    assert "manager.download_updates(info, _progress)" in UPDATER
    assert 'self._begin_update(latest, info, source_url)' in UPDATER
    assert "已从 Stable 通道撤回" not in UPDATER


def test_network_failures_keep_current_version_and_show_actionable_message() -> None:
    assert "update_service_unreachable" in RUNTIME
    assert "_friendly_update_error" in UPDATER
    assert "暂时无法连接更新服务" in UPDATER
    assert "Listing Studio 已保持当前版本运行" in UPDATER
