from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BATCH_FILES_UI = (ROOT / "gui" / "batch_product_files.py").read_text(encoding="utf-8")
BATCH_JOB = (ROOT / "makro_batch_job.py").read_text(encoding="utf-8")
ENTRY = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_batch_rows_expose_per_product_supplemental_files() -> None:
    assert 'QPushButton("资料 0"' in BATCH_FILES_UI
    assert "getOpenFileNames" in BATCH_FILES_UI
    assert "product_files" in BATCH_FILES_UI
    assert "_files_by_url" in BATCH_FILES_UI
    assert "url.casefold()" in BATCH_FILES_UI


def test_batch_prepare_forwards_files_only_to_the_owned_job() -> None:
    assert 'if stage == "prepare"' in BATCH_FILES_UI
    assert 'argv.extend(["--product-file", str(path)])' in BATCH_FILES_UI
    assert '"--product-file"' in BATCH_JOB
    assert "acquire_product_input(" in BATCH_JOB
    assert "product_files=args.product_file" in BATCH_JOB
    assert "_prepare_step3_pack(" in BATCH_JOB


def test_batch_product_file_layer_is_installed_after_row_inputs() -> None:
    assert "from gui.batch_product_files import install_batch_product_files" in ENTRY
    assert ENTRY.index("install_batch_sku_spec_ui(window)") < ENTRY.index(
        "install_batch_product_files(window)"
    )
