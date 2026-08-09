from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POLISH = (ROOT / "gui" / "ui_polish.py").read_text(encoding="utf-8")
RUNNER = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")


def test_standard_gui_installs_polish_after_native_visual_layer() -> None:
    assert "from gui.console_window import MainWindow" in RUNNER
    assert "visual = install_native_visual_style(window)" in RUNNER
    assert "install_ui_polish(window)" in RUNNER
    assert RUNNER.index("visual = install_native_visual_style(window)") < RUNNER.index(
        "install_ui_polish(window)"
    )
    assert RUNNER.index("install_ui_polish(window)") < RUNNER.index(
        "install_nekro_card_fx(window, visual)"
    )


def test_polish_rebalances_workspace_without_business_replacement() -> None:
    assert 'body = QSplitter(Qt.Orientation.Vertical, root)' in POLISH
    assert 'body.setObjectName("bodySplitter")' in POLISH
    assert "workspace.setMinimumHeight(310)" in POLISH
    assert "body.setSizes([520, 270])" in POLISH
    assert 'workspace_splitter.setObjectName("workspaceSplitter")' in POLISH
    assert "workspace_splitter.setSizes([1180, 360])" in POLISH
    assert "Resolver" not in POLISH
    assert "RealExecutionConfig" not in POLISH
    assert "Send to QC" not in POLISH


def test_trace_tables_have_readable_density_and_widths() -> None:
    assert "_configure_data_table(table, minimum_height=300)" in POLISH
    assert "vertical.setDefaultSectionSize(42)" in POLISH
    assert "vertical.setMinimumSectionSize(40)" in POLISH
    assert "header.setMinimumHeight(42)" in POLISH
    assert "header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)" in POLISH
    assert "header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)" in POLISH
    assert "table.setTextElideMode(Qt.TextElideMode.ElideRight)" in POLISH


def test_data_surfaces_are_more_opaque_than_wallpaper_glass() -> None:
    assert "background-color: rgba(3,8,14,158);" in POLISH
    assert "background-color: rgba(13,21,31,212);" in POLISH
    assert "background-color: rgba(2,7,13,174);" in POLISH
    assert "QComboBox QAbstractItemView" in POLISH
    assert "QTabWidget::pane" in POLISH


def test_real_execution_actions_are_reflowed_not_reimplemented() -> None:
    assert "controls.removeWidget(window.real_start_button)" in POLISH
    assert "controls.removeWidget(window.real_stop_button)" in POLISH
    assert "layout.removeWidget(window.real_policy_hint)" in POLISH
    assert "action_row.addWidget(window.real_policy_hint, 1)" in POLISH
    assert "action_row.addWidget(window.real_start_button)" in POLISH
    assert "action_row.addWidget(window.real_stop_button)" in POLISH


def test_polish_refreshes_existing_quick_glass_geometry() -> None:
    assert "background.schedule_mask_update()" in POLISH
    assert "install_native_window_shell(window, quick_window)" in RUNNER
    assert "install_nekro_effects(window, sakura_count=3)" in RUNNER
