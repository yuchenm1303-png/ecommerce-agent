from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REAL = (ROOT / "gui" / "real_execution.py").read_text(encoding="utf-8")
BRIDGE = (ROOT / "gui" / "quick_bridge.py").read_text(encoding="utf-8")
QML = (ROOT / "gui" / "qml" / "Main.qml").read_text(encoding="utf-8")
EXECUTOR = (ROOT / "makro_execute_listing.py").read_text(encoding="utf-8")


def test_real_execution_reuses_completed_read_only_artifacts() -> None:
    assert 'latest_live_schema(run_dir)' in REAL
    assert 'latest_resolver_manifest(run_dir, "03-hot-resolver")' in REAL
    assert 'run_dir / "04-fill-plan"' in REAL
    assert '"makro_execute_listing.py"' in REAL
    assert '"--decision-packet"' in REAL
    assert '"--supplier-snapshot"' in REAL


def test_save_is_opt_in_and_full_step3_still_requires_it() -> None:
    assert 'config.scope == FULL_STEP3 and not config.allow_save' in REAL
    assert 'args.append("--all-step3")' in REAL
    assert 'if config.allow_save:' in REAL
    assert 'args.append("--allow-section-save")' in REAL
    assert 'id: saveCheck' in QML
    assert 'bridge.startReal(row.value, saveCheck.checked, uploadCheck.checked)' in QML
    assert 'if scope == FULL_STEP3 and not allow_save:' in BRIDGE
    assert 'persist=args.allow_section_save' in EXECUTOR
    assert 'allow_save=args.allow_section_save' in EXECUTOR


def test_image_upload_is_opt_in_and_qc_stays_policy_locked() -> None:
    assert 'id: uploadCheck' in QML
    assert 'bridge.selectedImageCount' in QML
    assert 'Send to QC · LOCKED' in QML
    assert 'enabled: false' in QML
    assert 'args.extend(["--upload-image", str(image)])' in REAL
    assert 'send_to_qc=False (repository policy lock)' in REAL
    assert '"--send-to-qc"' not in REAL
    assert '"send_to_qc_clicked": False' in EXECUTOR
