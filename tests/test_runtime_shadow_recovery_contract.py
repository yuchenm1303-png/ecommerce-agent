from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SHADOW = (ROOT / "gui" / "runtime_shadow_recovery.py").read_text(encoding="utf-8")
ASSISTANT = (ROOT / "gui" / "runtime_assistant.py").read_text(encoding="utf-8")


def test_shadow_recovery_is_read_only_and_only_runs_after_failure() -> None:
    assert "runner.failed.connect" in SHADOW
    assert "real.failed.connect" in SHADOW
    assert "connect_over_cdp" in SHADOW
    assert "observe_page(" in SHADOW
    assert "RecoveryAgent(provider).analyze" in SHADOW
    assert ".click(" not in SHADOW
    assert ".goto(" not in SHADOW
    assert ".reload(" not in SHADOW
    assert ".go_back(" not in SHADOW
    assert "force=True" not in SHADOW
    assert "browser.close(" not in SHADOW


def test_shadow_recovery_refuses_ambiguous_makro_tabs() -> None:
    assert "if len(pages) != 1:" in SHADOW
    assert "为避免跨 Job 猜 target" in SHADOW
    assert "page_target_id(page)" in SHADOW


def test_recovery_ai_has_short_failure_only_deadline() -> None:
    assert "request_timeout_seconds=30.0" in SHADOW
    assert "Shadow Mode 不会执行 AI 建议" in SHADOW


def test_runtime_assistant_receives_shadow_ai_events() -> None:
    assert "install_runtime_shadow_recovery" in ASSISTANT
    assert "shadow.event_emitted.connect(assistant.present)" in ASSISTANT
