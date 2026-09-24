from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ACCOUNT_SURFACE = (ROOT / "gui" / "channel_account_surface.py").read_text(encoding="utf-8")
BATCH_WORKSPACE = (ROOT / "gui" / "batch_workspace.py").read_text(encoding="utf-8")
BATCH_RUNTIME = (ROOT / "gui" / "batch_parallel_runtime.py").read_text(encoding="utf-8")
QUICK_BATCH = (ROOT / "gui" / "quick_batch_list.py").read_text(encoding="utf-8")
STATIC_BRIDGE = (ROOT / "gui" / "static_qml_bridge.py").read_text(encoding="utf-8")
STATIC_SCENE = (ROOT / "gui" / "static_qml_scene.py").read_text(encoding="utf-8")
QUICK_MODAL = (ROOT / "gui" / "quick_modal_layer.py").read_text(encoding="utf-8")


def test_multi_account_ui_sources_are_python_syntax_valid() -> None:
    for relative in (
        "gui/channel_account_surface.py",
        "gui/batch_workspace.py",
        "gui/batch_parallel_runtime.py",
        "gui/quick_batch_list.py",
        "gui/static_qml_bridge.py",
        "gui/static_qml_scene.py",
        "gui/quick_modal_layer.py",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        compile(source, str(ROOT / relative), "exec")


def test_account_center_is_a_card_workspace_not_only_a_text_summary() -> None:
    assert "Makro 店铺工作台" in ACCOUNT_SURFACE
    assert 'frame.setObjectName("channelAccountCard")' in ACCOUNT_SURFACE
    assert 'badge.setObjectName("channelAccountStatusBadge")' in ACCOUNT_SURFACE
    assert '"切换查看"' in ACCOUNT_SURFACE
    assert '"后台运行"' in ACCOUNT_SURFACE
    assert "def _refresh_account_cards" in ACCOUNT_SURFACE
    assert "def _queue_quick_refresh" in ACCOUNT_SURFACE
    assert "_queue_controls_refresh" in ACCOUNT_SURFACE
    assert "def _switch_account" in ACCOUNT_SURFACE


def test_global_header_exposes_the_current_makro_store() -> None:
    assert "def _refresh_button_label" in ACCOUNT_SURFACE
    assert 'self.button.setText(f"Makro · {compact}")' in ACCOUNT_SURFACE
    assert "self.manager.status_changed.connect(self._refresh_button_label)" in ACCOUNT_SURFACE


def test_batch_workspace_has_persistent_account_context() -> None:
    assert 'frame.setObjectName("batchAccountContext")' in BATCH_WORKSPACE
    assert 'self.account_context_status.setObjectName("batchAccountStatusBadge")' in BATCH_WORKSPACE
    assert "def set_account_context" in BATCH_WORKSPACE
    assert "后台 {int(running_others)} 个店铺仍在运行" in BATCH_WORKSPACE
    assert "当前店铺 ·" in BATCH_WORKSPACE


def test_runtime_updates_account_context_for_background_lanes() -> None:
    assert "def _refresh_workspace_account_context" in BATCH_RUNTIME
    assert "running_others" in BATCH_RUNTIME
    assert "self.manager.status_changed.connect(self._refresh_workspace_account_context)" in BATCH_RUNTIME
    assert "lane_running.connect(self._on_lane_runtime_changed)" in BATCH_RUNTIME
    assert "lane_state.connect(self._on_lane_state_changed)" in BATCH_RUNTIME


def test_quick_batch_cards_show_account_ownership_and_isolate_visual_logs() -> None:
    assert '"accountLabel"' in QUICK_BATCH
    assert "jobCard.accountLabel" in QUICK_BATCH
    assert "Makro account:" in QUICK_BATCH
    assert "Makro CDP lane:" in QUICK_BATCH
    assert "def _log_key" in QUICK_BATCH
    assert "def _is_background_lane_event" in QUICK_BATCH
    assert "if self._is_background_lane_event():" in QUICK_BATCH
    assert "scope_changed" in QUICK_BATCH


def test_quick_renderers_recognize_account_cards_and_status_badges() -> None:
    for name in (
        "channelAccountCard",
        "batchAccountContext",
        "channelAccountStatusBadge",
        "batchAccountStatusBadge",
    ):
        assert name in STATIC_BRIDGE
    assert "channelAccountCard" in QUICK_MODAL
    assert "channelAccountStatusBadge" in QUICK_MODAL
    assert "batchAccountContext" in STATIC_SCENE
    assert "batchAccountStatusBadge" in STATIC_SCENE
