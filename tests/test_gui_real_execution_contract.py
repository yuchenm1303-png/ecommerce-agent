from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REAL = (ROOT / "gui" / "real_execution.py").read_text(encoding="utf-8")
WINDOW = (ROOT / "gui" / "console_window.py").read_text(encoding="utf-8")


def test_real_execution_reuses_completed_read_only_artifacts() -> None:
    assert 'latest_live_schema(run_dir)' in REAL
    assert 'latest_resolver_manifest(run_dir, "03-hot-resolver")' in REAL
    assert 'run_dir / "04-fill-plan"' in REAL
    assert '"makro_execute_listing.py"' in REAL
    assert '"--decision-packet"' in REAL
    assert '"--supplier-snapshot"' in REAL


def test_full_step3_requires_explicit_save_authorization() -> None:
    assert 'config.scope == FULL_STEP3 and not config.allow_save' in REAL
    assert 'args.extend(["--all-step3", "--allow-section-save"])' in REAL
    assert 'self.real_save_check.setChecked(False)' in WINDOW


def test_image_upload_is_opt_in_and_qc_stays_policy_locked() -> None:
    assert 'self.real_upload_check.setChecked(False)' in WINDOW
    assert 'args.extend(["--upload-image", str(image)])' in REAL
    assert 'self.real_qc_check.setEnabled(False)' in WINDOW
    assert 'send_to_qc=False (repository policy lock)' in REAL
    assert '"--send-to-qc"' not in REAL
