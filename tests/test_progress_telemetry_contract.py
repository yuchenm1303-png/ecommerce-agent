from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOMAIN = (ROOT / "app" / "makro" / "domain.py").read_text(encoding="utf-8")
ACTIVITY = (ROOT / "gui" / "activity_presence.py").read_text(encoding="utf-8")


def test_domain_emits_field_telemetry_around_the_existing_fill_call() -> None:
    block = DOMAIN.split("    def fill_resolved_field(", 1)[1].split(
        "    def verify_resolved_field(", 1
    )[0]
    assert 'GUI_EXEC_FIELD\\tSTART' in block
    assert 'GUI_EXEC_FIELD\\tCOMPLETE' in block
    assert "verification = fill_resolved_field(" in block
    assert block.index('GUI_EXEC_FIELD\\tSTART') < block.index("verification = fill_resolved_field(")
    assert block.index("verification = fill_resolved_field(") < block.index('GUI_EXEC_FIELD\\tCOMPLETE')
    assert "return verification" in block


def test_field_telemetry_is_observability_only_not_a_second_decision_gate() -> None:
    block = DOMAIN.split("    def fill_resolved_field(", 1)[1].split(
        "    def verify_resolved_field(", 1
    )[0]
    assert "_ensure_answer_value_slots(" in block
    assert "if verification.status" not in block
    assert "skip" not in block.casefold()
    assert "READY" not in block
    assert "BLOCKED" not in block


def test_gui_maps_field_completion_to_continuous_real_work_progress() -> None:
    assert "self._real_field_done += 1" in ACTIVITY
    assert "68 * self._real_field_done / max(1, self._real_field_total)" in ACTIVITY
    assert "字段 {next_index}/{self._real_field_total}" in ACTIVITY
    assert "Section {len(self._real_sections_done)}/3" in ACTIVITY
    assert "图片 {staged}/{requested} staged" in ACTIVITY


def test_waiting_animation_does_not_advance_business_progress() -> None:
    animate = ACTIVITY.split("    def _animate(self)", 1)[1].split(
        "    def paintEvent", 1
    )[0]
    assert "self.target_percent" in animate
    assert "self.target_percent +=" not in animate
    assert "_motion_time_s" in animate
