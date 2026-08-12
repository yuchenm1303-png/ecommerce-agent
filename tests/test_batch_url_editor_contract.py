from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "gui" / "batch_url_editor.py").read_text(encoding="utf-8")
RUN = (ROOT / "run_local_gui.py").read_text(encoding="utf-8")
BATCH = (ROOT / "gui" / "batch_workspace.py").read_text(encoding="utf-8")


def test_each_batch_link_has_independent_row_switch_and_delete_control() -> None:
    assert "class BatchUrlRow(QFrame):" in SOURCE
    assert 'self.toggle = QPushButton("启用")' in SOURCE
    assert "self.toggle.setCheckable(True)" in SOURCE
    assert 'self.toggle.setText("启用" if checked else "停用")' in SOURCE
    assert 'self.remove_button = QPushButton("删除")' in SOURCE
    assert "self.input = QLineEdit(url)" in SOURCE


def test_multi_paste_is_split_into_individual_rows() -> None:
    assert '_URL_RE = re.compile(r"https?://[^\\s]+"' in SOURCE
    assert 'self.paste_button = QPushButton("粘贴并拆分")' in SOURCE
    assert "self.add_urls(_extract_urls(text))" in SOURCE
    assert "self.rows_layout.insertWidget" in SOURCE


def test_batch_editor_reserves_a_real_five_link_working_viewport() -> None:
    assert "_VISIBLE_ROWS = 5" in SOURCE
    assert "_ROW_HEIGHT = 40" in SOURCE
    assert "self.setMinimumHeight(_EDITOR_MIN_HEIGHT)" in SOURCE
    assert "self.scroll.setFixedHeight(_LIST_HEIGHT)" in SOURCE
    assert 'url_head = QLabel("SUPPLIER PRODUCT URL")' in SOURCE
    assert "if len(self.rows) > _VISIBLE_ROWS:" in SOURCE
    assert "self.scroll.ensureWidgetVisible(row" in SOURCE


def test_disabled_rows_are_excluded_from_legacy_batch_prepare_input() -> None:
    assert "def toPlainText(self) -> str:" in SOURCE
    assert "if row.is_enabled() and row.url()" in SOURCE
    # Existing Batch preparation logic remains untouched and still normalizes the
    # compatibility text. The editor decides which rows are exposed to it.
    assert "urls = normalize_batch_urls(self.url_input.toPlainText())" in BATCH


def test_running_batch_locks_all_per_link_controls() -> None:
    assert "def setReadOnly(self, read_only: bool)" in SOURCE
    assert "self.set_locked(bool(read_only))" in SOURCE
    assert "self.toggle.setEnabled(not locked)" in SOURCE
    assert "self.remove_button.setEnabled(not locked)" in SOURCE
    assert "self.paste_button.setEnabled(not self.locked)" in SOURCE
    assert "self.add_button.setEnabled(not self.locked)" in SOURCE


def test_formal_launcher_installs_editor_after_batch_workspace_exists() -> None:
    assert "from gui.batch_url_editor import install_batch_url_editor" in RUN
    assert "window.install_mode_workspace()" in RUN
    assert "install_batch_url_editor(window.batch_workspace)" in RUN
    assert RUN.index("window.install_mode_workspace()") < RUN.index(
        "install_batch_url_editor(window.batch_workspace)"
    )


def test_batch_url_editor_source_compiles_without_importing_pyside() -> None:
    compile(SOURCE, str(ROOT / "gui" / "batch_url_editor.py"), "exec")
