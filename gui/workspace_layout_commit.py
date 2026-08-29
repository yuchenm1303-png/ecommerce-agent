from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QTimer, Qt
from PySide6.QtWidgets import QLayout, QMainWindow, QStackedWidget, QWidget

from .page_scroll_layout import refresh_single_source_layout


_MAX_LAYOUT_PASSES = 3


def _activate_layout_tree(widget: QWidget) -> bool:
    """Settle one persistent page subtree without pumping the Qt event loop."""

    changed = False
    try:
        widget.ensurePolished()
        layout = widget.layout()
        if isinstance(layout, QLayout):
            layout.invalidate()
            changed = bool(layout.activate()) or changed
    except RuntimeError:
        return changed

    try:
        children = widget.findChildren(
            QWidget,
            "",
            Qt.FindChildOption.FindDirectChildrenOnly,
        )
    except (RuntimeError, TypeError):
        return changed

    for child in children:
        changed = _activate_layout_tree(child) or changed
    return changed


class WorkspaceLayoutCommitter(QObject):
    """Own finite QWidget -> Quick geometry commits at real layout boundaries.

    Normal runtime state changes are not layout transactions. Only page changes and
    native resize/show boundaries settle the QWidget layout tree before Quick reads
    geometry. Batch job-state publishing no longer forces recursive layout activation;
    the Quick-owned Batch ListView renders job rows directly from the Batch model.
    """

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self.window = window
        self.stack = getattr(window, "mode_stack", None)
        if not isinstance(self.stack, QStackedWidget):
            raise RuntimeError("workspace layout owner requires installed modeStack")
        self._committing = False

        self._settle_timer = QTimer(self)
        self._settle_timer.setSingleShot(True)
        self._settle_timer.setInterval(0)
        self._settle_timer.timeout.connect(self._settle_current_page)

        self.stack.installEventFilter(self)
        self.stack.currentChanged.connect(self.commit_current)
        window.destroyed.connect(self.cleanup)

        self.prepare_page(int(self.stack.currentIndex()))

    def _commit_single_fixed(self) -> None:
        summary = getattr(self.window, "_console_summary_mode", None)
        apply = getattr(summary, "apply", None)
        if callable(apply):
            try:
                apply()
            except RuntimeError:
                pass

    def _commit_batch_responsive(self) -> None:
        workspace = getattr(self.window, "batch_workspace", None)
        responsive = getattr(workspace, "_batch_card_responsive", None)
        commit = getattr(responsive, "commit_now", None)
        if callable(commit):
            try:
                commit()
            except RuntimeError:
                pass

    def _publish_committed_geometry(self) -> None:
        controller = getattr(self.window, "_static_qml_view_controller", None)
        bridge = getattr(controller, "bridge", None)
        schedule_refresh = getattr(bridge, "schedule_refresh", None)
        if callable(schedule_refresh):
            try:
                schedule_refresh()
            except RuntimeError:
                pass

        activity = getattr(controller, "activity_presence", None)
        schedule_activity = getattr(activity, "schedule_refresh", None)
        if callable(schedule_activity):
            try:
                schedule_activity()
            except RuntimeError:
                pass

        quick_batch = getattr(controller, "quick_batch", None)
        refresh_batch = getattr(quick_batch, "refresh_geometry", None)
        if callable(refresh_batch):
            try:
                refresh_batch()
            except RuntimeError:
                pass

    def _commit_page(self, index: int) -> None:
        page = self.stack.widget(int(index))
        if not isinstance(page, QWidget):
            return

        target_rect = self.stack.contentsRect()
        if target_rect.width() > 0 and target_rect.height() > 0 and page.geometry() != target_rect:
            page.setGeometry(target_rect)

        if int(index) == 0:
            refresh_single_source_layout(self.window)

        for _pass in range(_MAX_LAYOUT_PASSES):
            changed = _activate_layout_tree(page)
            if int(index) == 1:
                self._commit_batch_responsive()
                changed = _activate_layout_tree(page) or changed
            if not changed:
                break

        if int(index) == 0:
            self._commit_single_fixed()
            _activate_layout_tree(page)

    def prepare_page(self, index: int) -> None:
        if self._committing:
            return
        if int(index) < 0 or int(index) >= self.stack.count():
            return
        self._committing = True
        try:
            self._commit_page(int(index))
        finally:
            self._committing = False
        self._publish_committed_geometry()

    def commit_current(self, _index: int | None = None) -> None:
        self.prepare_page(int(self.stack.currentIndex()))
        self._schedule_current_settle()

    def _schedule_current_settle(self) -> None:
        if self._committing or self._settle_timer.isActive():
            return
        self._settle_timer.start()

    def _settle_current_page(self) -> None:
        if self._committing:
            return
        self.prepare_page(int(self.stack.currentIndex()))

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self.stack and event.type() in {QEvent.Type.Resize, QEvent.Type.Show}:
            self._schedule_current_settle()
        return False

    def cleanup(self) -> None:
        self._settle_timer.stop()
        try:
            self.stack.currentChanged.disconnect(self.commit_current)
        except (RuntimeError, TypeError):
            pass
        try:
            self.stack.removeEventFilter(self)
        except RuntimeError:
            pass


def install_workspace_layout_commit(window: QMainWindow) -> WorkspaceLayoutCommitter:
    existing = getattr(window, "_workspace_layout_commit", None)
    if isinstance(existing, WorkspaceLayoutCommitter):
        return existing
    controller = WorkspaceLayoutCommitter(window)
    window._workspace_layout_commit = controller  # type: ignore[attr-defined]
    return controller


__all__ = ["WorkspaceLayoutCommitter", "install_workspace_layout_commit"]
