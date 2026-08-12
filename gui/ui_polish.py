from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QBoxLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLayout,
    QMainWindow,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)


# Presentation/layout only. Business runners, permission gates, Save/image policy
# and the Send-to-QC lock remain owned by the existing GUI implementation.
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
    font-size: 27px;
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
    min-height: 30px;
    padding: 0 12px;
    color: rgba(255,255,255,225);
    background-color: rgba(0,0,0,72);
    border: 1px solid rgba(255,255,255,20);
    border-radius: 7px;
    font-size: 11px;
    font-weight: 650;
}

QLineEdit,
QSpinBox,
QComboBox {
    min-height: 35px;
    padding: 0 11px;
    color: #ffffff;
    background-color: rgba(0,0,0,72);
    border: 1px solid rgba(255,255,255,26);
    border-radius: 7px;
    selection-background-color: rgba(255,255,255,58);
    selection-color: #ffffff;
}

QLineEdit:hover,
QSpinBox:hover,
QComboBox:hover {
    background-color: rgba(0,0,0,86);
    border-color: rgba(255,255,255,44);
}

QLineEdit:focus,
QSpinBox:focus,
QComboBox:focus {
    background-color: rgba(0,0,0,96);
    border-color: rgba(255,255,255,90);
}

QComboBox {
    padding-right: 30px;
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
    background-color: rgb(58,58,61);
    border: 1px solid rgba(255,255,255,28);
    border-radius: 7px;
    outline: 0;
    selection-background-color: rgb(84,84,88);
    selection-color: #ffffff;
    padding: 5px;
}

QPushButton {
    min-height: 35px;
    padding: 0 14px;
    border-radius: 7px;
    background-color: rgba(0,0,0,68);
    border: 1px solid rgba(255,255,255,16);
    font-weight: 650;
}

QPushButton:hover {
    background-color: rgba(0,0,0,98);
    border-color: rgba(255,255,255,32);
}

QPushButton:pressed {
    background-color: rgba(0,0,0,78);
}

QPushButton#primaryButton {
    min-width: 128px;
    background-color: rgba(255,255,255,48);
    border-color: rgba(255,255,255,32);
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
    background-color: rgba(0,0,0,72);
    border: 1px solid rgba(255,255,255,62);
    border-radius: 4px;
}

QCheckBox::indicator:checked {
    background-color: rgba(255,255,255,118);
    border-color: rgba(255,255,255,188);
}

QTableWidget {
    color: rgba(255,255,255,232);
    background-color: rgba(0,0,0,58);
    alternate-background-color: rgba(255,255,255,9);
    border: 1px solid rgba(255,255,255,16);
    border-radius: 8px;
    gridline-color: transparent;
    selection-background-color: rgba(255,255,255,38);
    selection-color: #ffffff;
    outline: 0;
}

QTableWidget::item {
    padding: 8px 10px;
    border-bottom: 1px solid rgba(255,255,255,10);
}

QTableWidget::item:selected {
    background-color: rgba(255,255,255,38);
}

QHeaderView::section {
    min-height: 39px;
    padding: 0 10px;
    color: rgba(255,255,255,220);
    background-color: rgba(255,255,255,28);
    border: 0;
    border-bottom: 1px solid rgba(255,255,255,20);
    font-size: 11px;
    font-weight: 700;
}

QPlainTextEdit {
    color: rgba(255,255,255,224);
    background-color: rgba(0,0,0,74);
    border: 1px solid rgba(255,255,255,14);
    border-radius: 8px;
    padding: 9px 10px;
    selection-background-color: rgba(255,255,255,48);
    font-family: "Cascadia Mono", "Consolas", monospace;
    font-size: 11px;
}

QTabWidget#sideDetailTabs::pane {
    border: 0;
    background: transparent;
    top: -1px;
}

QTabWidget#sideDetailTabs QTabBar::tab {
    min-height: 28px;
    padding: 0 13px;
    margin-right: 4px;
    color: rgba(255,255,255,158);
    background: rgba(0,0,0,34);
    border: 1px solid rgba(255,255,255,12);
    border-radius: 7px;
    font-size: 10px;
    font-weight: 650;
}

QTabWidget#sideDetailTabs QTabBar::tab:selected {
    color: #ffffff;
    background: rgba(255,255,255,36);
    border-color: rgba(255,255,255,22);
}

QTabWidget#sideDetailTabs QTabBar::tab:hover {
    color: #ffffff;
    background: rgba(255,255,255,24);
}

QSplitter#workspaceSplitter::handle,
QSplitter#bodySplitter::handle {
    background: transparent;
}

QSplitter#workspaceSplitter::handle:horizontal {
    width: 10px;
}

QSplitter#bodySplitter::handle:vertical {
    height: 9px;
}

QScrollBar:vertical {
    width: 7px;
    margin: 2px 1px;
    background: transparent;
}

QScrollBar::handle:vertical {
    min-height: 28px;
    border-radius: 3px;
    background: rgba(255,255,255,100);
}

QScrollBar::handle:vertical:hover {
    background: rgba(255,255,255,150);
}

QScrollBar:horizontal {
    height: 7px;
    margin: 1px 2px;
    background: transparent;
}

QScrollBar::handle:horizontal {
    min-width: 28px;
    border-radius: 3px;
    background: rgba(255,255,255,100);
}
"""


_CONSOLE_POLISH_STYLE = r"""
QFrame#acceptanceConsole {
    background: transparent;
    border: 0;
}

QFrame#consolePhaseUnit {
    background-color: rgba(0,0,0,48);
    border: 1px solid rgba(255,255,255,14);
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
    font-size: 14px;
    font-weight: 720;
}

QLabel#consoleHint {
    color: rgba(255,255,255,152);
    font-size: 10px;
}

QProgressBar {
    min-height: 17px;
    max-height: 17px;
    border: 1px solid rgba(255,255,255,12);
    border-radius: 6px;
    background-color: rgba(0,0,0,58);
    color: rgba(255,255,255,220);
    text-align: center;
    font-size: 9px;
    font-weight: 700;
}

QProgressBar::chunk {
    border-radius: 5px;
    background-color: rgba(255,255,255,110);
}

QTabWidget::pane {
    border: 1px solid rgba(255,255,255,14);
    border-radius: 8px;
    background-color: rgba(0,0,0,40);
    top: -1px;
}

QTabBar::tab {
    color: rgba(255,255,255,150);
    background: rgba(0,0,0,34);
    border: 0;
    padding: 7px 13px;
    margin-right: 3px;
    border-top-left-radius: 7px;
    border-top-right-radius: 7px;
    font-size: 10px;
    font-weight: 650;
}

QTabBar::tab:selected {
    color: #ffffff;
    background: rgba(0,0,0,72);
}

QTabBar::tab:hover {
    color: #ffffff;
    background: rgba(0,0,0,54);
}

QTableWidget#consoleTable {
    background-color: rgba(0,0,0,62);
    alternate-background-color: rgba(255,255,255,8);
    border: 0;
    border-radius: 6px;
}

QTableWidget#consoleTable QHeaderView::section {
    background-color: rgba(255,255,255,26);
}

QPlainTextEdit#consoleText {
    background-color: rgba(0,0,0,76);
    border: 0;
    border-radius: 6px;
    padding: 9px;
    font-size: 11px;
}
"""


def _set_box_layout(layout: QLayout | None, *, margins: tuple[int, int, int, int], spacing: int) -> None:
    if not isinstance(layout, QBoxLayout):
        return
    layout.setContentsMargins(*margins)
    layout.setSpacing(spacing)


def _set_layout_widgets_visible(layout: QLayout | None, visible: bool) -> None:
    if layout is None:
        return
    for index in range(layout.count()):
        item = layout.itemAt(index)
        widget = item.widget()
        if widget is not None:
            widget.setVisible(visible)
        child_layout = item.layout()
        if child_layout is not None:
            _set_layout_widgets_visible(child_layout, visible)


def _schedule_glass(window: QMainWindow) -> None:
    visual = getattr(window, "_visual_style", None)
    background = getattr(visual, "background", None)
    if background is not None and hasattr(background, "schedule_mask_update"):
        background.schedule_mask_update()


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
    vertical.setDefaultSectionSize(40)
    vertical.setMinimumSectionSize(38)

    header = table.horizontalHeader()
    header.setMinimumSectionSize(88)
    header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    header.setHighlightSections(False)
    header.setSectionsClickable(False)
    header.setStretchLastSection(False)
    header.setMinimumHeight(39)


def _polish_field_table(table: QTableWidget) -> None:
    _configure_data_table(table, minimum_height=220)
    header = table.horizontalHeader()
    columns = table.columnCount()
    if columns >= 7:
        for index in range(columns):
            header.setSectionResizeMode(index, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        widths = {0: 168, 1: 112, 3: 120, 5: 176, 6: 140}
        for column, width in widths.items():
            table.setColumnWidth(column, width)
    elif columns >= 5:
        for index in range(columns):
            header.setSectionResizeMode(index, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        table.setColumnWidth(0, 172)
        table.setColumnWidth(2, 122)


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


def _reflow_real_execution_controls(window: QMainWindow, input_card: QFrame) -> QBoxLayout | None:
    required = ("real_start_button", "real_stop_button", "real_policy_hint", "real_scope_combo")
    if not all(hasattr(window, name) for name in required):
        return None

    layout = input_card.layout()
    if not isinstance(layout, QVBoxLayout):
        return None

    controls: QBoxLayout | None = None
    for index in range(layout.count()):
        candidate = layout.itemAt(index).layout()
        if not isinstance(candidate, QBoxLayout):
            continue
        for child_index in range(candidate.count()):
            if candidate.itemAt(child_index).widget() is window.real_scope_combo:
                controls = candidate
                break
        if controls is not None:
            break
    if controls is None:
        return None

    if not getattr(window, "_ui_polish_real_reflow", False):
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
        setattr(window, "_ui_polish_real_reflow", True)

    window.real_scope_combo.setMinimumWidth(280)
    window.real_scope_combo.setMaximumWidth(360)
    window.real_start_button.setMinimumWidth(126)
    window.real_stop_button.setMinimumWidth(110)
    return controls


def _install_real_execution_collapse(window: QMainWindow, input_card: QFrame) -> None:
    if getattr(window, "_ui_polish_real_collapse", False):
        return
    controls = _reflow_real_execution_controls(window, input_card)
    layout = input_card.layout()
    if controls is None or not isinstance(layout, QVBoxLayout):
        return

    controls_index = -1
    for index in range(layout.count()):
        if layout.itemAt(index).layout() is controls:
            controls_index = index
            break
    if controls_index < 0:
        return

    heading_layout = layout.itemAt(controls_index - 1).layout() if controls_index > 0 else None
    _set_layout_widgets_visible(heading_layout, False)

    summary_row = QHBoxLayout()
    summary_row.setSpacing(10)
    summary_title = QLabel("真实网页填写验收")
    summary_title.setObjectName("cardTitle")
    summary_hint = QLabel("read-only 完成后解锁 · Save / 图片显式授权 · Send to QC 锁定")
    summary_hint.setObjectName("cardHint")
    summary_row.addWidget(summary_title)
    summary_row.addWidget(summary_hint, 1)

    toggle = QPushButton("展开设置")
    toggle.setObjectName("quietButton")
    toggle.setCheckable(True)
    toggle.setMinimumWidth(92)
    summary_row.addWidget(toggle)
    layout.insertLayout(max(0, controls_index - 1), summary_row)

    detail_widgets = [
        getattr(window, "real_scope_combo", None),
        getattr(window, "real_save_check", None),
        getattr(window, "real_upload_check", None),
        getattr(window, "real_pick_images_button", None),
        getattr(window, "real_image_count", None),
        getattr(window, "real_qc_check", None),
        getattr(window, "real_policy_hint", None),
        getattr(window, "real_start_button", None),
        getattr(window, "real_stop_button", None),
    ]

    def apply(expanded: bool) -> None:
        for widget in detail_widgets:
            if isinstance(widget, QWidget):
                widget.setVisible(expanded)
        toggle.setText("收起设置" if expanded else "展开设置")
        QTimer.singleShot(0, lambda: _schedule_glass(window))

    toggle.toggled.connect(apply)
    apply(False)
    setattr(window, "real_settings_toggle", toggle)
    setattr(window, "_ui_polish_real_collapse", True)


def _tabify_side_panel(window: QMainWindow, workspace_splitter: QSplitter) -> None:
    if getattr(window, "_ui_polish_side_tabs", False) or workspace_splitter.count() < 2:
        return

    runtime_anchor = getattr(window, "cold_label", None)
    web_table = getattr(window, "web_table", None)
    safety_anchor = getattr(window, "write_value", None)
    runtime_card = runtime_anchor.parentWidget() if isinstance(runtime_anchor, QWidget) else None
    web_card = web_table.parentWidget() if isinstance(web_table, QWidget) else None
    safety_card = safety_anchor.parentWidget() if isinstance(safety_anchor, QWidget) else None
    if not all(isinstance(card, QFrame) for card in (runtime_card, web_card, safety_card)):
        return

    side = workspace_splitter.widget(1)
    host = side.widget() if hasattr(side, "widget") else None
    side_layout = host.layout() if isinstance(host, QWidget) else None
    if not isinstance(side_layout, QVBoxLayout):
        return

    for card in (runtime_card, web_card, safety_card):
        side_layout.removeWidget(card)
    while side_layout.count():
        side_layout.takeAt(0)

    # This lives inside the existing SmoothScrollArea. Keep clearance as scroll
    # content padding rather than fixing the Telemetry card height, so long
    # diagnostics can still grow naturally and remain reachable by scrolling.
    side_layout.setContentsMargins(0, 0, 0, 28)

    tabs = QTabWidget(host)
    tabs.setObjectName("sideDetailTabs")
    tabs.setDocumentMode(True)
    tabs.addTab(runtime_card, "Telemetry")
    tabs.addTab(web_card, "Web")
    tabs.addTab(safety_card, "Safety")
    tabs.setMinimumHeight(220)
    side_layout.addWidget(tabs, 1)
    tabs.currentChanged.connect(lambda *_: QTimer.singleShot(0, lambda: _schedule_glass(window)))

    setattr(window, "side_detail_tabs", tabs)
    setattr(window, "_ui_polish_side_tabs", True)


def _install_console_collapse(window: QMainWindow, console: QFrame, body: QSplitter) -> None:
    if getattr(window, "_ui_polish_console_collapse", False):
        return
    layout = console.layout()
    tabs = getattr(console, "tabs", None)
    phase_units = list(getattr(console, "phase_units", {}).values())
    if not isinstance(layout, QVBoxLayout) or not isinstance(tabs, QTabWidget):
        return

    header = layout.itemAt(0).layout() if layout.count() else None
    if not isinstance(header, QBoxLayout):
        return

    toggle = QPushButton("展开详情")
    toggle.setObjectName("quietButton")
    toggle.setCheckable(True)
    toggle.setMinimumWidth(90)
    header.addSpacing(6)
    header.addWidget(toggle)

    def apply(expanded: bool) -> None:
        for unit in phase_units:
            if isinstance(unit, QWidget):
                unit.setVisible(expanded)
        tabs.setVisible(expanded)
        toggle.setText("收起详情" if expanded else "展开详情")

        if expanded:
            console.setMinimumHeight(230)
            console.setMaximumHeight(620)
            available = max(0, body.height() - body.handleWidth())
            target = min(420, max(240, available - 260))
            body.setSizes([max(260, available - target), target])
        else:
            console.setMinimumHeight(112)
            console.setMaximumHeight(132)
            body.setSizes([max(320, body.height() - 122), 122])
        QTimer.singleShot(0, lambda: _schedule_glass(window))

    toggle.toggled.connect(apply)
    apply(False)
    setattr(window, "console_detail_toggle", toggle)
    setattr(window, "_ui_polish_console_collapse", True)


def install_ui_polish(window: QMainWindow) -> None:
    """Apply a responsive desktop layout without changing business behavior."""

    root = window.centralWidget()
    if root is None:
        return
    outer = root.layout()
    if not isinstance(outer, QVBoxLayout):
        return

    outer.setContentsMargins(18, 14, 18, 16)
    outer.setSpacing(10)

    header_layout = outer.itemAt(0).layout() if outer.count() > 0 else None
    if isinstance(header_layout, QBoxLayout):
        header_layout.setSpacing(14)

    input_card = outer.itemAt(1).widget() if outer.count() > 1 else None
    if isinstance(input_card, QFrame):
        _set_box_layout(input_card.layout(), margins=(18, 11, 18, 12), spacing=6)
        input_card.setMinimumHeight(0)
        _install_real_execution_collapse(window, input_card)

    status_layout = outer.itemAt(2).layout() if outer.count() > 2 else None
    if isinstance(status_layout, QBoxLayout):
        status_layout.setSpacing(10)
    for name in ("ready_card", "missing_card", "conflict_card", "blocked_card"):
        card = getattr(window, name, None)
        if isinstance(card, QFrame):
            card.setMinimumHeight(78)
            card.setMaximumHeight(84)
            _set_box_layout(card.layout(), margins=(16, 8, 16, 8), spacing=1)

    body: QSplitter | None = getattr(window, "_ui_polish_body_splitter", None)
    workspace: QWidget | None = None
    if outer.count() >= 5 and body is None:
        workspace_candidate = outer.itemAt(3).widget()
        console_candidate = outer.itemAt(4).widget()
        if isinstance(workspace_candidate, QWidget) and isinstance(console_candidate, QWidget):
            workspace = workspace_candidate
            console_widget = console_candidate
            outer.removeWidget(workspace)
            outer.removeWidget(console_widget)

            body = QSplitter(Qt.Orientation.Vertical, root)
            body.setObjectName("bodySplitter")
            body.setChildrenCollapsible(False)
            body.setHandleWidth(9)
            workspace.setMinimumHeight(260)
            body.addWidget(workspace)
            body.addWidget(console_widget)
            body.setStretchFactor(0, 1)
            body.setStretchFactor(1, 0)
            body.setSizes([650, 122])
            outer.addWidget(body, 1)
            setattr(window, "_ui_polish_body_splitter", body)
    elif isinstance(body, QSplitter) and body.count():
        workspace = body.widget(0)

    workspace_splitter = workspace.findChild(QSplitter) if isinstance(workspace, QWidget) else None
    if isinstance(workspace_splitter, QSplitter):
        workspace_splitter.setObjectName("workspaceSplitter")
        workspace_splitter.setHandleWidth(10)
        workspace_splitter.setStretchFactor(0, 1)
        workspace_splitter.setStretchFactor(1, 0)
        if workspace_splitter.count() > 1:
            side = workspace_splitter.widget(1)
            side.setMinimumWidth(320)
            side.setMaximumWidth(410)
        workspace_splitter.setSizes([1220, 350])
        _tabify_side_panel(window, workspace_splitter)

    field_table = getattr(window, "field_table", None)
    if isinstance(field_table, QTableWidget):
        _polish_field_table(field_table)
        field_card = field_table.parentWidget()
        if isinstance(field_card, QFrame):
            _compact_field_header(window, field_card)
            _set_box_layout(field_card.layout(), margins=(18, 12, 18, 15), spacing=8)

    web_table = getattr(window, "web_table", None)
    if isinstance(web_table, QTableWidget):
        _configure_data_table(web_table, minimum_height=170)
        header = web_table.horizontalHeader()
        if web_table.columnCount() >= 3:
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
            header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
            web_table.setColumnWidth(0, 96)
        web_card = web_table.parentWidget()
        if isinstance(web_card, QFrame):
            _set_box_layout(web_card.layout(), margins=(15, 12, 15, 14), spacing=7)

    for anchor_name in ("cold_label", "write_value"):
        anchor = getattr(window, anchor_name, None)
        card = anchor.parentWidget() if isinstance(anchor, QWidget) else None
        if isinstance(card, QFrame):
            _set_box_layout(card.layout(), margins=(15, 12, 15, 14), spacing=5)

    console = getattr(window, "console", None)
    if isinstance(console, QFrame):
        _set_box_layout(console.layout(), margins=(16, 10, 16, 11), spacing=7)
        console.setStyleSheet(console.styleSheet() + "\n" + _CONSOLE_POLISH_STYLE)
        tabs = getattr(console, "tabs", None)
        if isinstance(tabs, QWidget):
            tabs.setMinimumHeight(105)
        for table in console.findChildren(QTableWidget):
            _configure_data_table(table)
        if isinstance(body, QSplitter):
            _install_console_collapse(window, console, body)

    window.setStyleSheet(window.styleSheet() + "\n" + _POLISH_STYLE)

    if isinstance(body, QSplitter):
        body.splitterMoved.connect(lambda *_: _schedule_glass(window))
    if isinstance(workspace_splitter, QSplitter):
        workspace_splitter.splitterMoved.connect(lambda *_: _schedule_glass(window))

    QTimer.singleShot(0, lambda: _schedule_glass(window))
    setattr(window, "_ui_polish_installed", True)
