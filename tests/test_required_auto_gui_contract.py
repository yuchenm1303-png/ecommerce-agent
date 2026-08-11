from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SUPPORT = (ROOT / "gui" / "required_input_support.py").read_text(encoding="utf-8")
HELPER = (ROOT / "makro_complete_required.py").read_text(encoding="utf-8")
COMPLETION = (ROOT / "app" / "required_field_completion.py").read_text(encoding="utf-8")
OVERRIDES = (ROOT / "app" / "required_overrides.py").read_text(encoding="utf-8")


def test_full_step3_no_longer_requires_manual_required_values_before_start():
    assert "AI 自动补齐" in SUPPORT
    assert "留空即可在真实填写前由 AI 自动补齐" in SUPPORT
    assert "self._start_auto_completion(result)" in SUPPORT
    assert "self.window.real_start_button.setEnabled(can_start)" in SUPPORT
    assert "setEnabled(ready and result.ready > 0)" not in SUPPORT
    assert "请先在字段表补齐" not in SUPPORT


def test_required_completion_runs_out_of_process_before_browser_execution():
    assert "QProcess" in SUPPORT
    assert '"makro_complete_required.py"' in SUPPORT
    assert "process.start(sys.executable, args)" in SUPPORT
    assert "QTimer.singleShot(0, self._original_start)" in SUPPORT
    assert "Full Step 3 尚未开始写入" in SUPPORT


def test_manual_required_text_remains_optional_override_and_wins_over_model():
    assert "merged = {" in SUPPORT
    assert '"source_type": "user"' in SUPPORT
    assert "merged[identifier] =" in SUPPORT
    assert "identifier in self._auto_overrides" in SUPPORT
    assert "你仍可输入手动值覆盖它" in SUPPORT


def test_required_helper_reuses_current_resolver_model_config_and_evidence():
    assert 'manifest.get("fact_provider_config")' in HELPER
    assert 'outputs.get("compact_evidence")' in HELPER
    assert "build_semantic_provider" in HELPER
    assert "build_required_completion_request" in HELPER
    assert "parse_required_completion_response" in HELPER


def test_targeted_required_completion_never_bypasses_live_hard_guards():
    assert "_hard_guard_values" in COMPLETION
    assert "identifier-like field requires explicit supplied evidence" in COMPLETION
    assert "placeholder-like model value rejected" in COMPLETION
    assert 'source_type = str(override.get("source_type") or "user")' in OVERRIDES
    assert 'source_type not in {"user", "model"}' in OVERRIDES


def test_new_required_sources_compile_without_importing_gui_runtime():
    for relative in (
        "app/required_field_completion.py",
        "makro_complete_required.py",
        "gui/required_input_support.py",
        "app/required_overrides.py",
    ):
        path = ROOT / relative
        compile(path.read_text(encoding="utf-8"), str(path), "exec")
