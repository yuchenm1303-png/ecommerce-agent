from __future__ import annotations

from types import MethodType

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import (
    QBoxLayout,
    QFrame,
    QLayout,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


_SUMMARY_MIN = 292
_SUMMARY_MAX = 336
_SUMMARY_TARGET = 310
_WORKSPACE_MIN = 220
_CONSOLE_TABS_MIN = 146
_CONSOLE_TABS_MAX = 190
_PHASE_UNIT_MIN_WIDTH = 132
_PHASE_UNIT_MIN_HEIGHT = 38
_PHASE_UNIT_MAX_HEIGHT = 42
_SIDE_MIN = 360
_SIDE_MAX = 480
_SIDE_RATIO = 0.29
_COALESCE_MS = 16


class ConsoleSummaryMode(QObject):
    """Single-owner geometry controller for the fixed Single workspace.

    The mature presentation layer still owns typography/style, but its historical
    responsive splitter sizing sleeps while Single is active. This controller also
    owns the compact indicator composition: phase state belongs in the title row,
    leaving the card's vertical budget to live console/timeline content.
    """

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self.window = window
        self.root = window.centralWidget()
        self.console = getattr(window, "console", None)
        self.toggle = getattr(window, "console_detail_toggle", None)
        self.body = getattr(window, "_ui_polish_body_splitter", None)
        self.details = getattr(window, "_card_details", None)
        self._last_geometry_signature: tuple[int, ...] | None = None
        self._mature_timer: QTimer | None = None
        self._mature_original_schedule = None
        self._mature_original_apply = None
        self._layout_compacted = False

        if not isinstance(self.root, QWidget):
            raise RuntimeError("console summary mode requires a central widget")
        if not isinstance(self.console, QFrame):
            raise RuntimeError("console summary mode requires the acceptance console")
        if not isinstance(self.toggle, QPushButton):
            raise RuntimeError("console summary mode requires console_detail_toggle")
        if not isinstance(self.body, QSplitter):
            raise RuntimeError("console summary mode requires the fixed bodySplitter")
        if self.details is None or not hasattr(self.details, "open_console_details"):
            raise RuntimeError("console summary mode requires the shared glass detail controller")

        # Fixed Single never expands/collapses its main splitter. Detailed console
        # inspection stays in the existing modal, so this button is a plain action.
        try:
            self.toggle.toggled.disconnect()
        except (RuntimeError, TypeError):
            pass
        try:
            self.toggle.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        self.toggle.setCheckable(False)
        self.toggle.setEnabled(True)
        self.toggle.show()
        self.toggle.setText("展开详情")
        self.toggle.clicked.connect(self._open_detail)

        self._compact_indicator_layout()

        tabs = getattr(self.console, "tabs", None)
        if isinstance(tabs, QTabWidget):
            tabs.show()
            tabs.setMinimumHeight(_CONSOLE_TABS_MIN)
            tabs.setMaximumHeight(_CONSOLE_TABS_MAX)
            tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            for index in range(tabs.count()):
                page = tabs.widget(index)
                page_layout = page.layout() if isinstance(page, QWidget) else None
                if isinstance(page_layout, QBoxLayout):
                    page_layout.setContentsMargins(5, 4, 5, 5)
                    page_layout.setSpacing(4)

        # This is the performance boundary: only one controller may own the fixed
        # Single splitters. The older maturity controller remains available for
        # Batch, but cannot enqueue work while Single is active.
        self._install_mature_single_fast_path()

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(_COALESCE_MS)
        self._timer.timeout.connect(self.apply)

        # Deliberately do not observe LayoutRequest. Field/log/progress changes can
        # emit it continuously and none of those events change fixed-page geometry.
        self.root.installEventFilter(self)
        self.body.installEventFilter(self)
        window.destroyed.connect(self.cleanup)

        QTimer.singleShot(0, self._bind_mode_stack)
        QTimer.singleShot(0, self.apply)

    @staticmethod
    def _find_layout_containing(layout: QLayout, widget: QWidget) -> QLayout | None:
        for index in range(layout.count()):
            item = layout.itemAt(index)
            if item.widget() is widget:
                return layout
            child = item.layout()
            if isinstance(child, QLayout):
                found = ConsoleSummaryMode._find_layout_containing(child, widget)
                if found is not None:
                    return found
        return None

    @staticmethod
    def _detach_child_layout(parent: QLayout, child: QLayout) -> bool:
        for index in range(parent.count()):
            item = parent.itemAt(index)
            nested = item.layout()
            if nested is child:
                parent.takeAt(index)
                return True
            if isinstance(nested, QLayout) and ConsoleSummaryMode._detach_child_layout(
                nested, child
            ):
                return True
        return False

    def _compact_indicator_layout(self) -> None:
        if self._layout_compacted:
            return

        root_layout = self.console.layout()
        if not isinstance(root_layout, QVBoxLayout) or root_layout.count() == 0:
            raise RuntimeError("acceptance console requires a vertical root layout")

        header = root_layout.itemAt(0).layout()
        if not isinstance(header, QBoxLayout):
            raise RuntimeError("acceptance console requires a horizontal header layout")

        units = [
            unit
            for unit in getattr(self.console, "phase_units", {}).values()
            if isinstance(unit, QWidget)
        ]
        if not units:
            raise RuntimeError("acceptance console phase units are missing")

        source_layout = self._find_layout_containing(root_layout, units[0])
        if source_layout is None or source_layout is header:
            raise RuntimeError("acceptance console phase strip is not independently owned")

        # The old header spacer separated title from metrics because phases lived on
        # their own row. Replace that dead gap with the four compact stage cards.
        if header.count() > 1 and header.itemAt(1).spacerItem() is not None:
            header.takeAt(1)

        title_layout = header.itemAt(0).layout()
        if isinstance(title_layout, QBoxLayout):
            title_layout.setSpacing(0)

        header.setSpacing(7)
        insert_at = 1
        for unit in units:
            source_layout.removeWidget(unit)
            unit_layout = unit.layout()
            if isinstance(unit_layout, QBoxLayout):
                unit_layout.setContentsMargins(9, 4, 9, 5)
                unit_layout.setSpacing(0)

            detail = getattr(unit, "detail", None)
            if isinstance(detail, QWidget):
                detail.hide()

            unit.setMinimumWidth(_PHASE_UNIT_MIN_WIDTH)
            unit.setMinimumHeight(_PHASE_UNIT_MIN_HEIGHT)
            unit.setMaximumHeight(_PHASE_UNIT_MAX_HEIGHT)
            unit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            unit.show()
            header.insertWidget(
                insert_at,
                unit,
                1,
                Qt.AlignmentFlag.AlignVCenter,
            )
            insert_at += 1

        # Remove the now-empty phase row entirely. This is the vertical-space win:
        # header + status + progress + tabs are the only remaining summary bands.
        self._detach_child_layout(root_layout, source_layout)
        root_layout.setContentsMargins(13, 8, 13, 10)
        root_layout.setSpacing(6)
        self._layout_compacted = True

    def _bind_mode_stack(self) -> None:
        stack = getattr(self.window, "mode_stack", None)
        signal = getattr(stack, "currentChanged", None)
        if signal is None or not hasattr(signal, "connect"):
            return
        try:
            signal.connect(self._on_mode_changed)
        except (RuntimeError, TypeError):
            pass

    def _single_active(self) -> bool:
        stack = getattr(self.window, "mode_stack", None)
        if stack is None:
            return True
        try:
            return int(stack.currentIndex()) == 0
        except RuntimeError:
            return True

    def _on_mode_changed(self, index: int) -> None:
        self._last_geometry_signature = None
        if int(index) == 0:
            self.schedule()
            return
        # Batch keeps the original mature responsive behavior. Switching modes is
        # the only bridge needed to wake that controller after Single suppressed it.
        if callable(self._mature_original_schedule):
            self._mature_original_schedule()

    def _install_mature_single_fast_path(self) -> None:
        """Sleep the old responsive geometry owner while Single is visible."""
        mature = getattr(self.window, "_mature_ui", None)
        timer = getattr(mature, "_timer", None)
        original_schedule = getattr(mature, "schedule", None)
        original_apply = getattr(mature, "apply", None)
        if mature is None or not isinstance(timer, QTimer):
            return
        if not callable(original_schedule) or not callable(original_apply):
            return

        self._mature_timer = timer
        self._mature_original_schedule = original_schedule
        self._mature_original_apply = original_apply
        timer.stop()

        try:
            timer.timeout.disconnect()
        except (RuntimeError, TypeError):
            pass
        timer.timeout.connect(self._dispatch_mature_apply)

        controller = self

        def fixed_aware_schedule(_mature) -> None:  # noqa: ANN001
            if controller._single_active():
                return
            original_schedule()

        mature.schedule = MethodType(fixed_aware_schedule, mature)  # type: ignore[method-assign]

    def _dispatch_mature_apply(self) -> None:
        if self._single_active():
            return
        if callable(self._mature_original_apply):
            self._mature_original_apply()

    @staticmethod
    def _set_sizes_if_needed(splitter: QSplitter, target: list[int]) -> bool:
        current = splitter.sizes()
        if len(current) == len(target) and all(
            abs(a - b) <= 3 for a, b in zip(current, target)
        ):
            return False
        splitter.setSizes(target)
        return True

    def _workspace_splitter(self) -> QSplitter | None:
        value = self.root.findChild(QSplitter, "workspaceSplitter")
        return value if isinstance(value, QSplitter) else None

    def _geometry_signature(self) -> tuple[int, ...]:
        splitter = self._workspace_splitter()
        return (
            int(self.root.width()),
            int(self.root.height()),
            int(self.body.width()),
            int(self.body.height()),
            int(splitter.width()) if splitter is not None else 0,
            int(splitter.height()) if splitter is not None else 0,
        )

    def _apply_workspace_width(self) -> bool:
        splitter = self._workspace_splitter()
        if not isinstance(splitter, QSplitter) or splitter.count() < 2:
            return False
        total = max(1, splitter.width() - splitter.handleWidth())
        side_target = min(
            _SIDE_MAX,
            max(_SIDE_MIN, round(total * _SIDE_RATIO)),
        )
        side = splitter.widget(1)
        if isinstance(side, QWidget):
            side.setMinimumWidth(_SIDE_MIN)
            side.setMaximumWidth(_SIDE_MAX)
        return self._set_sizes_if_needed(
            splitter,
            [max(620, total - side_target), side_target],
        )

    def _apply_body_height(self) -> bool:
        available = max(1, self.body.height() - self.body.handleWidth())
        # The compact indicator no longer spends a full row on phase cards, so the
        # same outer height now yields materially more live log/timeline content.
        proportional = round(available * 0.52)
        target = min(
            _SUMMARY_MAX,
            max(
                _SUMMARY_MIN,
                min(proportional, max(0, available - _WORKSPACE_MIN)),
            ),
        )
        return self._set_sizes_if_needed(
            self.body,
            [max(_WORKSPACE_MIN, available - target), target],
        )

    def _reposition_expand_buttons(self) -> None:
        for button in self.window.findChildren(QToolButton, "cardExpandButton"):
            parent = button.parentWidget()
            if parent is None:
                continue
            target_x = max(5, parent.width() - 27)
            if button.x() != target_x or button.y() != 7:
                button.move(target_x, 7)
            button.raise_()

    def apply(self) -> None:
        if not self._single_active():
            return

        self._compact_indicator_layout()
        signature = self._geometry_signature()
        if signature == self._last_geometry_signature:
            return
        self._last_geometry_signature = signature

        self.console.setMinimumHeight(_SUMMARY_MIN)
        self.console.setMaximumHeight(_SUMMARY_MAX)

        tabs = getattr(self.console, "tabs", None)
        if isinstance(tabs, QTabWidget):
            tabs.setMinimumHeight(_CONSOLE_TABS_MIN)
            tabs.setMaximumHeight(_CONSOLE_TABS_MAX)

        # setSizes() itself causes the real glass cards to emit Move/Resize, which
        # already feeds the native background's coalesced geometry timer. Do not
        # schedule another full-window mask update from this controller.
        self._apply_workspace_width()
        self._apply_body_height()
        self._reposition_expand_buttons()

    def _open_detail(self, *_args: object) -> None:
        self.toggle.setText("展开详情")
        self.details.open_console_details()

    def schedule(self) -> None:
        if not self._single_active():
            return
        if not self._timer.isActive():
            self._timer.start()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched in {self.root, self.body} and event.type() in {
            QEvent.Type.Resize,
            QEvent.Type.Show,
        }:
            self.schedule()
        return False

    def cleanup(self) -> None:
        self._timer.stop()
        for watched in (self.root, self.body):
            try:
                watched.removeEventFilter(self)
            except RuntimeError:
                pass


def install_console_summary_mode(window: QMainWindow) -> ConsoleSummaryMode:
    controller = ConsoleSummaryMode(window)
    window._console_summary_mode = controller  # type: ignore[attr-defined]
    return controller
