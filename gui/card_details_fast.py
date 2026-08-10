from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QKeyEvent, QMouseEvent, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFrame,
    QGraphicsBlurEffect,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .card_details import CardDetailController


_GEOMETRY_COALESCE_MS = 32
_DEFAULT_RATIO = (0.80, 0.80)
_CONSOLE_RATIO = (0.90, 0.86)
_MODAL_MARGIN = 28
_BACKDROP_SCALE = 0.34
_BACKDROP_BLUR_RADIUS = 8.0


_GLASS_MODAL_STYLE = r"""
QLabel#cardDetailBackdrop {
    background: transparent;
    border: 0;
}
QFrame#cardDetailScrim {
    background-color: rgba(12, 17, 26, 94);
    border: 0;
}
QFrame#cardDetailDrawer {
    background-color: rgba(220, 228, 238, 74);
    border: 1px solid rgba(255, 255, 255, 72);
    border-radius: 14px;
}
QFrame#cardDetailSection {
    background-color: rgba(255, 255, 255, 22);
    border: 1px solid rgba(255, 255, 255, 24);
    border-radius: 10px;
}
QLabel#cardDetailEyebrow {
    color: rgba(255, 255, 255, 166);
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 1px;
}
QLabel#cardDetailTitle {
    color: #ffffff;
    font-size: 21px;
    font-weight: 730;
}
QLabel#cardDetailSectionTitle {
    color: rgba(255, 255, 255, 236);
    font-size: 11px;
    font-weight: 720;
}
QLabel#cardDetailText, QLabel#modalFieldLabel, QLabel#modalMetaLabel {
    color: rgba(255, 255, 255, 202);
    font-size: 11px;
}
QToolButton#cardDetailClose {
    min-width: 32px;
    max-width: 32px;
    min-height: 32px;
    max-height: 32px;
    color: #ffffff;
    background-color: rgba(255, 255, 255, 24);
    border: 1px solid rgba(255, 255, 255, 26);
    border-radius: 9px;
    font-size: 18px;
}
QToolButton#cardDetailClose:hover {
    background-color: rgba(255, 255, 255, 42);
}
QScrollArea#cardDetailScroll {
    background: transparent;
    border: 0;
}
QWidget#cardDetailBody {
    background: transparent;
}
QTableWidget#cardDetailTable,
QPlainTextEdit#cardDetailTextView {
    color: rgba(255, 255, 255, 224);
    background-color: rgba(15, 23, 34, 66);
    border: 1px solid rgba(255, 255, 255, 14);
    border-radius: 8px;
}
QTableWidget#cardDetailTable::item {
    padding: 7px 9px;
    border-bottom: 1px solid rgba(255, 255, 255, 8);
}
QTableWidget#cardDetailTable QHeaderView::section {
    min-height: 35px;
    padding: 0 9px;
    color: rgba(255, 255, 255, 220);
    background-color: rgba(255, 255, 255, 22);
    border: 0;
    font-size: 10px;
    font-weight: 700;
}
QPlainTextEdit#cardDetailTextView {
    padding: 9px;
    font-family: "Cascadia Mono", "Consolas", monospace;
    font-size: 10px;
}
QTabWidget#modalDetailTabs::pane {
    border: 1px solid rgba(255, 255, 255, 18);
    border-radius: 9px;
    background-color: rgba(12, 20, 31, 44);
    top: -1px;
}
QTabWidget#modalDetailTabs QTabBar::tab {
    min-height: 30px;
    padding: 0 14px;
    margin-right: 4px;
    color: rgba(255, 255, 255, 170);
    background-color: rgba(255, 255, 255, 12);
    border: 1px solid rgba(255, 255, 255, 10);
    border-radius: 7px;
}
QTabWidget#modalDetailTabs QTabBar::tab:selected {
    color: #ffffff;
    background-color: rgba(255, 255, 255, 30);
    border-color: rgba(255, 255, 255, 24);
}
QPushButton#modalPrimaryButton {
    min-height: 35px;
    padding: 0 18px;
    color: #ffffff;
    background-color: rgba(255, 255, 255, 38);
    border: 1px solid rgba(255, 255, 255, 28);
    border-radius: 8px;
    font-weight: 700;
}
QPushButton#modalPrimaryButton:hover {
    background-color: rgba(255, 255, 255, 52);
}
QPushButton#modalDangerButton {
    min-height: 35px;
    padding: 0 18px;
    color: rgba(255, 225, 231, 238);
    background-color: rgba(126, 38, 56, 72);
    border: 1px solid rgba(236, 118, 143, 42);
    border-radius: 8px;
    font-weight: 700;
}
QComboBox#modalCombo {
    min-height: 35px;
    padding: 0 11px;
    color: #ffffff;
    background-color: rgba(13, 22, 34, 66);
    border: 1px solid rgba(255, 255, 255, 20);
    border-radius: 8px;
}
QCheckBox#modalCheck {
    color: rgba(255, 255, 255, 220);
    spacing: 8px;
}
"""


class FastCardDetailController(CardDetailController):
    """One stable, full-screen glass modal reused by every expansion path.

    The interaction deliberately mirrors the reference webpage's structure:
    the main UI never reflows, a blurred snapshot becomes the backdrop, a soft
    scrim suppresses the old surface, and one centered translucent panel owns
    the detail content.  No geometry interpolation or layout-height animation
    exists in this controller.
    """

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)

        # The base controller still carries its historical animated path.  This
        # runtime controller never uses it and removes both offscreen effects.
        self.drawer.setGraphicsEffect(None)
        self.drawer_effect = None  # type: ignore[assignment]
        self.ghost.setGraphicsEffect(None)
        self.ghost_effect = None  # type: ignore[assignment]
        self.ghost.hide()

        # The old corner affordance is gone.  The discovered cards themselves
        # are the click targets, while child controls retain native mouse input.
        self._expandable_cards = tuple(self._installed_cards)
        for button in tuple(self._buttons.values()):
            button.hide()
            button.setParent(None)
            button.deleteLater()
        self._buttons.clear()

        # A pre-blurred snapshot of the complete composited app surface sits
        # underneath the scrim.  It is generated once per open, never per frame.
        self.backdrop = QLabel(self.root)
        self.backdrop.setObjectName("cardDetailBackdrop")
        self.backdrop.setScaledContents(True)
        self.backdrop.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.backdrop.hide()

        self._modal_ratio = _DEFAULT_RATIO
        self.root.setStyleSheet(self.root.styleSheet() + "\n" + _GLASS_MODAL_STYLE)

        self._geometry_timer = QTimer(self)
        self._geometry_timer.setSingleShot(True)
        self._geometry_timer.setInterval(_GEOMETRY_COALESCE_MS)
        self._geometry_timer.timeout.connect(self._sync_geometry)

        self._install_real_settings_action()

    def attach_mature(self, mature: QObject) -> None:
        """Reclaim the lane that ui_maturity used to reserve for the old icon."""

        self._reclaim_expand_lane()
        timer = getattr(mature, "_timer", None)
        if isinstance(timer, QTimer):
            timer.timeout.connect(self._reclaim_expand_lane)

    def _reclaim_expand_lane(self) -> None:
        for frame in self._expandable_cards:
            layout = frame.layout()
            if layout is None:
                continue
            margins = layout.contentsMargins()
            if margins.right() >= 38:
                right = margins.left() if margins.left() > 0 else 10
                layout.setContentsMargins(margins.left(), margins.top(), right, margins.bottom())

    @staticmethod
    def _blur_pixmap(source: QPixmap) -> QPixmap:
        if source.isNull():
            return source
        width = max(1, int(source.width() * _BACKDROP_SCALE))
        height = max(1, int(source.height() * _BACKDROP_SCALE))
        small = source.scaled(
            width,
            height,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        small.setDevicePixelRatio(1.0)

        item = QGraphicsPixmapItem(small)
        effect = QGraphicsBlurEffect()
        effect.setBlurRadius(_BACKDROP_BLUR_RADIUS)
        item.setGraphicsEffect(effect)
        scene = QGraphicsScene()
        scene.addItem(item)
        result = QPixmap(small.size())
        result.fill(Qt.GlobalColor.transparent)
        painter = QPainter(result)
        scene.render(painter, QRectF(result.rect()), QRectF(small.rect()))
        painter.end()
        scene.removeItem(item)
        item.setGraphicsEffect(None)
        result.setDevicePixelRatio(1.0)
        return result

    def _capture_backdrop(self) -> QPixmap:
        screen = self.window.screen()
        pixmap = QPixmap()
        if screen is not None:
            global_pos = self.root.mapToGlobal(QPoint(0, 0))
            screen_origin = screen.geometry().topLeft()
            local_pos = global_pos - screen_origin
            pixmap = screen.grabWindow(
                0,
                local_pos.x(),
                local_pos.y(),
                self.root.width(),
                self.root.height(),
            )
        if pixmap.isNull():
            pixmap = self.root.grab()
        return self._blur_pixmap(pixmap)

    def _schedule_geometry(self) -> None:
        if not self._geometry_timer.isActive():
            self._geometry_timer.start()

    def _sync_geometry(self) -> None:
        self.backdrop.setGeometry(self.root.rect())
        self.scrim.setGeometry(self.root.rect())
        if self.drawer.isVisible():
            self.drawer.setGeometry(self._drawer_rect())

    def _drawer_rect(self) -> QRect:
        root = self.root.rect()
        available_w = max(320, root.width() - _MODAL_MARGIN * 2)
        available_h = max(320, root.height() - _MODAL_MARGIN * 2)
        ratio_w, ratio_h = self._modal_ratio
        width = min(available_w, max(min(720, available_w), int(root.width() * ratio_w)))
        height = min(available_h, max(min(520, available_h), int(root.height() * ratio_h)))
        return QRect(
            max(_MODAL_MARGIN, (root.width() - width) // 2),
            max(_MODAL_MARGIN, (root.height() - height) // 2),
            width,
            height,
        )

    def _stop_animation(self) -> None:
        animation = self._animation
        self._animation = None
        if animation is not None:
            animation.stop()
            animation.deleteLater()
        self.ghost.hide()

    def _show_prepared_modal(self, *, ratio: tuple[float, float]) -> None:
        self._modal_ratio = ratio
        snapshot = self._capture_backdrop()
        updates_were_enabled = self.root.updatesEnabled()
        if updates_were_enabled:
            self.root.setUpdatesEnabled(False)

        try:
            self.backdrop.setPixmap(snapshot)
            self.backdrop.setGeometry(self.root.rect())
            self.scrim.setGeometry(self.root.rect())
            self.drawer.setGeometry(self._drawer_rect())
            self.body_layout.activate()
            if self.drawer.layout() is not None:
                self.drawer.layout().activate()
            self.scroll.verticalScrollBar().setValue(0)

            self.backdrop.show()
            self.backdrop.raise_()
            self.scrim.show()
            self.scrim.raise_()
            self.drawer.show()
            self.drawer.raise_()
            self.ghost.hide()
        finally:
            if updates_were_enabled:
                self.root.setUpdatesEnabled(True)
                self.root.update()

        self.close_button.setFocus(Qt.FocusReason.OtherFocusReason)
        self._schedule_geometry()

    def open(self, frame: QFrame) -> None:
        if frame not in self._expandable_cards or self.drawer.isVisible():
            return
        self._stop_animation()
        self._selected = frame
        self._populate(frame)
        self._show_prepared_modal(ratio=_DEFAULT_RATIO)

    def open_custom(
        self,
        *,
        title: str,
        eyebrow: str,
        populate: Callable[[], None],
        ratio: tuple[float, float] = _DEFAULT_RATIO,
    ) -> None:
        if self.drawer.isVisible():
            return
        self._stop_animation()
        self._selected = None
        self._clear_body()
        self.title.setText(title)
        self.eyebrow.setText(eyebrow)
        populate()
        self._show_prepared_modal(ratio=ratio)

    def close(self) -> None:
        if not self.drawer.isVisible() and not self.scrim.isVisible():
            return

        self._stop_animation()
        updates_were_enabled = self.root.updatesEnabled()
        if updates_were_enabled:
            self.root.setUpdatesEnabled(False)
        try:
            self.drawer.hide()
            self.ghost.hide()
            self.scrim.hide()
            self.backdrop.hide()
            self.backdrop.clear()
            self._selected = None
            self._modal_ratio = _DEFAULT_RATIO
        finally:
            if updates_were_enabled:
                self.root.setUpdatesEnabled(True)
                self.root.update()

    def _install_real_settings_action(self) -> None:
        toggle = getattr(self.window, "real_settings_toggle", None)
        if not isinstance(toggle, QPushButton):
            return
        try:
            toggle.toggled.disconnect()
        except (RuntimeError, TypeError):
            pass
        try:
            toggle.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        toggle.setCheckable(False)
        toggle.setText("展开设置")
        toggle.clicked.connect(self.open_real_settings)

    @staticmethod
    def _proxy_checkbox(source: QCheckBox) -> QCheckBox:
        clone = QCheckBox(source.text())
        clone.setObjectName("modalCheck")
        clone.setChecked(source.isChecked())
        clone.setEnabled(source.isEnabled())
        clone.setToolTip(source.toolTip())
        clone.toggled.connect(source.setChecked)
        return clone

    def open_real_settings(self, *_args: object) -> None:
        scope_source = getattr(self.window, "real_scope_combo", None)
        save_source = getattr(self.window, "real_save_check", None)
        upload_source = getattr(self.window, "real_upload_check", None)
        pick_source = getattr(self.window, "real_pick_images_button", None)
        count_source = getattr(self.window, "real_image_count", None)
        qc_source = getattr(self.window, "real_qc_check", None)
        policy_source = getattr(self.window, "real_policy_hint", None)
        start_source = getattr(self.window, "real_start_button", None)
        stop_source = getattr(self.window, "real_stop_button", None)
        if not all(
            isinstance(widget, QWidget)
            for widget in (
                scope_source,
                save_source,
                upload_source,
                pick_source,
                count_source,
                qc_source,
                policy_source,
                start_source,
                stop_source,
            )
        ):
            return

        def populate() -> None:
            scope_layout = self._section("执行范围")
            scope_row = QHBoxLayout()
            label = QLabel("填写范围")
            label.setObjectName("modalFieldLabel")
            scope_row.addWidget(label)
            combo = QComboBox()
            combo.setObjectName("modalCombo")
            assert isinstance(scope_source, QComboBox)
            for index in range(scope_source.count()):
                combo.addItem(scope_source.itemText(index), scope_source.itemData(index))
            combo.setCurrentIndex(scope_source.currentIndex())
            combo.setEnabled(scope_source.isEnabled())
            combo.currentIndexChanged.connect(scope_source.setCurrentIndex)
            scope_row.addWidget(combo, 1)
            scope_layout.addLayout(scope_row)

            permissions = self._section("写入与图片授权")
            checks = QHBoxLayout()
            assert isinstance(save_source, QCheckBox)
            assert isinstance(upload_source, QCheckBox)
            assert isinstance(qc_source, QCheckBox)
            save = self._proxy_checkbox(save_source)
            upload = self._proxy_checkbox(upload_source)
            qc = self._proxy_checkbox(qc_source)
            checks.addWidget(save)
            checks.addWidget(upload)
            checks.addWidget(qc)
            checks.addStretch(1)
            permissions.addLayout(checks)

            image_row = QHBoxLayout()
            pick = QPushButton(pick_source.text())
            pick.setObjectName("modalPrimaryButton")
            count = QLabel(count_source.text())
            count.setObjectName("modalMetaLabel")

            def sync_image_controls() -> None:
                count.setText(count_source.text())
                pick.setEnabled(pick_source.isEnabled())
                upload.setChecked(upload_source.isChecked())
                upload.setEnabled(upload_source.isEnabled())

            def choose_images() -> None:
                pick_source.click()
                QTimer.singleShot(0, sync_image_controls)

            pick.setEnabled(pick_source.isEnabled())
            pick.clicked.connect(choose_images)
            upload.toggled.connect(lambda *_: QTimer.singleShot(0, sync_image_controls))
            image_row.addWidget(pick)
            image_row.addWidget(count)
            image_row.addStretch(1)
            permissions.addLayout(image_row)

            safety = self._section("安全策略与执行")
            policy = QLabel(policy_source.text())
            policy.setObjectName("cardDetailText")
            policy.setWordWrap(True)
            safety.addWidget(policy)

            actions = QHBoxLayout()
            actions.addStretch(1)
            start = QPushButton(start_source.text())
            start.setObjectName("modalPrimaryButton")
            stop = QPushButton(stop_source.text())
            stop.setObjectName("modalDangerButton")

            def sync_execution_buttons() -> None:
                start.setEnabled(start_source.isEnabled())
                stop.setEnabled(stop_source.isEnabled())
                combo.setEnabled(scope_source.isEnabled())
                save.setEnabled(save_source.isEnabled())
                qc.setEnabled(qc_source.isEnabled())
                policy.setText(policy_source.text())
                sync_image_controls()

            def start_execution() -> None:
                start_source.click()
                QTimer.singleShot(0, sync_execution_buttons)
                QTimer.singleShot(250, sync_execution_buttons)

            def stop_execution() -> None:
                stop_source.click()
                QTimer.singleShot(0, sync_execution_buttons)
                QTimer.singleShot(250, sync_execution_buttons)

            start.clicked.connect(start_execution)
            stop.clicked.connect(stop_execution)
            actions.addWidget(start)
            actions.addWidget(stop)
            safety.addLayout(actions)
            sync_execution_buttons()
            self.body_layout.addStretch(1)

        self.open_custom(
            title="真实填写设置",
            eyebrow="REAL EXECUTION · SETTINGS",
            populate=populate,
            ratio=_DEFAULT_RATIO,
        )

    @staticmethod
    def _clone_table_widget(source: QTableWidget) -> QTableWidget:
        clone = QTableWidget(source.rowCount(), source.columnCount())
        clone.setObjectName("cardDetailTable")
        clone.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        clone.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        clone.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        clone.setAlternatingRowColors(True)
        clone.verticalHeader().setVisible(False)
        labels: list[str] = []
        for column in range(source.columnCount()):
            item = source.horizontalHeaderItem(column)
            labels.append(item.text() if item is not None else f"Column {column + 1}")
        clone.setHorizontalHeaderLabels(labels)
        clone.setUpdatesEnabled(False)
        for row in range(source.rowCount()):
            for column in range(source.columnCount()):
                item = source.item(row, column)
                target = QTableWidgetItem(item.text() if item is not None else "")
                if item is not None:
                    target.setToolTip(item.toolTip())
                    target.setForeground(item.foreground())
                clone.setItem(row, column, target)
        clone.setUpdatesEnabled(True)
        header = clone.horizontalHeader()
        for column in range(source.columnCount()):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
            clone.setColumnWidth(column, max(90, min(280, source.columnWidth(column))))
        if source.columnCount():
            header.setSectionResizeMode(source.columnCount() - 1, QHeaderView.ResizeMode.Stretch)
        clone.verticalHeader().setDefaultSectionSize(35)
        clone.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        return clone

    @staticmethod
    def _clone_text_view(source: QPlainTextEdit) -> QPlainTextEdit:
        clone = QPlainTextEdit()
        clone.setObjectName("cardDetailTextView")
        clone.setReadOnly(True)
        clone.setLineWrapMode(source.lineWrapMode())
        clone.setPlainText(source.toPlainText())
        clone.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        return clone

    def _clone_console_page(self, source_page: QWidget) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        visible_texts: list[str] = []
        seen: set[str] = set()
        for label in source_page.findChildren(QLabel):
            text = label.text().strip()
            if text and text not in seen:
                seen.add(text)
                visible_texts.append(text)
        if visible_texts:
            hint = QLabel(" · ".join(visible_texts[:6]))
            hint.setObjectName("modalMetaLabel")
            hint.setWordWrap(True)
            layout.addWidget(hint)

        tables = source_page.findChildren(QTableWidget)
        texts = source_page.findChildren(QPlainTextEdit)
        if not tables and not texts:
            empty = QLabel("当前页面暂无可展开的诊断数据。")
            empty.setObjectName("cardDetailText")
            layout.addWidget(empty)
            layout.addStretch(1)
            return page

        for table in tables:
            clone = self._clone_table_widget(table)
            clone.setMinimumHeight(180)
            layout.addWidget(clone, 1)
        for text in texts:
            clone_text = self._clone_text_view(text)
            clone_text.setMinimumHeight(190)
            layout.addWidget(clone_text, 1)
        return page

    def open_console_details(self, *_args: object) -> None:
        console = getattr(self.window, "console", None)
        if not isinstance(console, QFrame):
            return

        def populate() -> None:
            phase_layout = self._section("阶段总览")
            phase_row = QHBoxLayout()
            phase_row.setSpacing(8)
            for unit in getattr(console, "phase_units", {}).values():
                if not isinstance(unit, QFrame):
                    continue
                card = QFrame()
                card.setObjectName("cardDetailSection")
                card_layout = QVBoxLayout(card)
                card_layout.setContentsMargins(10, 8, 10, 8)
                card_layout.setSpacing(2)
                title_source = getattr(unit, "title", None)
                state_source = getattr(unit, "state", None)
                title = QLabel(title_source.text() if isinstance(title_source, QLabel) else "Stage")
                title.setObjectName("cardDetailSectionTitle")
                state = QLabel(state_source.text() if isinstance(state_source, QLabel) else "WAITING")
                state.setObjectName("modalMetaLabel")
                card_layout.addWidget(title)
                card_layout.addWidget(state)
                phase_row.addWidget(card, 1)
            phase_layout.addLayout(phase_row)

            tabs_source = getattr(console, "tabs", None)
            if isinstance(tabs_source, QTabWidget):
                tabs = QTabWidget()
                tabs.setObjectName("modalDetailTabs")
                tabs.setDocumentMode(True)
                tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
                for index in range(tabs_source.count()):
                    source_page = tabs_source.widget(index)
                    if isinstance(source_page, QWidget):
                        tabs.addTab(self._clone_console_page(source_page), tabs_source.tabText(index))
                tabs.setCurrentIndex(min(tabs_source.currentIndex(), max(0, tabs.count() - 1)))
                tabs.setMinimumHeight(360)
                self.body_layout.addWidget(tabs, 1)

        self.open_custom(
            title="运行控制台详情",
            eyebrow="ACCEPTANCE CONTROL CONSOLE · DETAIL",
            populate=populate,
            ratio=_CONSOLE_RATIO,
        )

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        event_type = event.type()
        if watched is self.root:
            if event_type in {QEvent.Type.Resize, QEvent.Type.Show}:
                self._schedule_geometry()
            elif event_type == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
                if event.key() == Qt.Key.Key_Escape and self.drawer.isVisible():
                    self.close()
                    return True
        elif isinstance(watched, QFrame) and watched in self._expandable_cards:
            if event_type in {QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.LayoutRequest}:
                self._schedule_geometry()
            elif event_type == QEvent.Type.MouseButtonRelease and isinstance(event, QMouseEvent):
                if event.button() == Qt.MouseButton.LeftButton and not self.drawer.isVisible():
                    self.open(watched)
                    event.accept()
                    return True
        return False

    def _cleanup(self) -> None:
        self._geometry_timer.stop()
        super()._cleanup()


def install_card_details(window: QMainWindow) -> FastCardDetailController:
    controller = FastCardDetailController(window)
    window._card_details = controller  # type: ignore[attr-defined]
    window.destroyed.connect(controller._cleanup)
    return controller
