from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COPY = (ROOT / "gui" / "product_copy.py").read_text(encoding="utf-8")
RUN = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_primary_workflow_uses_product_language() -> None:
    for token in (
        '"采集商品"',
        '"识别类目"',
        '"识别品牌"',
        '"生成填写方案"',
        '"开始填写"',
        '"填写后保存并复核"',
        '"送审需手动完成"',
        '"商品上架助手"',
    ):
        assert token in COPY


def test_batch_copy_is_customer_facing() -> None:
    for token in (
        '"商品总数"',
        '"处理中"',
        '"可填写"',
        '"已完成"',
        '"待处理"',
        '"填写此商品"',
        '"任务目录"',
        '"查看详情"',
        '"个链接 · "',
    ):
        assert token in COPY


def test_engineering_terms_are_translated_at_presentation_boundary() -> None:
    translations = (
        '("Full Step 3", "完整填写")',
        '("Resolver", "字段匹配")',
        '("Fill Plan", "填写方案")',
        '("owned tabs", "独立标签页")',
        '("Shadow Mode", "仅提示，不自动操作")',
        '("Send to QC", "送审")',
    )
    for token in translations:
        assert token in COPY


def test_product_copy_installs_before_first_window_show() -> None:
    assert "from gui.product_copy import install_product_copy" in RUN
    assert "product_copy = install_product_copy(window)" in RUN
    assert "product_copy.attach_runtime_assistant(assistant)" in RUN
    assert RUN.index("product_copy = install_product_copy(window)") < RUN.index("shell.show()")
    assert RUN.index("assistant = install_runtime_assistant(window)") < RUN.index(
        "product_copy.attach_runtime_assistant(assistant)"
    )
    assert RUN.index("product_copy.attach_runtime_assistant(assistant)") < RUN.index(
        "entrance_stability.start()"
    )


def test_product_copy_sources_compile_without_importing_pyside() -> None:
    compile(COPY, str(ROOT / "gui" / "product_copy.py"), "exec")
    compile(RUN, str(ROOT / "run_local_gui.py"), "exec")
