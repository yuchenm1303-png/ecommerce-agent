from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "gui" / "batch_url_editor.py").read_text(encoding="utf-8")
DENSITY = (ROOT / "gui" / "batch_workspace_density.py").read_text(encoding="utf-8")
RUN = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")
BATCH = (ROOT / "gui" / "batch_workspace.py").read_text(encoding="utf-8")


def test_each_batch_link_has_independent_row_switch_and_delete_control() -> None:
    assert "class BatchUrlRow(QFrame):" in SOURCE
    assert 'self.toggle = QPushButton("启用")' in SOURCE
    assert "self.toggle.setCheckable(True)" in SOURCE
    assert 'self.toggle.setText("启用" if checked else "停用")' in SOURCE
    assert 'self.remove_button = QPushButton("×")' in SOURCE
    assert "self.input = QLineEdit(url)" in SOURCE


def test_compact_batch_rail_is_default_and_management_drawer_is_on_demand() -> None:
    assert "_COMPACT_HEIGHT = 42" in SOURCE
    assert "self.setFixedHeight(_COMPACT_HEIGHT)" in SOURCE
    assert "self.drawer.hide()" in SOURCE
    assert 'self.quick_input.setPlaceholderText("输入 supplier URL，Enter 加入任务队列…")' in SOURCE
    assert 'self.paste_button = QPushButton("批量粘贴")' in SOURCE
    assert 'self.manage_button = QPushButton("管理 0 ▾")' in SOURCE
    assert "def set_expanded(self, expanded: bool)" in SOURCE
    assert "_EXPANDED_HEIGHT if self.expanded else _COMPACT_HEIGHT" in SOURCE


def test_multi_paste_is_split_into_individual_rows() -> None:
    assert '_URL_RE = re.compile(r"https?://[^\\s]+"' in SOURCE
    assert "urls = _extract_urls(text)" in SOURCE
    assert "self.add_urls(urls)" in SOURCE
    assert "self.rows_layout.insertWidget" in SOURCE


def test_disabled_rows_are_excluded_from_legacy_batch_prepare_input() -> None:
    assert "def toPlainText(self) -> str:" in SOURCE
    assert "if row.is_enabled() and row.url()" in SOURCE
    assert "urls = normalize_batch_urls(self.url_input.toPlainText())" in BATCH


def test_running_batch_collapses_and_locks_link_management() -> None:
    assert "def setReadOnly(self, read_only: bool)" in SOURCE
    assert "self.set_locked(bool(read_only))" in SOURCE
    assert "self.toggle.setEnabled(not locked)" in SOURCE
    assert "self.remove_button.setEnabled(not locked)" in SOURCE
    assert "self.paste_button.setEnabled(not self.locked)" in SOURCE
    assert "self.add_button.setEnabled(not self.locked)" in SOURCE
    assert "if self.locked:\n            self.set_expanded(False)" in SOURCE


def test_batch_density_keeps_detailed_summary_cards_and_expanding_job_surface() -> None:
    assert '"TOTAL · 全部任务"' in DENSITY
    assert '"PROCESSING · 处理中"' in DENSITY
    assert '"READY · 可执行"' in DENSITY
    assert "card.setMinimumWidth(150)" in DENSITY
    assert "card.setMinimumHeight(70)" in DENSITY
    assert "detail = QLabel(detail_text)" in DENSITY
    assert "queue_card.setMinimumHeight(430)" in DENSITY
    assert "root.setStretch(2, 1)" in DENSITY


def test_formal_launcher_installs_compact_batch_density_after_editor() -> None:
    assert "from gui.batch_url_editor import install_batch_url_editor" in RUN
    assert "from gui.batch_workspace_density import install_batch_workspace_density" in RUN
    assert "window.install_mode_workspace()" in RUN
    assert "install_batch_url_editor(window.batch_workspace)" in RUN
    assert "install_batch_workspace_density(window.batch_workspace)" in RUN
    assert RUN.index("install_batch_url_editor(window.batch_workspace)") < RUN.index(
        "install_batch_workspace_density(window.batch_workspace)"
    )


def test_batch_presentation_sources_compile_without_importing_pyside() -> None:
    compile(SOURCE, str(ROOT / "gui" / "batch_url_editor.py"), "exec")
    compile(DENSITY, str(ROOT / "gui" / "batch_workspace_density.py"), "exec")
