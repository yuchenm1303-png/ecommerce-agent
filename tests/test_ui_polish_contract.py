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


def test_workspace_prioritizes_fields_and_compacts_console() -> None:
    assert 'body = QSplitter(Qt.Orientation.Vertical, root)' in POLISH
    assert 'body.setObjectName("bodySplitter")' in POLISH
    assert "workspace.setMinimumHeight(260)" in POLISH
    assert "body.setSizes([650, 122])" in POLISH
    assert 'workspace_splitter.setObjectName("workspaceSplitter")' in POLISH
    assert "workspace_splitter.setSizes([1220, 350])" in POLISH
    assert "console.setMinimumHeight(112)" in POLISH
    assert "console.setMaximumHeight(132)" in POLISH
    assert "from .readonly_runner" not in POLISH
    assert "from .real_execution" not in POLISH
    assert "RealExecutionConfig(" not in POLISH


def test_real_execution_options_are_collapsible_without_reimplementation() -> None:
    assert "def _install_real_execution_collapse" in POLISH
    assert 'toggle = QPushButton("展开设置")' in POLISH
    assert 'toggle.setText("收起设置" if expanded else "展开设置")' in POLISH
    assert "controls.removeWidget(window.real_start_button)" in POLISH
    assert "controls.removeWidget(window.real_stop_button)" in POLISH
    assert "action_row.addWidget(window.real_policy_hint, 1)" in POLISH
    assert "action_row.addWidget(window.real_start_button)" in POLISH
    assert "action_row.addWidget(window.real_stop_button)" in POLISH


def test_side_diagnostics_are_tabbed_instead_of_stacked() -> None:
    assert "def _tabify_side_panel" in POLISH
    assert 'tabs.setObjectName("sideDetailTabs")' in POLISH
    assert 'tabs.addTab(runtime_card, "Telemetry")' in POLISH
    assert 'tabs.addTab(web_card, "Web")' in POLISH
    assert 'tabs.addTab(safety_card, "Safety")' in POLISH
    assert "side_layout.addWidget(tabs, 1)" in POLISH


def test_console_details_expand_on_demand() -> None:
    assert "def _install_console_collapse" in POLISH
    assert 'toggle = QPushButton("展开详情")' in POLISH
    assert 'toggle.setText("收起详情" if expanded else "展开详情")' in POLISH
    assert "unit.setVisible(expanded)" in POLISH
    assert "tabs.setVisible(expanded)" in POLISH
    assert "console.setMaximumHeight(620)" in POLISH


def test_trace_tables_keep_readable_density_and_compact_header() -> None:
    assert "_configure_data_table(table, minimum_height=220)" in POLISH
    assert "vertical.setDefaultSectionSize(40)" in POLISH
    assert "vertical.setMinimumSectionSize(38)" in POLISH
    assert "header.setMinimumHeight(39)" in POLISH
    assert "header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)" in POLISH
    assert "header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)" in POLISH
    assert "table.setTextElideMode(Qt.TextElideMode.ElideRight)" in POLISH
    assert "def _compact_field_header" in POLISH


def test_polish_keeps_baseline_neutral_glass_palette() -> None:
    assert "background-color: rgba(0,0,0,58);" in POLISH
    assert "background-color: rgba(255,255,255,28);" in POLISH
    assert "background-color: rgba(0,0,0,74);" in POLISH
    assert "background-color: rgba(0,0,0,62);" in POLISH
    assert "rgba(13,21,31,212)" not in POLISH
    assert "rgba(2,7,13,174)" not in POLISH
    assert "QComboBox QAbstractItemView" in POLISH
    assert "QTabWidget#sideDetailTabs::pane" in POLISH


def test_dynamic_layout_changes_refresh_quick_glass_geometry() -> None:
    assert "def _schedule_glass" in POLISH
    assert "background.schedule_mask_update()" in POLISH
    assert "body.splitterMoved.connect" in POLISH
    assert "workspace_splitter.splitterMoved.connect" in POLISH
    assert "tabs.currentChanged.connect" in POLISH
    assert "install_native_window_shell(window, quick_window)" in RUNNER
    assert "install_nekro_effects(window, sakura_count=3)" in RUNNER
