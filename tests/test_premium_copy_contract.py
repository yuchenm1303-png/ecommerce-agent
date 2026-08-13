from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PREMIUM = (ROOT / "gui" / "premium_copy.py").read_text(encoding="utf-8")
RUN = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_premium_copy_keeps_professional_english_hierarchy() -> None:
    for token in (
        '"MAKRO LISTING AUTOMATION"',
        '"Listing Studio"',
        '"PRODUCT SOURCE"',
        '"FIELD REVIEW"',
        '"LISTING CONTROL"',
        '"BATCH QUEUE"',
        '"RUNTIME"',
        '"REFERENCE"',
        '"SAFETY"',
        '"WORKFLOW"',
    ):
        assert token in PREMIUM


def test_compact_states_and_tabs_are_english() -> None:
    for token in (
        '"READY"',
        '"MISSING"',
        '"CONFLICT"',
        '"BLOCKED"',
        '"Console"',
        '"Timeline"',
        '"Artifacts"',
        '"Diagnostics"',
        '"Fill Log"',
        '"TOTAL"',
        '"ACTIVE"',
        '"DONE"',
        '"REVIEW"',
        '"FAILED"',
    ):
        assert token in PREMIUM


def test_actions_remain_concise_chinese_in_base_product_copy() -> None:
    product = (ROOT / "gui" / "product_copy.py").read_text(encoding="utf-8")
    for token in (
        '"一键准备商品"',
        '"识别类目"',
        '"识别品牌"',
        '"生成填写方案"',
        '"开始填写"',
        '"填写后保存并复核"',
        '"送审需手动完成"',
    ):
        assert token in product


def test_premium_layer_installs_after_product_copy_before_startup_snapshot() -> None:
    assert "from gui.premium_copy import install_premium_copy" in RUN
    assert "premium_copy = install_premium_copy(window)" in RUN
    assert RUN.index("product_copy = install_product_copy(window)") < RUN.index(
        "premium_copy = install_premium_copy(window)"
    )
    assert RUN.index("premium_copy = install_premium_copy(window)") < RUN.index(
        "entrance = install_startup_entrance(window, visual)"
    )
    assert "premium_copy.attach_runtime_assistant(assistant)" in RUN


def test_premium_copy_sources_compile_without_importing_pyside() -> None:
    compile(PREMIUM, str(ROOT / "gui" / "premium_copy.py"), "exec")
    compile(RUN, str(ROOT / "run_local_gui.py"), "exec")
