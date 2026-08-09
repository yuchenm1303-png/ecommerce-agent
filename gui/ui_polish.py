from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QBoxLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLayout,
    QMainWindow,
    QSplitter,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)


# Final presentation pass for the acceptance console. This module is layout and
# presentation only; the existing runners, permission gates and execution widgets
# remain the authoritative business implementation.
_POLISH_STYLE = r"""
QWidget#root {
    font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
    font-size: 13px;
}

QLabel#brandMark {
    color: rgba(255,255,255,156);
    font-size: 10px;
    font-weight: 650;
    letter-spacing: 1px;
}

QLabel#appTitle {
    color: #ffffff;
    font-size: 28px;
    font-weight: 720;
}

QLabel#subtle {
    color: rgba(255,255,255,158);
    font-size: 11px;
}

QLabel#sectionEyebrow {
    color: rgba(255,255,255,132);
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 1px;
}

QLabel#cardTitle {
    color: #ffffff;
    font-size: 15px;
    font-weight: 720;
}

QLabel#cardHint {
    color: rgba(255,255,255,162);
    font-size: 11px;
}

QLabel#phaseBadge {
    min-height: 32px;
    padding: 0 13px;
    color: rgba(255,255,255,225);
    background-color: rgba(4,10,18,118);
    border: 1px solid rgba(255,255,255,22);
    border-radius: 7px;
    font-size: 11px;
    font-weight: 650;
}

QLineEdit,
QSpinBox,
QComboBox {
    min-height: 36px;
    padding: 0 11px;
    color: #ffffff;
    background-color: rgba(4,10,18,132);
    border: 1px solid rgba(255,255,255,28);
    border-radius: 7px;
    selection-background-color: rgba(255,255,255,58);
    selection-color: #ffffff;
}

QLineEdit:hover,
QSpinBox:hover,
QComboBox:hover {
    background-color: rgba(5,12,21,154);
    border-color: rgba(255,255,255,46);
}

QLineEdit:focus,
QSpinBox:focus,
QComboBox:focus {
    background-color: rgba(5,12,21,176);
    border-color: rgba(255,255,255,92);
}

QComboBox {
    padding-right: 32px;
}

QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 28px;
    border: 0;
    background: transparent;
}

QComboBox QAbstractItemView {
    color: #ffffff;
    background-color: rgb(15,22,31);
    border: 1px solid rgba(255,255,255,28);
    border-radius: 7px;
    outline: 0;
    selection-background-color: rgb(54,64,76);
    selection-color: #ffffff;
    padding: 5px;
}

QPushButton {
    min-height: 36px;
    padding: 0 15px;
    border-radius: 7px;
    background-color: rgba(4,10,18,126);
    border: 1px solid rgba(255,255,255,18);
    font-weight: 650;
}

QPushButton:hover {
    background-color: rgba(18,27,38,166);
    border-color: rgba(255,255,255,34);
}

QPushButton:pressed {
    background-color: rgba(3,8,15,180);
}

QPushButton#primaryButton {
    min-width: 132px;
    background-color: rgba(255,255,255,48);
    border-color: rgba(255,255,255,34);
    font-weight: 720;
}

QPushButton#primaryButton:hover {
    background-color: rgba(255,255,255,70);
}

QCheckBox {
    spacing: 7px;
    color: rgba(255,255,255,205);
    font-size: 11px;
}

QCheckBox::indicator {
    width: 15px;
    height: 15px;
    background-color: rgba(4,10,18,136);
    border: 1px solid rgba(255,255,255,64);
    border-radius: 4px;
}

QCheckBox::indicator:checked {
    background-color: rgba(255,255,255,120);
    border-color: rgba(255,255,255,190);
}

QTableWidget {
    color: rgba(255,255,255,232);
    background-color: rgba(3,8,14,158);
    alternate-background-color: rgba(255,255,255,8);
    border: 1px solid rgba(255,255,255,18);
    border-radius: 8px;
    gridline-color: transparent;
    selection-background-color: rgba(255,255,255,38);
    selection-color: #ffffff;
    outline: 0;
}

QTableWidget::item {
    padding: 9px 11px;
    border-bottom: 1px solid rgba(255,255,255,10);
}

QTableWidget::item:selected {
    background-color: rgba(255,255,255,38);
}

QHeaderView::section {
    min-height: 42px;
    padding: 0 11px;
    color: rgba(255,255,255,220);
    background-color: rgba(13,21,31,212);
    border: 0;
    border-bottom: 1px solid rgba(255,255,255,22);
    font-size: 11px;
    font-weight: 700;
}

QPlainTextEdit {
    color: rgba(255,255,255,224);
    background-color: rgba(2,7,13,174);
    border: 1px solid rgba(255,255,255,16);
    border-radius: 8px;
    padding: 10px 11px;
    selection-background-color: rgba(255,255,255,48);
    font-family: "Cascadia Mono", "Consolas", monospace;
    font-size: 11px;
}

QSplitter#workspaceSplitter::handle,
QSplitter#bodySplitter::handle {
    background: transparent;
}

QSplitter#workspaceSplitter::handle:horizontal {
    width: 10px;
}

QSplitter#bodySplitter::handle:vertical {
    height: 10px;
}

QScrollBar:vertical {
    width: 7px;
    margin: 2px 1px;
    background: transparent;
}

QScrollBar::handle:vertical {
    min-height: 28px;
    border-radius: 3px;
    background: rgba(255,255,255,104);
}

QScrollBar::handle:vertical:hover {
    background: rgba(255,255,255,154);
}

QScrollBar:horizontal {
    height: 7px;
    margin: 1px 2px;
    background: transparent;
}

QScrollBar::handle:horizontal {
    min-width: 28px;
    border-radius: 3px;
    background: rgba(255,255,255,104);
}
"""


_CONSOLE_POLISH_STYLE = r"""
QFrame#acceptanceConsole {
    background: transparent;
    border: 0;
}

QFrame#consolePhaseUnit {
    background-color: rgba(3,8,14,128);
    border: 1px solid rgba(255,255,255,16);
    border-radius: 8px;
}

QLabel#consoleEyebrow {
    color: rgba(255,255,255,128);
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 1px;
}

QLabel#consoleTitle {
    color: #ffffff;
    font-size: 15px;
    font-weight: 720;
}

QLabel#consoleHint {
    color: rgba(255,255,255,152);
    font-size: 10px;
}

QProgressBar {
    min-height: 18px;
    max-height: 18px;
    border: 1px solid rgba(255,255,255,14);
    border-radius: 6px;
    background-color: rgba(3,8,14,152);
    color: rgba(255,255,255,220);
    text-align: center;
    font-size: 9px;
    font-weight: 700;
}

QProgressBar::chunk {
    border-radius: 5px;
    background-color: rgba(255,255,255,112);
}

QTabWidget::pane {
    border: 1px solid rgba(255,255,255,16);
    border-radius: 8px;
    background-color: rgba(2,7,13,118);
    top: -1px;
}

QTabBar::tab {
    color: rgba(255,255,255,150);
    background: rgba(2,7,13,92);
    border: 0;
    padding: 8px 14px;
    margin-right: 3px;
    border-top-left-radius: 7px;
    border-top-right-radius: 7px;
    font-size: 10px;
    font-weight: 650;
}

QTabBar::tab:selected {
    color: #ffffff;
    background: rgba(18,27,38,178);
}

QTabBar::tab:hover {
    color: #ffffff;
    background: rgba(18,27,38,138);
}

QTableWidget#consoleTable {
    background-color: rgba(1,5,10,174);
    alternate-background-color: rgba(255,255,255,7);
    border: 0;
    border-radius: 6px;
}

QTableWidget#consoleTable QHeaderView::section {
    background-color: rgba(14,22,32,220);
}

QPlainTextEdit#consoleText {
    background-color: rgba(1,5,10,188);
    border: 0;
    border-radius: 6px;
    padding: 10px;
    font-size: 11px;
}
"""


def _set_box_layout(layout: QLayout | None, *, margins: tuple[int, int, int, int], spacing: int) -> None:
    if not isinstance(layout, QBoxLayout):
        return
    layout.setContentsMargins(*margins)
    layout.setSpacing(spacing)


def _configure_data_table(table: QTableWidget, *, minimum_height: int = 0) -> None:
    table.setShowGrid(False)
    table.setWordWrap(False)
    table.setTextElideMode(Qt.TextElideMode.ElideRight)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    if minimum_height:
        table.setMinimumHeight(minimum_height)

    vertical = table.verticalHeader()
    vertical.setDefaultSectionSize(42)
    vertical.setMinimumSectionSize(40)

    header = table.horizontalHeader()
    header.setMinimumSectionSize(92)
    header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    header.setHighlightSections(False)
    header.setSectionsClickable(False)
    header.setStretchLastSection(False)
    header.setMinimumHeight(42)


def _polish_field_table(table: QTableWidget) -> None:
    _configure_data_table(table, minimum_height=260)
    header = table.horizontalHeader()
    columns = table.columnCount()
    if columns >= 7:
        for index in range(columns):
            header.setSectionResizeMode(index, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        widths = {0: 176, 1: 118, 3: 126, 5: 190, 6: 150}
        for column, width in widths.items():
            table.setColumnWidth(column, width)
    elif columns >= 5:
        for index in range(columns):
            header.setSectionResizeMode(index, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        table.setColumnWidth(0, 180)
        table.setColumnWidth(2, 128)


def _compact_field_header(window: QMainWindow, card: QFrame) -> None:
    layout = card.layout()
    hint = getattr(window, "fields_hint", None)
    if not isinstance(layout, QVBoxLayout) or not isinstance(hint, QLabel):
        return
    if getattr(card, "_ui_polish_header_compact", False):
        return

    direct_labels = [child for child in card.children() if isinstance(child, QLabel)]
    eyebrow = next((label for label in direct_labels if label.objectName() == "sectionEyebrow"), None)
    title = next((label for label in direct_labels if label.objectName() == "cardTitle"), None)
    if eyebrow is None or title is None:
        return

    layout.removeWidget(eyebrow)
    layout.removeWidget(title)
    layout.removeWidget(hint)

    title_box = QVBoxLayout()
    title_box.setSpacing(1)
    title_box.addWidget(eyebrow)
    title_box.addWidget(title)

    header_row = QHBoxLayout()
    header_row.setSpacing(12)
    header_row.addLayout(title_box)
    header_row.addStretch(1)
    header_row.addWidget(hint, 0, Qt.AlignmentFlag.AlignBottom)
    layout.insertLayout(0, header_row)
    setattr(card, "_ui_polish_header_compact", True)


def _reflow_real_execution_controls(window: QMainWindow, input_card: QFrame) -> None:
    required = (
        "real_start_button",
        "real_stop_button",
        "real_policy_hint",
        "real_scope_combo",
    )
    if not all(hasattr(window, name) for name in required):
        return

    layout = input_card.layout()
    if not isinstance(layout, QVBoxLayout) or getattr(window, "_ui_polish_real_reflow", False):
        return

    controls: QBoxLayout | None = None
    for index in range(layout.count()):
        candidate = layout.itemAt(index).layout()
        if not isinstance(candidate, QBoxLayout):
            continue
        for child_index in range(candidate.count()):
            if candidate.itemAt(child_index).widget() is window.real_start_button:
                controls = candidate
                break
        if controls is not None:
            break

    if controls is None:
        return

    controls.removeWidget(window.real_start_button)
    controls.removeWidget(window.real_stop_button)
    layout.removeWidget(window.real_policy_hint)

    action_row = QHBoxLayout()
    action_row.setSpacing(8)
    action_row.addWidget(window.real_policy_hint, 1)
    action_row.addSpacing(10)
    action_row.addWidget(window.real_start_button)
    action_row.addWidget(window.real_stop_button)
    layout.addLayout(action_row)

    window.real_scope_combo.setMinimumWidth(300)
    window.real_scope_combo.setMaximumWidth(380)
    window.real_start_button.setMinimumWidth(132)
    window.real_stop_button.setMinimumWidth(116)
    window.real_policy_hint.setMaximumHeight(34)
    setattr(window, "_ui_polish_real_reflow", True)


def install_ui_polish(window: QMainWindow) -> None:
    """Apply the final desktop layout pass without changing business behavior."""

    root = window.centralWidget()
    if root is None:
        return

    outer = root.layout()
    if not isinstance(outer, QVBoxLayout):
        return

    outer.setContentsMargins(24, 18, 24, 20)
    outer.setSpacing(12)

    header_layout = outer.itemAt(0).layout() if outer.count() > 0 else None
    if isinstance(header_layout, QBoxLayout):
        header_layout.setSpacing(16)

    input_card = outer.itemAt(1).widget() if outer.count() > 1 else None
    if isinstance(input_card, QFrame):
        _set_box_layout(input_card.layout(), margins=(18, 12, 18, 13), spacing=6)
        input_card.setMinimumHeight(0)
        _reflow_real_execution_controls(window, input_card)

    status_layout = outer.itemAt(2).layout() if outer.count() > 2 else None
    if isinstance(status_layout, QBoxLayout):
        status_layout.setSpacing(12)
    for name in ("ready_card", "missing_card", "conflict_card", "blocked_card"):
        card = getattr(window, name, None)
        if isinstance(card, QFrame):
            card.setMinimumHeight(84)
            card.setMaximumHeight(92)
            _set_box_layout(card.layout(), margins=(18, 10, 18, 10), spacing=2)

    if outer.count() >= 5 and not hasattr(window, "_ui_polish_body_splitter"):
        workspace = outer.itemAt(3).widget()
        console = outer.itemAt(4).widget()
        if isinstance(workspace, QWidget) and isinstance(console, QWidget):
            outer.removeWidget(workspace)
            outer.removeWidget(console)
            body = QSplitter(Qt.Orientation.Vertical, root)
            body.setObjectName("bodySplitter")
            body.setChildrenCollapsible(False)
            body.setHandleWidth(10)
            workspace.setMinimumHeight(300)
            console.setMinimumHeight(238)
            console.setMaximumHeight(420)
            body.addWidget(workspace)
            body.addWidget(console)
            body.setStretchFactor(0, 7)
            body.setStretchFactor(1, 3)
            body.setSizes([520, 270])
            outer.addWidget(body, 1)
            setattr(window, "_ui_polish_body_splitter", body)

            workspace_splitter = workspace.findChild(QSplitter)
            if isinstance(workspace_splitter, QSplitter):
                workspace_splitter.setObjectName("workspaceSplitter")
                workspace_splitter.setHandleWidth(10)
                workspace_splitter.setStretchFactor(0, 1)
                workspace_splitter.setStretchFactor(1, 0)
                if workspace_splitter.count() > 1:
                    side = workspace_splitter.widget(1)
                    side.setMinimumWidth(330)
                    side.setMaximumWidth(430)
                workspace_splitter.setSizes([1180, 360])

    field_table = getattr(window, "field_table", None)
    if isinstance(field_table, QTableWidget):
        _polish_field_table(field_table)
        field_card = field_table.parentWidget()
        if isinstance(field_card, QFrame):
            _compact_field_header(window, field_card)
            _set_box_layout(field_card.layout(), margins=(20, 14, 20, 17), spacing=9)

    web_table = getattr(window, "web_table", None)
    if isinstance(web_table, QTableWidget):
        _configure_data_table(web_table, minimum_height=190)
        header = web_table.horizontalHeader()
        if web_table.columnCount() >= 3:
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
            header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
            web_table.setColumnWidth(0, 108)
        web_card = web_table.parentWidget()
        if isinstance(web_card, QFrame):
            _set_box_layout(web_card.layout(), margins=(18, 13, 18, 15), spacing=8)

    for anchor_name in ("cold_label", "write_value"):
        anchor = getattr(window, anchor_name, None)
        card = anchor.parentWidget() if isinstance(anchor, QWidget) else None
        if isinstance(card, QFrame):
            _set_box_layout(card.layout(), margins=(18, 13, 18, 15), spacing=6)

    console = getattr(window, "console", None)
    if isinstance(console, QFrame):
        console.setMinimumHeight(238)
        console.setMaximumHeight(420)
        _set_box_layout(console.layout(), margins=(18, 12, 18, 14), spacing=8)
        console.setStyleSheet(console.styleSheet() + "\n" + _CONSOLE_POLISH_STYLE)
        tabs = getattr(console, "tabs", None)
        if isinstance(tabs, QWidget):
            tabs.setMinimumHeight(132)
        for table in console.findChildren(QTableWidget):
            _configure_data_table(table)

    window.setStyleSheet(window.styleSheet() + "\n" + _POLISH_STYLE)

    visual = getattr(window, "_visual_style", None)
    background = getattr(visual, "background", None)
    if background is not None and hasattr(background, "schedule_mask_update"):
        background.schedule_mask_update()

    setattr(window, "_ui_polish_installed", True)
