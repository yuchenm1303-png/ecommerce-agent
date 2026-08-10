from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtWidgets import QFrame, QMainWindow, QPushButton, QSplitter, QTabWidget, QWidget


_SUMMARY_MIN = 300
_SUMMARY_MAX = 460
_DETAIL_MIN = 460
_DETAIL_MAX = 620
_COALESCE_MS = 40


class ConsoleSummaryMode(QObject):
    """Keep the old expanded console as the permanent compact/default state.

    The former ~112 px collapsed strip is removed from the interaction model.
    Phase cards, tabs and the active console viewport stay visible at all times.
    The header button now switches between this rich summary state and a larger
    diagnostic state. Both transitions are atomic splitter changes; no layout
    animation is involved.
    """

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self.window = window
        self.root = window.centralWidget()
        self.console = getattr(window, "console", None)
        self.toggle = getattr(window, "console_detail_toggle", None)
        self.body = getattr(window, "_ui_polish_body_splitter", None)
        self._detail_open = False
        self._mature_apply = None

        if not isinstance(self.root, QWidget):
            raise RuntimeError("console summary mode requires a central widget")
        if not isinstance(self.console, QFrame):
            raise RuntimeError("console summary mode requires the acceptance console")
        if not isinstance(self.toggle, QPushButton):
            raise RuntimeError("console summary mode requires console_detail_toggle")
        if not isinstance(self.body, QSplitter):
            raise RuntimeError("console summary mode requires bodySplitter")

        # ui_polish used toggled() to hide phase cards/tabs and collapse the
        # console to ~112 px. ui_maturity also listened to the same signal.
        # Remove both old state transitions and keep the button logically
        # checked so ui_maturity always regards the console as usable/expanded.
        try:
            self.toggle.toggled.disconnect()
        except (RuntimeError, TypeError):
            pass
        self.toggle.setCheckable(True)
        self.toggle.setChecked(True)
        self.toggle.setText("展开详情 ⌄")
        self.toggle.clicked.connect(self._toggle_detail)

        for unit in getattr(self.console, "phase_units", {}).values():
            if isinstance(unit, QWidget):
                unit.show()
        tabs = getattr(self.console, "tabs", None)
        if isinstance(tabs, QTabWidget):
            tabs.show()

        # MatureResponsiveController still owns the rest of the responsive
        # workspace. Wrap only its timeout callback so our console geometry is
        # applied immediately after its normal sizing pass, never in competition
        # with it on a later frame.
        mature = getattr(window, "_mature_ui", None)
        timer = getattr(mature, "_timer", None)
        apply = getattr(mature, "apply", None)
        if isinstance(timer, QTimer) and callable(apply):
            self._mature_apply = apply
            try:
                timer.timeout.disconnect()
            except (RuntimeError, TypeError):
                pass
            timer.timeout.connect(self._apply_after_mature)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(_COALESCE_MS)
        self._timer.timeout.connect(self.apply)
        self.root.installEventFilter(self)
        window.destroyed.connect(self.cleanup)

        QTimer.singleShot(0, self.apply)

    @staticmethod
    def _set_sizes_if_needed(splitter: QSplitter, target: list[int]) -> None:
        current = splitter.sizes()
        if len(current) != len(target) or any(abs(a - b) > 3 for a, b in zip(current, target)):
            splitter.setSizes(target)

    def _summary_target(self, available: int) -> int:
        target = min(440, max(340, available - 300))
        return min(target, max(_SUMMARY_MIN, available - 260))

    def _detail_target(self, available: int) -> int:
        target = min(590, max(480, available - 220))
        return min(target, max(420, available - 220))

    def apply(self) -> None:
        available = max(1, self.body.height() - self.body.handleWidth())
        if self._detail_open:
            self.console.setMinimumHeight(_DETAIL_MIN)
            self.console.setMaximumHeight(_DETAIL_MAX)
            target = self._detail_target(available)
            workspace_min = 220
        else:
            self.console.setMinimumHeight(_SUMMARY_MIN)
            self.console.setMaximumHeight(_SUMMARY_MAX)
            target = self._summary_target(available)
            workspace_min = 260

        self._set_sizes_if_needed(
            self.body,
            [max(workspace_min, available - target), target],
        )

    def _apply_after_mature(self) -> None:
        if callable(self._mature_apply):
            self._mature_apply()
        self.apply()

    def _toggle_detail(self, *_args: object) -> None:
        # QAbstractButton flips its checked state before clicked(). Restore the
        # internal checked bit immediately so legacy responsive code can never
        # reinterpret the rich summary as the old tiny collapsed state.
        if not self.toggle.isChecked():
            self.toggle.setChecked(True)
        self._detail_open = not self._detail_open
        self.toggle.setText("收起详情 ⌃" if self._detail_open else "展开详情 ⌄")
        self.apply()

    def schedule(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self.root and event.type() in {
            QEvent.Type.Resize,
            QEvent.Type.Show,
            QEvent.Type.LayoutRequest,
        }:
            self.schedule()
        return False

    def cleanup(self) -> None:
        self._timer.stop()
        try:
            self.root.removeEventFilter(self)
        except RuntimeError:
            pass


def install_console_summary_mode(window: QMainWindow) -> ConsoleSummaryMode:
    controller = ConsoleSummaryMode(window)
    window._console_summary_mode = controller  # type: ignore[attr-defined]
    return controller
