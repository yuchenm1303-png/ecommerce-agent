from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUN = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")
TOP = (ROOT / "gui" / "single_top_compact.py").read_text(encoding="utf-8")


def test_compact_single_top_is_installed_after_offer_layers() -> None:
    assert "from gui.single_top_compact import install_single_top_compact" in RUN
    assert "install_listing_offer_support(window)" in RUN
    assert "install_listing_offer_hardening(window)" in RUN
    assert "install_single_top_compact(window)" in RUN
    assert RUN.index("install_listing_offer_hardening(window)") < RUN.index(
        "install_single_top_compact(window)"
    )


def test_compact_geometry_releases_vertical_budget_without_business_wiring() -> None:
    assert "_TOP_CARD_MIN = 238" in TOP
    assert "_TOP_CARD_MAX = 248" in TOP
    assert "_CONTROL_HEIGHT = 30" in TOP
    assert "_SINGLE_PAGE_SPACING = 6" in TOP
    assert "layout.setSpacing(4)" in TOP
    assert "single_layout.setSpacing(_SINGLE_PAGE_SPACING)" in TOP
    assert "QTimer.singleShot(0, lambda: _apply(window))" in TOP
    assert "ReadOnlyRunner(" not in TOP
    assert "RealExecutionRunner(" not in TOP


def test_compact_layout_sources_compile() -> None:
    compile(RUN, str(ROOT / "run_local_gui.py"), "exec")
    compile(TOP, str(ROOT / "gui" / "single_top_compact.py"), "exec")
