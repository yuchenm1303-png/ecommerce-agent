"""Scrollable Single-workspace page layout for the formal GUI.

Presentation only: this module rearranges already-created QWidget objects. It does
not rebuild runners, change permissions, or touch Resolver / Fill Plan / executor
logic.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QBoxLayout,
    QFrame,
    QLayout,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)


_WORKSPACE_HEIGHT = 350
_FIELD_TABLE_MIN_HEIGHT = 255
_SIDE_TABS_HEIGHT = 320
_CONSOLE_MIN_HEIGHT = 420
_CONSOLE_MAX_HEIGHT = 560
_CONSOLE_TABS_MIN_HEIGHT = 250
_CONSOLE_LOG_MIN_HEIGHT = 180


def _ancestor_card(widget: QWidget | None, object_name: str) -> QFrame | None:
    current = widget
    while current is not None:
        if isinstance(current, QFrame) and current.objectName() == object_name:
            return current
        current = current.parentWidget()
    return None


def _contains_widget(layout: QLayout | None, target: QWidget) -> bool:
    if layout is None:
        return False
    for index in range(layout.count()):
        item = layout.itemAt(index)
        if item.widget() is target:
            return True
        child = item.layout()
        if child is not None and _contains_widget(child, target):
            return True
    return False


def _take_widget(layout: QBoxLayout, target: QWidget) -> None:
    for index in range(layout.count()):
        item = layout.itemAt(index)
        if item.widget() is target:
            layout.takeAt(index)
            return


def _take_layout(layout: QBoxLayout, target: QLayout) -> None:
    for index in range(layout.count()):
        item = layout.itemAt(index)
        if item.layout() is target:
            layout.takeAt(index)
            return


def _restore_console_view(window: QMainWindow, console: QWidget) -> None:
    toggle = getattr(window, "console_detail_toggle", None)
    if isinstance(toggle, QPushButton):
        was_blocked = toggle.blockSignals(True)
        try:
            toggle.setChecked(True)
            toggle.setEnabled(True)
            toggle.show()
        finally:
            toggle.blockSignals(was_blocked)

    for unit in getattr(console, "phase_units", {}).values():
        if isinstance(unit, QWidget):
            unit.show()

    tabs = getattr(console, "tabs", None)
    if isinstance(tabs, QTabWidget):
        tabs.show()
        tabs.setMinimumHeight(_CONSOLE_TABS_MIN_HEIGHT)
        tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    log_view = getattr(console, "log_view", None)
    if isinstance(log_view, QWidget):
        log_view.setMinimumHeight(_CONSOLE_LOG_MIN_HEIGHT)

    console.setMinimumHeight(_CONSOLE_MIN_HEIGHT)
    console.setMaximumHeight(_CONSOLE_MAX_HEIGHT)
    console.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)


def install_page_scroll_layout(window: QMainWindow, visual: Any | None = None) -> QScrollArea:
    """Make the preserved Single body one vertically scrollable page.

    The application header remains fixed at the root. Product source, status cards,
    field workspace and acceptance console move into one content-driven page. The
    old body QSplitter stays detached/hidden only long enough for legacy signal
    closures to remain harmless; it no longer controls any visible geometry.

    Glass is still rendered by the native Quick scene. Once the page exists we bind
    this outer scrollbar to one Quick scene-group offset; continuous scrolling does
    not trigger per-card geometry scans or blur-mask rebuilds.
    """

    existing = getattr(window, "_single_page_scroll", None)
    if isinstance(existing, QScrollArea):
        return existing

    root = window.centralWidget()
    outer = root.layout() if root is not None else None
    if root is None or not isinstance(outer, QVBoxLayout):
        raise RuntimeError("page scroll layout requires the preserved root QVBoxLayout")

    url_input = getattr(window, "url_input", None)
    input_card = _ancestor_card(url_input if isinstance(url_input, QWidget) else None, "heroCard")
    ready_card = getattr(window, "ready_card", None)
    body = getattr(window, "_ui_polish_body_splitter", None)
    console = getattr(window, "console", None)

    if not isinstance(input_card, QFrame):
        raise RuntimeError("page scroll layout could not resolve the Product Source card")
    if not isinstance(ready_card, QWidget):
        raise RuntimeError("page scroll layout could not resolve the status row")
    if not isinstance(body, QSplitter) or body.count() < 2:
        raise RuntimeError("page scroll layout requires the polished body splitter")
    if not isinstance(console, QWidget):
        raise RuntimeError("page scroll layout could not resolve the acceptance console")

    status_layout: QLayout | None = None
    for index in range(outer.count()):
        candidate = outer.itemAt(index).layout()
        if candidate is not None and _contains_widget(candidate, ready_card):
            status_layout = candidate
            break
    if status_layout is None:
        raise RuntimeError("page scroll layout could not resolve the status layout")

    workspace = body.widget(0)
    body_console = body.widget(1)
    if workspace is None or body_console is not console:
        raise RuntimeError("page scroll layout found an unexpected polished body structure")

    _take_widget(outer, input_card)
    _take_layout(outer, status_layout)
    _take_widget(outer, body)

    scroll = QScrollArea(root)
    scroll.setObjectName("singlePageScroll")
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    scroll.setStyleSheet(
        "QScrollArea#singlePageScroll { background: transparent; border: 0; }"
        "QScrollArea#singlePageScroll > QWidget > QWidget { background: transparent; }"
    )
    scroll.viewport().setAutoFillBackground(False)
    scroll.viewport().setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    page = QWidget()
    page.setObjectName("singlePageScrollContent")
    page.setAutoFillBackground(False)
    page.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    page.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding)

    page_layout = QVBoxLayout(page)
    page_layout.setContentsMargins(0, 0, 8, 20)
    page_layout.setSpacing(10)
    page_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)

    input_card.setParent(page)
    page_layout.addWidget(input_card)

    status_host = QWidget(page)
    status_host.setObjectName("statusRowHost")
    status_host.setAutoFillBackground(False)
    status_host.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    status_layout.setParent(None)
    status_host.setLayout(status_layout)
    page_layout.addWidget(status_host)

    workspace.setParent(page)
    console.setParent(page)

    workspace.setMinimumHeight(_WORKSPACE_HEIGHT)
    workspace.setMaximumHeight(_WORKSPACE_HEIGHT)
    workspace.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    field_table = getattr(window, "field_table", None)
    if isinstance(field_table, QWidget):
        field_table.setMinimumHeight(_FIELD_TABLE_MIN_HEIGHT)

    side_tabs = getattr(window, "side_detail_tabs", None)
    if isinstance(side_tabs, QTabWidget):
        side_tabs.setMinimumHeight(_SIDE_TABS_HEIGHT)
        side_tabs.setMaximumHeight(_SIDE_TABS_HEIGHT)
        side_tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        side_host = side_tabs.parentWidget()
        side_layout = side_host.layout() if side_host is not None else None
        if isinstance(side_layout, QVBoxLayout):
            margins = side_layout.contentsMargins()
            side_layout.setContentsMargins(margins.left(), margins.top(), margins.right(), 0)

    _restore_console_view(window, console)

    page_layout.addWidget(workspace)
    page_layout.addWidget(console)
    page_layout.addStretch(1)

    scroll.setWidget(page)
    outer.addWidget(scroll, 1)

    body.hide()
    body.setParent(root)
    setattr(window, "_ui_polish_body_splitter", None)

    # Publish the page identity/layout once, then the background owns the O(1)
    # scroll-value -> QML group-transform hot path. This deliberately replaces the
    # old valueChanged -> card_model.sync_geometry() -> mask rebuild chain.
    background = getattr(visual, "background", None)
    if background is None:
        background = getattr(getattr(window, "_visual_style", None), "background", None)
    bind_scroll = getattr(background, "bind_single_page_scroll", None)
    if callable(bind_scroll):
        bind_scroll(scroll, page)

    setattr(window, "_single_page_scroll", scroll)
    setattr(window, "_single_page_scroll_content", page)
    return scroll
