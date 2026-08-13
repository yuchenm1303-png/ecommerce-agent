from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUN = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")
SOURCE = (ROOT / "gui" / "batch_card_responsive.py").read_text(encoding="utf-8")


def test_formal_gui_installs_responsive_batch_cards_after_job_controls() -> None:
    assert "from gui.batch_card_responsive import install_batch_card_responsive" in RUN
    assert "install_batch_job_controls(window.batch_workspace)" in RUN
    assert "install_batch_card_responsive(window.batch_workspace)" in RUN
    assert RUN.index("install_batch_job_controls(window.batch_workspace)") < RUN.index(
        "install_batch_card_responsive(window.batch_workspace)"
    )


def test_job_host_and_cards_are_capped_to_real_viewport_width() -> None:
    assert "self.jobs_host.setMinimumWidth(0)" in SOURCE
    assert "self.jobs_host.setMaximumWidth(viewport_width)" in SOURCE
    assert "self.jobs_host.resize(viewport_width, self.jobs_host.height())" in SOURCE
    assert "card.setMinimumWidth(0)" in SOURCE
    assert "card.setMaximumWidth(content_width)" in SOURCE
    assert "ScrollBarAlwaysOff" in SOURCE


def test_unbounded_text_cannot_define_card_minimum_width() -> None:
    for token in (
        '"product_label"',
        '"url_label"',
        '"phase_label"',
        '"meta_label"',
        '"detail_label"',
        '"log_preview"',
        '"details_meta"',
        "QSizePolicy.Policy.Ignored",
    ):
        assert token in SOURCE


def test_supplier_url_is_middle_elided_without_losing_authoritative_value() -> None:
    assert 'getattr(job, "product_url", "")' in SOURCE
    assert "Qt.TextElideMode.ElideMiddle" in SOURCE
    assert "label.setToolTip(url)" in SOURCE


def test_resize_and_job_updates_reapply_constraints() -> None:
    assert "jobs_changed.connect" in SOURCE
    assert "QEvent.Type.Resize" in SOURCE
    assert "QEvent.Type.LayoutRequest" in SOURCE
    assert "self.schedule_refresh()" in SOURCE


def test_responsive_source_compiles_without_importing_pyside() -> None:
    compile(SOURCE, str(ROOT / "gui" / "batch_card_responsive.py"), "exec")
