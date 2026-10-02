"""The single layout owner for the Listing Studio workspaces.

Presentation only. Every business widget is *moved* into the page structure below,
never reconstructed, so runners, gates, modal proxies, copy layers and telemetry
keep the exact objects they were wired to. The visible Quick scene mirrors this
QWidget geometry, which keeps the glass cards, hover response and modals intact.

Single page::

    ┌ task column ────────────┐ ┌ result column ───────────────────────────────┐
    │ 商品任务                 │ │ 流程进度 · phases · activity · progress       │
    │   source · intent · run │ │ READY · MISSING · CONFLICT · BLOCKED          │
    │ 真实填写 · safety        │ │ 字段检查                      │ Runtime/Ref.  │
    │ 高级 · 阶段诊断          │ │ 运行日志 · Console / Timeline / …             │
    └─────────────────────────┘ └───────────────────────────────────────────────┘

Batch page keeps its top-to-bottom flow (links → overview → tasks → execution)
on the same grid. The common header is ordered into brand · mode · status ·
actions · preferences · version, including controls installed after startup.

This module replaces the former page_scroll_layout / console_summary_mode /
single_top_compact geometry owners and the Single sizing of ui_maturity.
"""

from __future__ import annotations

import sys
from typing import Any

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import (
    QBoxLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QSpinBox,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)


_SIZE_MAX = 16777215

# One grid for the whole application. Card edges, gaps and control heights are
# derived from these tokens only, so adjacent cards always share their baselines.
_PAGE_GAP = 12
_CARD_MARGINS = (20, 16, 20, 16)
_SECTION_GAP = 14
_FIELD_GAP = 10
_LABEL_GAP = 6
_CONTROL_HEIGHT = 34
_PRIMARY_HEIGHT = 42
_COMPACT_CONTROL_HEIGHT = 30
_COMPACT_PRIMARY_HEIGHT = 36

_TASK_RATIO = 0.24
_TASK_MIN = 372
_TASK_MAX = 520
_SIDE_RATIO = 0.25
_SIDE_MIN = 300
_SIDE_MAX = 392
_PROGRESS_UNIT_HEIGHT = 46
_KPI_HEIGHT = 74
_LOG_HEIGHT = 238
_LOG_HEIGHT_COMPACT = 196

_COMPOSER_STYLE = r"""
QLabel#composerFieldLabel {
    color: rgba(255,255,255,196);
    font-size: 11px;
    font-weight: 650;
}
QLabel#composerStatCaption {
    color: rgba(255,255,255,150);
    font-size: 10px;
    font-weight: 650;
}
QLabel#headerModeLabel {
    font-size: 11px;
    font-weight: 720;
}
"""

_MODE_LABEL_ACTIVE = "color: #ffffff;"
_MODE_LABEL_IDLE = "color: rgba(255,255,255,118);"


# ---------------------------------------------------------------------------
# Layout helpers
# ---------------------------------------------------------------------------


def _reset_size(widget: QWidget) -> None:
    widget.setMinimumSize(0, 0)
    widget.setMaximumSize(_SIZE_MAX, _SIZE_MAX)


def _control(widget: Any, *, height: int = _CONTROL_HEIGHT) -> None:
    """Give one interactive control the shared height and a free width."""

    if not isinstance(widget, QWidget):
        return
    _reset_size(widget)
    widget.setFixedHeight(height)
    widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)


def _row_containing(layout: QLayout | None, target: QWidget) -> QBoxLayout | None:
    """Return the innermost box layout that directly holds ``target``."""

    if layout is None:
        return None
    for index in range(layout.count()):
        item = layout.itemAt(index)
        if item.widget() is target and isinstance(layout, QBoxLayout):
            return layout
        child = item.layout()
        if child is not None:
            found = _row_containing(child, target)
            if found is not None:
                return found
    return None


def _widgets_in(layout: QLayout | None) -> list[QWidget]:
    found: list[QWidget] = []
    if layout is None:
        return found
    for index in range(layout.count()):
        item = layout.itemAt(index)
        widget = item.widget()
        if widget is not None:
            found.append(widget)
        child = item.layout()
        if child is not None:
            found.extend(_widgets_in(child))
    return found


def _label_before(row: QBoxLayout | None, target: Any) -> QLabel | None:
    if row is None or not isinstance(target, QWidget):
        return None
    widgets = _widgets_in(row)
    if target not in widgets:
        return None
    for widget in reversed(widgets[: widgets.index(target)]):
        if isinstance(widget, QLabel):
            return widget
    return None


def _drain(layout: QLayout | None) -> list[QWidget]:
    """Empty a layout tree, returning its widgets in visual order.

    Widgets stay parented to their current widget; nested layouts are detached.
    """

    widgets: list[QWidget] = []
    if layout is None:
        return widgets
    while layout.count():
        item = layout.takeAt(0)
        if item is None:
            break
        widget = item.widget()
        if widget is not None:
            widgets.append(widget)
            continue
        child = item.layout()
        if child is not None:
            widgets.extend(_drain(child))
    return widgets


def _hbox(spacing: int = 0) -> QHBoxLayout:
    layout = QHBoxLayout()
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(spacing)
    return layout


def _vbox(spacing: int = 0) -> QVBoxLayout:
    layout = QVBoxLayout()
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(spacing)
    return layout


def _label(text: str, object_name: str, *, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    label.setObjectName(object_name)
    label.setWordWrap(wrap)
    return label


def _restyle(widget: QWidget, object_name: str) -> None:
    """Rename a widget and re-run style-sheet matching for the new selector."""

    widget.setObjectName(object_name)
    widget.setStyleSheet("")
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)


def _field_label(label: QLabel | None) -> QLabel | None:
    if not isinstance(label, QLabel):
        return None
    _reset_size(label)
    _restyle(label, "composerFieldLabel")
    label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
    return label


def _card(object_name: str = "glassCard") -> QFrame:
    card = QFrame()
    card.setObjectName(object_name)
    card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    layout = QVBoxLayout(card)
    layout.setContentsMargins(*_CARD_MARGINS)
    layout.setSpacing(0)
    return card


def _card_layout(card: QFrame) -> QVBoxLayout:
    layout = card.layout()
    if not isinstance(layout, QVBoxLayout):
        raise RuntimeError(f"composer expected a vertical card layout on {card.objectName()}")
    layout.setContentsMargins(*_CARD_MARGINS)
    layout.setSpacing(0)
    return layout


def _title_block(eyebrow: QLabel | None, title: QLabel | None) -> QVBoxLayout:
    block = _vbox(1)
    for label in (eyebrow, title):
        if isinstance(label, QLabel):
            _reset_size(label)
            label.setWordWrap(False)
            label.show()
            block.addWidget(label)
    return block


def _hidden_holder(parent: QWidget, widgets: list[QWidget]) -> QWidget:
    """Keep business widgets that are never presented alive but out of layout."""

    holder = QWidget(parent)
    holder.setObjectName("composerHiddenHolder")
    layout = QVBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    for widget in widgets:
        if isinstance(widget, QWidget):
            layout.addWidget(widget)
    holder.hide()
    return holder


def _visual(window: QMainWindow) -> Any:
    return getattr(window, "_visual_style", None)


def _unregister_glass(window: QMainWindow, frames: list[QFrame]) -> None:
    """Turn formerly independent glass cards into plain content of a parent card."""

    visual = _visual(window)
    glass = getattr(visual, "_glass", None)
    background = getattr(visual, "background", None)
    model = getattr(background, "card_model", None)
    targets = set(frames)

    if isinstance(glass, dict):
        for frame in frames:
            surface = glass.pop(frame, None)
            if surface is not None:
                try:
                    surface.cleanup()
                except RuntimeError:
                    pass

    cards = list(getattr(model, "cards", [])) if model is not None else []
    states = list(getattr(model, "_states", [])) if model is not None else []
    if model is not None and len(cards) == len(states) and any(card in targets for card in cards):
        kept = [(card, state) for card, state in zip(cards, states) if card not in targets]
        model.beginResetModel()
        try:
            model.cards[:] = [card for card, _ in kept]
            model._states[:] = [state for _, state in kept]
            model._rows = {card: row for row, card in enumerate(model.cards)}
        finally:
            model.endResetModel()

    watch = getattr(background, "_geometry_watch", None)
    if isinstance(watch, set):
        for frame in frames:
            if frame in watch:
                try:
                    frame.removeEventFilter(background)
                except RuntimeError:
                    pass
                watch.discard(frame)

    for frame in frames:
        frame.setObjectName("sideDetailPage")
        frame.setGraphicsEffect(None)
        frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        frame.setStyleSheet(
            frame.styleSheet() + "\nQFrame#sideDetailPage { background: transparent; border: 0; }"
        )


def _schedule_glass(window: QMainWindow) -> None:
    visual = _visual(window)
    refresh = getattr(visual, "refresh_glass_frames", None)
    if callable(refresh):
        refresh()
    background = getattr(visual, "background", None)
    schedule = getattr(background, "schedule_mask_update", None)
    if callable(schedule):
        QTimer.singleShot(0, schedule)


def _publish_structure(window: QMainWindow) -> None:
    controller = getattr(window, "_static_qml_view_controller", None)
    bridge = getattr(controller, "bridge", None)
    for name in ("schedule_structure_refresh", "schedule_refresh"):
        method = getattr(bridge, name, None)
        if callable(method):
            try:
                method()
            except RuntimeError:
                pass


def _warn_unplaced(scope: str, widgets: list[QWidget]) -> None:
    if not widgets:
        return
    names = ", ".join(
        f"{type(widget).__name__}#{widget.objectName() or '-'}" for widget in widgets
    )
    print(f"[workspace-composer] {scope}: kept unplaced widgets visible: {names}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Single workspace
# ---------------------------------------------------------------------------


class SingleWorkspaceComposer(QObject):
    """Compose Single into a task column and a result column."""

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self.window = window
        self.page = self._single_page()
        self._compact: bool | None = None
        self._measured_for: tuple[int, int] | None = None
        # Density registry: every spacing and control height in the task column
        # has a roomy and a compact value; roomy-only widgets fold away when short.
        self._gaps: list[tuple[QBoxLayout, QSpacerItem, int, int]] = []
        self._heights: list[tuple[QWidget, int, int]] = []
        self._roomy_only: list[QWidget] = []

        self._ensure_intent_detail()
        self.hero = self._ancestor_card(getattr(window, "url_input", None), "heroCard")
        if not isinstance(self.hero, QFrame):
            raise RuntimeError("Single composer could not resolve the product source card")

        self._compose_task_card()
        self.execution_card = self._compose_execution_card()
        self.advanced_card = self._compose_advanced_card()
        self.side_card = self._compose_side_card()
        self.log_card = self._compose_log_card()
        self._compose_progress_card()
        self._compose_kpis()
        self._compose_field_card()
        self._assemble_page()

        self.page.installEventFilter(self)
        window.destroyed.connect(self.cleanup)
        self.apply()

    # -- discovery -----------------------------------------------------------

    def _single_page(self) -> QWidget:
        stack = getattr(self.window, "mode_stack", None)
        if not isinstance(stack, QStackedWidget) or stack.count() < 1:
            raise RuntimeError("Single composer requires the installed modeStack")
        page = stack.widget(0)
        if not isinstance(page, QWidget) or not isinstance(page.layout(), QVBoxLayout):
            raise RuntimeError("Single composer requires the Single page layout")
        return page

    @staticmethod
    def _ancestor_card(widget: Any, object_name: str) -> QFrame | None:
        current = widget if isinstance(widget, QWidget) else None
        while current is not None:
            if isinstance(current, QFrame) and current.objectName() == object_name:
                return current
            current = current.parentWidget()
        return None

    def _ensure_intent_detail(self) -> None:
        if not isinstance(getattr(self.window, "listing_intent_input", None), QWidget):
            return
        from .listing_intent_detail import install_listing_intent_detail

        install_listing_intent_detail(self.window, on_expanded=self._intent_detail_toggled)

    def _intent_detail_toggled(self, _expanded: bool) -> None:
        self._measured_for = None
        self.apply()
        self.hero.updateGeometry()
        _schedule_glass(self.window)
        _publish_structure(self.window)

    # -- density -------------------------------------------------------------

    def _gap(self, layout: QBoxLayout, roomy: int, compact: int) -> None:
        layout.addSpacing(roomy)
        item = layout.itemAt(layout.count() - 1)
        spacer = item.spacerItem() if item is not None else None
        if spacer is not None:
            self._gaps.append((layout, spacer, roomy, compact))

    def _height(self, widget: Any, roomy: int, compact: int) -> None:
        if isinstance(widget, QWidget):
            widget.setFixedHeight(roomy)
            self._heights.append((widget, roomy, compact))

    def _control(
        self,
        widget: Any,
        *,
        roomy: int = _CONTROL_HEIGHT,
        compact: int = _COMPACT_CONTROL_HEIGHT,
    ) -> None:
        _control(widget, height=roomy)
        self._height(widget, roomy, compact)

    def _task_minimum_height(self, task_width: int) -> int:
        """Exact roomy height of the task column at ``task_width``.

        Measured from each card's own layout rather than the column's cached item
        sizes, and with word-wrapped paragraphs measured at their real width.
        """

        inner_width = max(1, task_width - _CARD_MARGINS[0] - _CARD_MARGINS[2])
        cards = (self.hero, self.execution_card, self.advanced_card)
        total = 0
        for card in cards:
            layout = card.layout()
            layout.invalidate()
            total += int(layout.totalMinimumSize().height())
            for label in card.findChildren(QLabel):
                if label.wordWrap() and not label.isHidden() and label.parentWidget() is card:
                    total += max(0, label.heightForWidth(inner_width) - label.minimumSizeHint().height())
        return total + self.task_column.layout().spacing() * (len(cards) - 1)

    def _set_density(self, compact: bool) -> None:
        for layout, spacer, roomy, small in self._gaps:
            spacer.changeSize(
                0,
                small if compact else roomy,
                QSizePolicy.Policy.Minimum,
                QSizePolicy.Policy.Fixed,
            )
            layout.invalidate()
        for widget, roomy, small in self._heights:
            widget.setFixedHeight(small if compact else roomy)
        for widget in self._roomy_only:
            widget.setVisible(not compact)
        # The folded source paragraph stays one hover away on the product card.
        hint = getattr(self.window, "product_input_hint", None)
        self.hero.setToolTip(hint.text() if compact and isinstance(hint, QLabel) else "")
        self.log_card.setFixedHeight(_LOG_HEIGHT_COMPACT if compact else _LOG_HEIGHT)
        self._compact = compact

    # -- task column -----------------------------------------------------------

    def _compose_task_card(self) -> None:
        window = self.window
        card = self.hero
        layout = _card_layout(card)

        url_input = window.url_input
        hint = getattr(window, "product_input_hint", None)
        header_row = _row_containing(layout, hint) if isinstance(hint, QWidget) else None
        header_labels = [w for w in _widgets_in(header_row) if isinstance(w, QLabel) and w is not hint]
        eyebrow = next((w for w in header_labels if w.objectName() == "sectionEyebrow"), None)
        title = next((w for w in header_labels if w.objectName() == "cardTitle"), None)

        offer_input = getattr(window, "listing_intent_input", None)
        offer_row = _row_containing(layout, offer_input) if isinstance(offer_input, QWidget) else None
        offer_label = _label_before(offer_row, offer_input)

        guidance_input = getattr(window, "ai_guidance_input", None)
        keywords_input = getattr(window, "model_name_keywords_input", None)
        guidance_row = (
            _row_containing(layout, guidance_input) if isinstance(guidance_input, QWidget) else None
        )
        guidance_label = _label_before(guidance_row, guidance_input)
        keywords_label = _label_before(guidance_row, keywords_input)

        toggle = getattr(window, "real_settings_toggle", None)
        summary_row = _row_containing(layout, toggle) if isinstance(toggle, QWidget) else None
        summary_labels = [w for w in _widgets_in(summary_row) if isinstance(w, QLabel)]
        self._summary_title = next((w for w in summary_labels if w.objectName() == "cardTitle"), None)
        self._summary_hint = next((w for w in summary_labels if w.objectName() == "cardHint"), None)

        photo_button = getattr(getattr(window, "_listing_photo_ownership", None), "single_photo_button", None)
        if not isinstance(photo_button, QPushButton):
            url_row = _row_containing(layout, url_input)
            photo_button = next(
                (
                    w
                    for w in _widgets_in(url_row)
                    if isinstance(w, QPushButton) and w.text().startswith("商品图片")
                ),
                None,
            )

        pause = getattr(window, "_cooperative_pause", None)
        pause_button = getattr(pause, "pause_button", None)
        pause_hint = getattr(pause, "pause_hint", None)
        browser_label = getattr(getattr(window, "_managed_makro_browser", None), "_single_label", None)
        detail_button = getattr(window, "listing_intent_detail_button", None)
        detail_host = getattr(window, "listing_intent_detail_host", None)
        vertical_input = getattr(window, "vertical_input", None)

        # Everything that leaves the product card: execution summary, diagnostics,
        # the never-presented real-execution controls and their legacy headings.
        self._real_controls = [
            getattr(window, name, None)
            for name in (
                "real_scope_combo",
                "real_save_check",
                "real_upload_check",
                "real_pick_images_button",
                "real_image_count",
                "real_qc_check",
                "real_policy_hint",
                "real_start_button",
                "real_stop_button",
            )
        ]
        self._advanced_widgets = [
            getattr(window, name, None)
            for name in (
                "source_port",
                "makro_port",
                "current_page_check",
                "step1_button",
                "step2_button",
                "step3_button",
            )
        ]

        all_widgets = _drain(layout)
        placed: set[int] = set()

        def take(widget: Any) -> Any:
            if isinstance(widget, QWidget):
                placed.add(id(widget))
            return widget

        # Header: title, then the source hint as one calm paragraph.
        layout.addLayout(_title_block(take(eyebrow), take(title)))
        if isinstance(hint, QLabel):
            take(hint)
            _reset_size(hint)
            hint.setWordWrap(True)
            hint.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
            self._gap(layout, 6, 0)
            layout.addWidget(hint)
            self._roomy_only.append(hint)
        self._gap(layout, _SECTION_GAP, 10)

        # Source: one link, or customer files, plus the final listing photos.
        self._control(take(url_input))
        layout.addWidget(url_input)
        self._gap(layout, 8, 6)
        files_row = _hbox(8)
        for button in (getattr(window, "product_pack_button", None), photo_button):
            if isinstance(button, QWidget):
                self._control(take(button))
                files_row.addWidget(button, 1)
        layout.addLayout(files_row)
        # Spare height is shared between the section breaks instead of opening one
        # large hole above the run action.
        self._gap(layout, _SECTION_GAP, 10)
        layout.addStretch(1)

        # Listing intent: stacked label/editor pairs on one left edge.
        layout.addWidget(_label("LISTING INTENT", "sectionEyebrow"))
        self._gap(layout, 10, 6)

        def add_field(label: QLabel | None, editor: Any, *, trailing: Any = None) -> None:
            if not isinstance(editor, QWidget):
                return
            if isinstance(label, QLabel):
                layout.addWidget(_field_label(take(label)))
                self._gap(layout, _LABEL_GAP, 3)
            self._control(take(editor))
            if isinstance(trailing, QWidget):
                row = _hbox(8)
                row.addWidget(editor, 1)
                take(trailing)
                _reset_size(trailing)
                trailing.setFixedWidth(64)
                self._height(trailing, _CONTROL_HEIGHT, _COMPACT_CONTROL_HEIGHT)
                row.addWidget(trailing)
                layout.addLayout(row)
            else:
                layout.addWidget(editor)
            self._gap(layout, _FIELD_GAP, 6)

        add_field(offer_label, offer_input, trailing=detail_button)
        if isinstance(detail_host, QWidget):
            take(detail_host)
            host_layout = detail_host.layout()
            if isinstance(host_layout, QLayout):
                host_layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(detail_host)
            self._gap(layout, _FIELD_GAP, 6)
        add_field(guidance_label, guidance_input)
        add_field(keywords_label, keywords_input)
        if isinstance(vertical_input, QWidget):
            add_field(_label("类目", "composerFieldLabel"), vertical_input)

        layout.addStretch(1)
        self._gap(layout, _SECTION_GAP - _FIELD_GAP, 2)

        # Run: one dominant action, its safe-pause/stop pair and the browser state.
        start = take(window.start_button)
        self._control(start, roomy=_PRIMARY_HEIGHT, compact=_COMPACT_PRIMARY_HEIGHT)
        layout.addWidget(start)
        self._gap(layout, 8, 6)
        run_row = _hbox(8)
        for button in (pause_button, window.stop_button):
            if isinstance(button, QWidget):
                self._control(take(button))
                run_row.addWidget(button, 1)
        layout.addLayout(run_row)
        status_row = _hbox(10)
        for label in (browser_label, pause_hint):
            if isinstance(label, QLabel):
                take(label)
                _reset_size(label)
                label.setWordWrap(False)
        if isinstance(browser_label, QLabel):
            status_row.addWidget(browser_label, 1)
        if isinstance(pause_hint, QLabel):
            pause_hint.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            status_row.addWidget(pause_hint)
        self._gap(layout, 8, 4)
        layout.addLayout(status_row)

        for widget in (*self._real_controls, *self._advanced_widgets, self._summary_title, self._summary_hint, toggle):
            take(widget)

        hidden = [w for w in all_widgets if id(w) not in placed and w.isHidden()]
        leftovers = [w for w in all_widgets if id(w) not in placed and not w.isHidden()]
        self._legacy_hidden = hidden
        if leftovers:
            _warn_unplaced("single product card", leftovers)
            extras = _hbox(8)
            for widget in leftovers:
                extras.addWidget(widget)
            layout.addSpacing(8)
            layout.addLayout(extras)

        _reset_size(card)
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def _compose_execution_card(self) -> QFrame:
        window = self.window
        card = _card()
        layout = _card_layout(card)

        toggle = getattr(window, "real_settings_toggle", None)
        # The legacy summary title is renamed to "填写设置" by the copy layer, which
        # would repeat the button text. It stays alive (hidden) for that layer.
        heading = _hbox(12)
        heading.addLayout(
            _title_block(_label("REAL EXECUTION", "sectionEyebrow"), _label("真实填写", "cardTitle")),
            1,
        )
        if isinstance(toggle, QWidget):
            _reset_size(toggle)
            toggle.setMinimumWidth(112)
            toggle.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            self._height(toggle, _CONTROL_HEIGHT, _COMPACT_CONTROL_HEIGHT)
            heading.addWidget(toggle, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(heading)

        if isinstance(self._summary_hint, QLabel):
            _reset_size(self._summary_hint)
            self._summary_hint.setWordWrap(True)
            self._gap(layout, 6, 0)
            layout.addWidget(self._summary_hint)
            self._roomy_only.append(self._summary_hint)

        stats = self._safety_stats()
        if stats is not None:
            self._gap(layout, _SECTION_GAP, 8)
            layout.addLayout(stats)

        # The real-execution controls are presented by the shared settings modal
        # through proxies. Their canonical QWidget owners stay alive here, hidden.
        legacy = [w for w in self._real_controls if isinstance(w, QWidget)]
        legacy.extend(self._legacy_hidden)
        if isinstance(self._summary_title, QLabel):
            legacy.append(self._summary_title)
        self._real_holder = _hidden_holder(card, legacy)
        return card

    def _safety_stats(self) -> QGridLayout | None:
        window = self.window
        values = [getattr(window, name, None) for name in ("write_value", "save_value", "qc_value")]
        if not all(isinstance(value, QLabel) for value in values):
            return None
        page = values[0].parentWidget()
        page_layout = page.layout() if isinstance(page, QWidget) else None
        captions: list[QLabel | None] = []
        if isinstance(page_layout, QGridLayout):
            for value in values:
                index = page_layout.indexOf(value)
                row, _column, _rs, _cs = page_layout.getItemPosition(index) if index >= 0 else (-1, 0, 0, 0)
                item = page_layout.itemAtPosition(row, 0) if row >= 0 else None
                caption = item.widget() if item is not None else None
                captions.append(caption if isinstance(caption, QLabel) else None)
        else:
            captions = [None, None, None]

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(3)
        fallback = ("Makro Write", "Save", "Send to QC")
        for column, (caption, value) in enumerate(zip(captions, values)):
            if not isinstance(caption, QLabel):
                caption = _label(fallback[column], "composerStatCaption")
            _reset_size(caption)
            _restyle(caption, "composerStatCaption")
            _reset_size(value)
            value.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            grid.addWidget(caption, 0, column)
            grid.addWidget(value, 1, column)
            grid.setColumnStretch(column, 1)
        self._safety_page = page if isinstance(page, QFrame) else None
        return grid

    def _compose_advanced_card(self) -> QFrame:
        card = _card()
        layout = _card_layout(card)
        layout.addLayout(
            _title_block(_label("ADVANCED", "sectionEyebrow"), _label("高级 · 阶段诊断", "cardTitle"))
        )
        hint = _label("采集端口与单阶段诊断，仅在排查问题时使用。", "cardHint", wrap=True)
        self._gap(layout, 6, 0)
        layout.addWidget(hint)
        self._roomy_only.append(hint)
        self._gap(layout, _SECTION_GAP, 8)

        window = self.window
        source_port = getattr(window, "source_port", None)
        current_page = getattr(window, "current_page_check", None)
        source_row = _hbox(12)
        if isinstance(source_port, QSpinBox):
            # Quick renders "Source CDP" + value + stepper as one control for this
            # semantic name; the QSpinBox remains the business owner.
            source_port.setObjectName("sourceCdpSpin")
            _reset_size(source_port)
            source_port.setFixedWidth(176)
            self._height(source_port, _CONTROL_HEIGHT, _COMPACT_CONTROL_HEIGHT)
            source_row.addWidget(source_port)
        if isinstance(current_page, QWidget):
            _reset_size(current_page)
            source_row.addWidget(current_page, 1)
        layout.addLayout(source_row)
        self._gap(layout, 10, 6)

        stage_row = _hbox(8)
        for name in ("step1_button", "step2_button", "step3_button"):
            button = getattr(window, name, None)
            if isinstance(button, QWidget):
                self._control(button)
                stage_row.addWidget(button, 1)
        layout.addLayout(stage_row)

        makro_port = getattr(window, "makro_port", None)
        if isinstance(makro_port, QWidget):
            self._advanced_holder = _hidden_holder(card, [makro_port])
        return card

    # -- result column -------------------------------------------------------

    def _compose_side_card(self) -> QFrame:
        window = self.window
        tabs = getattr(window, "side_detail_tabs", None)
        if not isinstance(tabs, QTabWidget):
            raise RuntimeError("Single composer requires the side detail tabs")

        pages = [tabs.widget(index) for index in range(tabs.count())]
        frames = [page for page in pages if isinstance(page, QFrame)]
        _unregister_glass(window, frames)

        # Safety moved into the execution card; Runtime and Reference stay here.
        safety = getattr(self, "_safety_page", None)
        if isinstance(safety, QWidget):
            index = tabs.indexOf(safety)
            if index >= 0:
                tabs.removeTab(index)
            safety.hide()

        old_host = tabs.parentWidget()
        old_layout = old_host.layout() if old_host is not None else None
        if isinstance(old_layout, QLayout):
            old_layout.removeWidget(tabs)

        card = _card()
        layout = _card_layout(card)
        layout.setContentsMargins(12, 12, 12, 12)
        _reset_size(tabs)
        tabs.setParent(card)
        tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        tabs.setStyleSheet(
            tabs.styleSheet()
            + r"""
QTabWidget#sideDetailTabs::pane { border: 0; background: transparent; top: 0px; }
QTabWidget#sideDetailTabs QTabBar { background: transparent; }
QTabWidget#sideDetailTabs QTabBar::tab {
    min-height: 32px; max-height: 32px; padding: 0 10px; margin: 0 4px 8px 0;
    color: rgba(255,255,255,172); background: rgba(0,0,0,30);
    border: 1px solid rgba(255,255,255,12); border-radius: 8px;
    font-size: 11px; font-weight: 650;
}
QTabWidget#sideDetailTabs QTabBar::tab:selected {
    color: #ffffff; background: rgba(255,255,255,42); border-color: rgba(255,255,255,28);
}
"""
        )
        bar = tabs.tabBar()
        bar.setExpanding(True)
        bar.setUsesScrollButtons(False)
        bar.setDrawBase(False)
        for page in pages:
            if isinstance(page, QWidget):
                page.setMinimumHeight(0)
                page.setMaximumHeight(_SIZE_MAX)
                page.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
                page_layout = page.layout()
                if isinstance(page_layout, QBoxLayout):
                    page_layout.setContentsMargins(6, 4, 6, 4)
        reference = getattr(window, "web_table", None)
        if isinstance(reference, QWidget):
            reference.setMinimumHeight(0)
        layout.addWidget(tabs, 1)
        tabs.show()
        self._old_side_host = old_host
        return card

    def _compose_log_card(self) -> QFrame:
        console = getattr(self.window, "console", None)
        tabs = getattr(console, "tabs", None)
        if not isinstance(tabs, QTabWidget):
            raise RuntimeError("Single composer requires the console tabs")
        console_layout = console.layout()
        if isinstance(console_layout, QLayout):
            console_layout.removeWidget(tabs)
        card = _card()
        layout = _card_layout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        _reset_size(tabs)
        tabs.setParent(card)
        tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        for index in range(tabs.count()):
            page = tabs.widget(index)
            page_layout = page.layout() if isinstance(page, QWidget) else None
            if isinstance(page_layout, QBoxLayout):
                page_layout.setContentsMargins(6, 6, 6, 6)
                page_layout.setSpacing(6)
        layout.addWidget(tabs, 1)
        tabs.show()
        return card

    def _compose_progress_card(self) -> None:
        window = self.window
        console = getattr(window, "console", None)
        if not isinstance(console, QFrame):
            raise RuntimeError("Single composer requires the acceptance console")
        layout = _card_layout(console)
        units = [unit for unit in getattr(console, "phase_units", {}).values() if isinstance(unit, QWidget)]
        presence = getattr(getattr(window, "_activity_presence_controller", None), "widget", None)
        toggle = getattr(window, "console_detail_toggle", None)
        progress = getattr(console, "progress", None)
        detail = getattr(console, "progress_detail", None)

        widgets = _drain(layout)
        title = next(
            (w for w in widgets if isinstance(w, QLabel) and w.objectName() == "consoleTitle"),
            None,
        )
        placed: set[int] = set()

        header = _hbox(8)
        if isinstance(title, QLabel):
            placed.add(id(title))
            _reset_size(title)
            header.addWidget(title, 0, Qt.AlignmentFlag.AlignVCenter)
            header.addSpacing(8)
        for unit in units:
            placed.add(id(unit))
            _reset_size(unit)
            unit.setFixedHeight(_PROGRESS_UNIT_HEIGHT)
            unit.setMinimumWidth(112)
            unit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            unit_layout = unit.layout()
            if isinstance(unit_layout, QBoxLayout):
                unit_layout.setContentsMargins(10, 5, 10, 6)
                unit_layout.setSpacing(1)
            unit_detail = getattr(unit, "detail", None)
            if isinstance(unit_detail, QWidget):
                unit_detail.hide()
            unit.show()
            header.addWidget(unit, 1)
        if isinstance(toggle, QPushButton):
            placed.add(id(toggle))
            self._bind_console_detail(toggle)
            header.addSpacing(4)
            header.addWidget(toggle, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(header)

        # Overall activity reads left to right; elapsed time and log volume sit at
        # its end instead of competing with the four phase cards for width.
        activity = _hbox(14)
        if isinstance(presence, QWidget):
            placed.add(id(presence))
            activity.addWidget(presence, 1)
        meters = _vbox(2)
        for label in (console.total_time_label, console.log_count_label):
            placed.add(id(label))
            _reset_size(label)
            # Quick re-measures CJK glyphs with its own font engine; a fixed slack
            # column keeps "用时 12.3s" / "128 条记录" from eliding mid-run.
            label.setFixedWidth(96)
            label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            meters.addWidget(label)
        activity.addLayout(meters)
        layout.addSpacing(10)
        layout.addLayout(activity)

        if isinstance(progress, QWidget):
            placed.add(id(progress))
            row = _hbox(10)
            row.addWidget(progress, 1)
            if isinstance(detail, QLabel):
                placed.add(id(detail))
                row.addWidget(detail)
            layout.addSpacing(8)
            layout.addLayout(row)

        leftovers = [w for w in widgets if id(w) not in placed]
        self._console_holder = _hidden_holder(console, [w for w in leftovers if w.isHidden()])
        visible = [w for w in leftovers if not w.isHidden()]
        if visible:
            _warn_unplaced("progress card", visible)
            for widget in visible:
                layout.addWidget(widget)

        _reset_size(console)
        console.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def _bind_console_detail(self, toggle: QPushButton) -> None:
        """Make "展开详情" the plain entry to the shared console detail modal."""

        details = getattr(self.window, "_card_details", None)
        for signal in (toggle.toggled, toggle.clicked):
            try:
                signal.disconnect()
            except (RuntimeError, TypeError):
                pass
        toggle.setCheckable(False)
        toggle.setEnabled(True)
        toggle.setText("展开详情")
        _reset_size(toggle)
        toggle.setFixedHeight(_CONTROL_HEIGHT)
        toggle.show()
        opener = getattr(details, "open_console_details", None)
        if callable(opener):
            toggle.clicked.connect(lambda *_: opener())

    def _compose_kpis(self) -> None:
        self.kpi_row = _hbox(_PAGE_GAP)
        for name in ("ready_card", "missing_card", "conflict_card", "blocked_card"):
            card = getattr(self.window, name, None)
            if not isinstance(card, QFrame):
                continue
            layout = card.layout()
            _reset_size(card)
            card.setFixedHeight(_KPI_HEIGHT)
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            if isinstance(layout, QBoxLayout):
                layout.setContentsMargins(18, 10, 18, 10)
                layout.setSpacing(1)
                for index in range(layout.count()):
                    item_widget = layout.itemAt(index).widget()
                    if isinstance(item_widget, QLabel):
                        item_widget.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            self.kpi_row.addWidget(card, 1)

    def _compose_field_card(self) -> None:
        table = getattr(self.window, "field_table", None)
        card = table.parentWidget() if isinstance(table, QWidget) else None
        if not isinstance(card, QFrame):
            raise RuntimeError("Single composer requires the field review card")
        layout = card.layout()
        if isinstance(layout, QBoxLayout):
            layout.setContentsMargins(*_CARD_MARGINS)
            layout.setSpacing(10)
        if isinstance(table, QWidget):
            table.setMinimumHeight(0)
        _reset_size(card)
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.field_card = card

    # -- page ----------------------------------------------------------------

    def _assemble_page(self) -> None:
        window = self.window
        page_layout = self.page.layout()
        old_widgets = _drain(page_layout)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(0)

        self.task_column = QWidget(self.page)
        self.task_column.setObjectName("singleTaskColumn")
        task_layout = QVBoxLayout(self.task_column)
        task_layout.setContentsMargins(0, 0, 0, 0)
        task_layout.setSpacing(_PAGE_GAP)
        task_layout.addWidget(self.hero, 1)
        task_layout.addWidget(self.execution_card)
        task_layout.addWidget(self.advanced_card)

        self.result_column = QWidget(self.page)
        self.result_column.setObjectName("singleResultColumn")
        result_layout = QVBoxLayout(self.result_column)
        result_layout.setContentsMargins(0, 0, 0, 0)
        result_layout.setSpacing(_PAGE_GAP)
        result_layout.addWidget(window.console)
        result_layout.addLayout(self.kpi_row)
        body = _hbox(_PAGE_GAP)
        body.addWidget(self.field_card, 1)
        body.addWidget(self.side_card)
        result_layout.addLayout(body, 1)
        result_layout.addWidget(self.log_card)

        columns = _hbox(_PAGE_GAP + 2)
        columns.addWidget(self.task_column)
        columns.addWidget(self.result_column, 1)
        page_layout.addLayout(columns, 1)

        # Former structural hosts (bodySplitter, workspaceHost, side scroll area)
        # no longer contain any business widget.
        retired = [w for w in old_widgets if w not in (self.hero, window.console)]
        retired = [
            w
            for w in retired
            if w not in (getattr(window, name, None) for name in ("ready_card", "missing_card", "conflict_card", "blocked_card"))
        ]
        for widget in retired:
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()
        if getattr(window, "_ui_polish_body_splitter", None) is not None:
            setattr(window, "_ui_polish_body_splitter", None)
        old_side = getattr(self, "_old_side_host", None)
        if isinstance(old_side, QWidget):
            old_side.hide()

        for widget in (self.task_column, self.result_column, self.execution_card, self.advanced_card, self.side_card, self.log_card):
            widget.show()

    # -- responsive sizing -----------------------------------------------------

    def apply(self) -> None:
        """Commit column widths and the compact budget for the current page size."""

        width = max(1, int(self.page.width()))
        height = max(1, int(self.page.height()))

        task_width = min(_TASK_MAX, max(_TASK_MIN, round(width * _TASK_RATIO)))
        if self.task_column.minimumWidth() != task_width:
            self.task_column.setFixedWidth(task_width)

        result_width = max(1, width - task_width - _PAGE_GAP)
        side_width = min(_SIDE_MAX, max(_SIDE_MIN, round(result_width * _SIDE_RATIO)))
        if self.side_card.minimumWidth() != side_width:
            self.side_card.setFixedWidth(side_width)

        # Short windows keep every control. When the roomy task column does not fit,
        # spacing and control heights tighten and explanatory paragraphs fold into
        # tooltips. The decision is measured, not guessed from a screen size.
        if self._measured_for != (task_width, height):
            self._measured_for = (task_width, height)
            self._set_density(False)
            if self._task_minimum_height(task_width) > height:
                self._set_density(True)

        for layout in (self.page.layout(), self.task_column.layout(), self.result_column.layout()):
            if isinstance(layout, QLayout):
                layout.invalidate()
                layout.activate()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self.page and event.type() in {QEvent.Type.Resize, QEvent.Type.Show}:
            self.apply()
        return False

    def cleanup(self) -> None:
        try:
            self.page.removeEventFilter(self)
        except RuntimeError:
            pass


# ---------------------------------------------------------------------------
# Batch workspace
# ---------------------------------------------------------------------------


class BatchWorkspaceComposer(QObject):
    """Put Batch on the shared grid: links → overview → tasks → execution."""

    def __init__(self, workspace: QWidget) -> None:
        super().__init__(workspace)
        self.workspace = workspace
        root = workspace.layout()
        if not isinstance(root, QVBoxLayout) or root.count() < 4:
            raise RuntimeError("Batch composer requires the Batch workspace layout")
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(_PAGE_GAP)

        editor = getattr(workspace, "_batch_url_editor", None)
        source = editor.parentWidget() if isinstance(editor, QWidget) else root.itemAt(0).widget()
        if isinstance(source, QFrame):
            self._compose_source(source)

        summary = root.itemAt(1).layout() if root.count() > 1 else None
        if isinstance(summary, QHBoxLayout):
            summary.setSpacing(_PAGE_GAP)
            for index in range(summary.count()):
                card = summary.itemAt(index).widget()
                if not isinstance(card, QFrame):
                    continue
                _reset_size(card)
                card.setFixedHeight(_KPI_HEIGHT + 6)
                card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
                box = card.layout()
                if isinstance(box, QBoxLayout):
                    box.setContentsMargins(18, 9, 18, 9)
                    box.setSpacing(1)

        queue = root.itemAt(2).widget() if root.count() > 2 else None
        if isinstance(queue, QFrame):
            _reset_size(queue)
            queue.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            layout = queue.layout()
            if isinstance(layout, QBoxLayout):
                layout.setContentsMargins(*_CARD_MARGINS)
                layout.setSpacing(10)

        action = root.itemAt(3).widget() if root.count() > 3 else None
        if isinstance(action, QFrame):
            _reset_size(action)
            action.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            layout = action.layout()
            if isinstance(layout, QBoxLayout):
                layout.setContentsMargins(20, 12, 20, 12)
                layout.setSpacing(10)
                for item_index in range(layout.count()):
                    widget = layout.itemAt(item_index).widget()
                    if isinstance(widget, QPushButton):
                        _reset_size(widget)
                        widget.setFixedHeight(_CONTROL_HEIGHT)
                        widget.setMinimumWidth(96)
            action.setFixedHeight(_CONTROL_HEIGHT + 24)

        root.setStretch(0, 0)
        root.setStretch(1, 0)
        root.setStretch(2, 1)
        root.setStretch(3, 0)

    def _compose_source(self, card: QFrame) -> None:
        workspace = self.workspace
        layout = card.layout()
        if not isinstance(layout, QVBoxLayout):
            return

        context = None
        for widget in _widgets_in(layout):
            if isinstance(widget, QFrame) and widget.objectName() == "batchAccountContext":
                context = widget
                break
        heading = layout.itemAt(0).layout() if layout.count() else None
        heading_labels = [w for w in _widgets_in(heading) if isinstance(w, QLabel)]
        eyebrow = next((w for w in heading_labels if w.objectName() == "sectionEyebrow"), None)
        title = next((w for w in heading_labels if w.objectName() == "cardTitle"), None)
        hint = next((w for w in heading_labels if w.objectName() == "cardHint"), None)

        editor = getattr(workspace, "_batch_url_editor", None)
        legacy_input = getattr(workspace, "_legacy_batch_url_input", None)
        browser_label = getattr(getattr(workspace.window(), "_managed_makro_browser", None), "_batch_label", None)
        ports = [getattr(workspace, name, None) for name in ("source_port", "worker_count")]
        makro_port = getattr(workspace, "makro_port", None)
        clear_button = getattr(workspace, "clear_button", None)
        prepare_button = getattr(workspace, "prepare_button", None)

        widgets = _drain(layout)
        placed: set[int] = set()

        top = _hbox(16)
        if isinstance(title, QLabel) and isinstance(hint, QLabel):
            title.setToolTip(hint.text())
        top.addLayout(_title_block(eyebrow, title), 1)
        for label in (eyebrow, title):
            if isinstance(label, QWidget):
                placed.add(id(label))
        if isinstance(context, QFrame):
            placed.add(id(context))
            _reset_size(context)
            context.setFixedWidth(560)
            context.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            top.addWidget(context, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(top)
        layout.addSpacing(12)

        if isinstance(editor, QWidget):
            placed.add(id(editor))
            layout.addWidget(editor)
            layout.addSpacing(12)

        footer = _hbox(10)
        for spin in ports:
            if isinstance(spin, QSpinBox):
                placed.add(id(spin))
                spin.setFixedHeight(_CONTROL_HEIGHT)
                footer.addWidget(spin)
        if isinstance(browser_label, QLabel):
            placed.add(id(browser_label))
            footer.addSpacing(6)
            footer.addWidget(browser_label, 0, Qt.AlignmentFlag.AlignVCenter)
        footer.addStretch(1)
        for button in (clear_button, prepare_button):
            if isinstance(button, QPushButton):
                placed.add(id(button))
                _reset_size(button)
                button.setFixedHeight(_CONTROL_HEIGHT)
                button.setMinimumWidth(120)
                footer.addWidget(button)
        layout.addLayout(footer)

        hidden = [w for w in widgets if id(w) not in placed]
        for extra in (makro_port, legacy_input, hint):
            if isinstance(extra, QWidget) and extra not in hidden:
                hidden.append(extra)
        visible = [w for w in hidden if not w.isHidden() and w not in (makro_port, legacy_input, hint)]
        if visible:
            _warn_unplaced("batch source card", visible)
            for widget in visible:
                layout.addWidget(widget)
        self._holder = _hidden_holder(card, [w for w in hidden if w not in visible])

        layout.setContentsMargins(*_CARD_MARGINS)
        layout.setSpacing(0)
        _reset_size(card)
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)


# ---------------------------------------------------------------------------
# Common header
# ---------------------------------------------------------------------------


class HeaderComposer(QObject):
    """Keep the shared header in one order while late controls keep arriving.

    Several features (updater, Agent, toy, assistant switch, account) add their
    header control after startup. Each arrival only appends to the header row;
    this owner re-sorts the row into stable groups on the next event-loop turn.
    """

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self.window = window
        self.root = window.centralWidget()
        outer = self.root.layout() if self.root is not None else None
        header = outer.itemAt(0).layout() if isinstance(outer, QVBoxLayout) and outer.count() else None
        if not isinstance(header, QHBoxLayout):
            raise RuntimeError("header composer requires the common header row")
        self.header = header
        self._order: tuple[int, ...] = ()
        self._busy = False

        self.mode_labels = (
            _label("单商品", "headerModeLabel"),
            _label("批量", "headerModeLabel"),
        )
        stack = getattr(window, "mode_stack", None)
        if isinstance(stack, QStackedWidget):
            stack.currentChanged.connect(self._sync_mode_labels)
        self._sync_mode_labels()

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(0)
        self._timer.timeout.connect(self.apply)
        self.root.installEventFilter(self)
        window.destroyed.connect(self.cleanup)
        self.apply()

    def _sync_mode_labels(self, *_args: object) -> None:
        stack = getattr(self.window, "mode_stack", None)
        batch = isinstance(stack, QStackedWidget) and stack.currentIndex() == 1
        single_label, batch_label = self.mode_labels
        single_label.setStyleSheet(_MODE_LABEL_IDLE if batch else _MODE_LABEL_ACTIVE)
        batch_label.setStyleSheet(_MODE_LABEL_ACTIVE if batch else _MODE_LABEL_IDLE)

    def _attr(self, *path: str) -> QWidget | None:
        value: Any = self.window
        for name in path:
            value = getattr(value, name, None)
            if value is None:
                return None
        return value if isinstance(value, QWidget) else None

    def _by_name(self, widgets: list[QWidget], object_name: str) -> QWidget | None:
        return next((w for w in widgets if w.objectName() == object_name), None)

    def _groups(self, widgets: list[QWidget]) -> list[list[QWidget | None]]:
        single_label, batch_label = self.mode_labels
        mode = [single_label, self._attr("_workspace_mode_switch"), batch_label]
        status = [self._attr("phase_badge"), self._attr("open_run_button")]
        agent = self._attr("_agent_workspace_controller", "_entry_button") or next(
            (w for w in widgets if isinstance(w, QPushButton) and w.text() == "AGENT"),
            None,
        )
        actions = [
            self._attr("_channel_account_center", "button"),
            agent,
            self._attr("_ai_settings_controller", "button"),
        ]
        preferences = [
            self._attr("_background_drift_toggle_label"),
            self._attr("_background_drift_switch"),
            self._attr("_sakana_toy_controller", "toggle"),
            self._attr("_runtime_assistant_toggle_label"),
            self._attr("_runtime_assistant_switch"),
        ]
        version = [
            self._by_name(widgets, "appVersionBadge"),
            self._by_name(widgets, "checkUpdateButton"),
            self._by_name(widgets, "accountHeaderButton"),
        ]
        return [mode, status, actions, preferences, version]

    def apply(self) -> None:
        if self._busy:
            return
        header = self.header
        title_layout: QLayout | None = None
        widgets: list[QWidget] = []
        for index in range(header.count()):
            item = header.itemAt(index)
            if item.widget() is not None:
                widgets.append(item.widget())
            elif item.layout() is not None and title_layout is None:
                title_layout = item.layout()
        for label in self.mode_labels:
            if label not in widgets:
                widgets.append(label)

        groups = self._groups(widgets)
        known = {id(w) for group in groups for w in group if w is not None}
        others = [w for w in widgets if id(w) not in known]
        if others:
            groups[3] = [*groups[3], *others]

        order = tuple(id(w) for group in groups for w in group if w is not None and w in widgets)
        if order == self._order:
            return

        self._busy = True
        try:
            while header.count():
                header.takeAt(0)
            header.setSpacing(8)
            header.setContentsMargins(0, 0, 0, 2)
            if title_layout is not None:
                header.addLayout(title_layout)
            gaps = (32, 18, 0, 22, 22)
            for index, group in enumerate(groups):
                members = [w for w in group if w is not None and w in widgets]
                if not members:
                    continue
                if index == 2:
                    header.addStretch(1)
                else:
                    header.addSpacing(gaps[index])
                for widget in members:
                    # addWidget reparents and re-shows unless the owner hid it on
                    # purpose (for example 打开任务目录 while Batch is active).
                    header.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)
            self._order = order
        finally:
            self._busy = False
        _publish_structure(self.window)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self.root and event.type() == QEvent.Type.ChildAdded and not self._busy:
            if not self._timer.isActive():
                self._timer.start()
        return False

    def cleanup(self) -> None:
        self._timer.stop()
        try:
            self.root.removeEventFilter(self)
        except RuntimeError:
            pass


# ---------------------------------------------------------------------------
# Installation
# ---------------------------------------------------------------------------


class WorkspaceComposer(QObject):
    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self.window = window
        window.setStyleSheet(window.styleSheet() + "\n" + _COMPOSER_STYLE)

        root = window.centralWidget()
        outer = root.layout() if root is not None else None
        if isinstance(outer, QVBoxLayout):
            outer.setContentsMargins(22, 14, 22, 18)
            outer.setSpacing(14)

        self.single = SingleWorkspaceComposer(window)
        workspace = getattr(window, "batch_workspace", None)
        self.batch = BatchWorkspaceComposer(workspace) if isinstance(workspace, QWidget) else None
        self.header = HeaderComposer(window)
        _schedule_glass(window)

    def apply(self) -> None:
        self.single.apply()
        self.header.apply()


def install_workspace_composer(window: QMainWindow) -> WorkspaceComposer:
    existing = getattr(window, "_workspace_composer", None)
    if isinstance(existing, WorkspaceComposer):
        return existing
    composer = WorkspaceComposer(window)
    window._workspace_composer = composer  # type: ignore[attr-defined]
    return composer


def refresh_workspace_layout(window: QMainWindow) -> None:
    """Commit the composed geometry before Quick snapshots a page."""

    composer = getattr(window, "_workspace_composer", None)
    apply = getattr(composer, "apply", None)
    if callable(apply):
        apply()


__all__ = [
    "BatchWorkspaceComposer",
    "HeaderComposer",
    "SingleWorkspaceComposer",
    "WorkspaceComposer",
    "install_workspace_composer",
    "refresh_workspace_layout",
]
