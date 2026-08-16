from __future__ import annotations

from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import QLayout, QMainWindow, QStackedWidget, QWidget

from .page_scroll_layout import refresh_single_source_layout


_MAX_LAYOUT_PASSES = 3


def _activate_layout_tree(widget: QWidget) -> bool:
    """Synchronously commit one QWidget subtree without pumping application events.

    Qt normally coalesces LayoutRequest delivery. That is fine for ordinary
    interactive resizing, but presentation snapshots must never sample the tree
    between a QStackedWidget page switch and the deferred layout pass. Activate
    the already-owned layouts directly instead of calling processEvents(), which
    could re-enter business slots while a transition is in progress.
    """

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
    """Single authority for final QWidget geometry before snapshots/handoffs.

    Single/Batch pages are deliberately persistent siblings in ``modeStack``.
    Hidden pages may therefore carry deferred LayoutRequest work. The visual
    transition and startup entrance are allowed to cache pixels only after this
    object has synchronously committed the newly current page.
    """

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self.window = window
        self.stack = getattr(window, "mode_stack", None)
        if not isinstance(self.stack, QStackedWidget):
            raise RuntimeError("workspace layout commit requires installed modeStack")
        self._committing = False
        self.stack.currentChanged.connect(self.commit_current)
        self.commit_current()

    def _commit_batch_responsive(self) -> None:
        workspace = getattr(self.window, "batch_workspace", None)
        responsive = getattr(workspace, "_batch_card_responsive", None)
        commit = getattr(responsive, "commit_now", None)
        if callable(commit):
            commit()

    def commit_current(self, _index: int | None = None) -> None:
        if self._committing:
            return
        page = self.stack.currentWidget()
        if not isinstance(page, QWidget):
            return

        self._committing = True
        try:
            # The stack's private QStackedLayout owns current-page geometry.
            # Activate it first so the page receives the final viewport rect.
            stack_layout = self.stack.layout()
            if isinstance(stack_layout, QLayout):
                stack_layout.invalidate()
                stack_layout.activate()

            if int(self.stack.currentIndex()) == 0:
                # Optional Single rows are installed after the original fixed
                # layout setup. Reflow them against the current, final page size.
                refresh_single_source_layout(self.window)
            else:
                # Batch URL/job cards have a viewport-owned width contract. Run
                # that calculation synchronously instead of waiting for its
                # coalesced zero-delay refresh while a snapshot is being taken.
                self._commit_batch_responsive()

            # A parent activation can change a splitter/scroll viewport which in
            # turn changes a descendant sizeHint. A small bounded fixed-point
            # loop gives nested layouts their final geometry in this same GUI
            # turn, with no arbitrary sleep and no processEvents re-entry.
            for _pass in range(_MAX_LAYOUT_PASSES):
                changed = _activate_layout_tree(page)
                if int(self.stack.currentIndex()) == 1:
                    self._commit_batch_responsive()
                if not changed:
                    break

            # Intentionally do not call updateGeometry() here. That API notifies
            # the parent layout and can queue fresh LayoutRequest work after this
            # barrier, recreating the exact one-frame reflow this class prevents.
        finally:
            self._committing = False


def install_workspace_layout_commit(window: QMainWindow) -> WorkspaceLayoutCommitter:
    existing = getattr(window, "_workspace_layout_commit", None)
    if isinstance(existing, WorkspaceLayoutCommitter):
        return existing
    controller = WorkspaceLayoutCommitter(window)
    window._workspace_layout_commit = controller  # type: ignore[attr-defined]
    return controller


__all__ = ["WorkspaceLayoutCommitter", "install_workspace_layout_commit"]
