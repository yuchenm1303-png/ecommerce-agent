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
    assert 'self.remove_button = QPushButton("删除")' in SOURCE
    assert "self.input = QLineEdit(url)" in SOURCE


def test_link_row_controls_have_explicit_centered_non_clipping_geometry() -> None:
    assert "_ROW_HEIGHT = 40" in SOURCE
    assert "_CONTROL_HEIGHT = 28" in SOURCE
    assert "layout.setContentsMargins(7, 5, 7, 5)" in SOURCE
    assert "layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)" in SOURCE
    assert "center = Qt.AlignmentFlag.AlignVCenter" in SOURCE
    assert "layout.addWidget(self.index_label, 0, center)" in SOURCE
    assert "layout.addWidget(self.toggle, 0, center)" in SOURCE
    assert "layout.addWidget(self.input, 1, center)" in SOURCE
    assert "layout.addWidget(self.remove_button, 0, center)" in SOURCE
    assert "self.toggle.setFixedSize(58, _CONTROL_HEIGHT)" in SOURCE
    assert "self.input.setFixedHeight(_CONTROL_HEIGHT)" in SOURCE
    assert "self.remove_button.setFixedSize(48, _CONTROL_HEIGHT)" in SOURCE
    assert '"  padding: 0 11px;"' in SOURCE
    assert '"  min-height: 28px; max-height: 28px;"' in SOURCE


def test_delete_control_has_visible_danger_button_treatment() -> None:
    assert 'self.remove_button.setToolTip("删除此链接")' in SOURCE
    assert "QPushButton#batchUrlRemoveButton" in SOURCE
    assert "background: rgba(128,42,58,54)" in SOURCE
    assert "border: 1px solid rgba(255,145,164,48)" in SOURCE
    assert "QPushButton#batchUrlRemoveButton:hover" in SOURCE


def test_multiple_link_boxes_are_always_visible_without_large_source_card() -> None:
    assert "_VISIBLE_ROWS = 4" in SOURCE
    assert "self.setFixedHeight(_EDITOR_HEIGHT)" in SOURCE
    assert "self._ensure_min_rows()" in SOURCE
    assert 'hint = QLabel("每个链接独立任务 · 第 5 条起滚动")' in SOURCE
    assert 'self.add_button = QPushButton("+ 添加链接")' in SOURCE
    assert 'self.paste_button = QPushButton("批量粘贴")' in SOURCE
    assert "quick_input" not in SOURCE
    assert "manage_button" not in SOURCE


def test_multi_paste_fills_existing_empty_link_boxes_before_adding_rows() -> None:
    assert '_URL_RE = re.compile(r"https?://[^\\s]+"' in SOURCE
    assert "self.add_urls(_extract_urls(text))" in SOURCE
    assert "target_rows = [row for row in self.rows if not row.url()]" in SOURCE
    assert "if target_rows:" in SOURCE
    assert "row.input.setText(url)" in SOURCE
    assert "self.rows_layout.insertWidget" in SOURCE


def test_disabled_rows_are_excluded_from_legacy_batch_prepare_input() -> None:
    assert "def toPlainText(self) -> str:" in SOURCE
    assert "if row.is_enabled() and row.url()" in SOURCE
    assert "urls = normalize_batch_urls(self.url_input.toPlainText())" in BATCH


def test_running_batch_locks_rows_without_hiding_the_multi_link_surface() -> None:
    assert "def setReadOnly(self, read_only: bool)" in SOURCE
    assert "self.set_locked(bool(read_only))" in SOURCE
    assert "self.toggle.setEnabled(not locked)" in SOURCE
    assert "self.remove_button.setEnabled(not locked)" in SOURCE
    assert "self.paste_button.setEnabled(not self.locked)" in SOURCE
    assert "self.add_button.setEnabled(not self.locked)" in SOURCE
    assert "set_expanded" not in SOURCE
    assert "drawer.hide" not in SOURCE


def test_clear_and_delete_keep_four_independent_input_slots_available() -> None:
    assert "while len(self.rows) < _VISIBLE_ROWS:" in SOURCE
    assert "self._ensure_min_rows()" in SOURCE
    assert "self.rows.clear()" in SOURCE


def test_batch_density_keeps_detailed_summary_cards_and_expanding_job_surface() -> None:
    assert '"TOTAL · 全部任务"' in DENSITY
    assert '"PROCESSING · 处理中"' in DENSITY
    assert '"READY · 可执行"' in DENSITY
    assert "card.setMinimumWidth(150)" in DENSITY
    assert "card.setMinimumHeight(70)" in DENSITY
    assert "detail = QLabel(detail_text)" in DENSITY
    assert "queue_card.setMinimumHeight(430)" in DENSITY
    assert "root.setStretch(2, 1)" in DENSITY


def test_formal_launcher_installs_batch_density_after_editor() -> None:
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
