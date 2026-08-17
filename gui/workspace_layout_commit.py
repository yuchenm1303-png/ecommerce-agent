from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QLayout, QMainWindow, QStackedWidget, QWidget

from .page_scroll_layout import refresh_single_source_layout


_MAX_LAYOUT_PASSES = 3


def _activate_layout_tree(widget: QWidget) -> bool:
    """Synchronously settle one QWidget subtree without pumping app events."""

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
    """Keep both persistent modeStack pages at the stack's final geometry.

    QStackedWidget hides the inactive page.  Hidden QWidget subtrees can retain
    stale child geometry until the next Show/LayoutRequest turn, which is exactly
    the one-frame reflow that used to leak into the Single/Batch transition.

    We therefore lay out the *hidden* target page before a mode change and keep
    both page roots synchronized whenever modeStack itself is resized.  The
    visible switch still only changes currentIndex; no layout work is triggered by
    currentChanged and no event loop pumping or arbitrary delay is involved.
    """

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self.window = window
        self.stack = getattr(window, "mode_stack", None)
        if not isinstance(self.stack, QStackedWidget):
            raise RuntimeError("workspace layout commit requires installed modeStack")
        self._committing = False
        self.stack.installEventFilter(self)
        window.destroyed.connect(self.cleanup)
        self.prime_all()

    def _commit_batch_responsive(self) -> None:
        workspace = getattr(self.window, "batch_workspace", None)
        responsive = getattr(workspace, "_batch_card_responsive", None)
        commit = getattr(responsive, "commit_now", None)
        if callable(commit):
            commit()

    def _commit_page(self, index: int) -> None:
        page = self.stack.widget(int(index))
        if not isinstance(page, QWidget):
            return

        # QStackedLayout may leave a hidden page at its previous geometry.  Give
        # it the exact stack client rect while it is still hidden, then settle its
        # descendants before it can ever become a visible transition source.
        target_rect = self.stack.contentsRect()
        if page.geometry() != target_rect:
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

    def prepare_page(self, index: int) -> None:
        """Pre-layout one target page without changing which mode is visible."""

        if self._committing:
            return
        if int(index) < 0 or int(index) >= self.stack.count():
            return
        self._committing = True
        try:
            self._commit_page(int(index))
        finally:
            self._committing = False

    def prime_all(self) -> None:
        """Synchronize every persistent workspace to the current stack rect."""

        if self._committing:
            return
        self._committing = True
        try:
            for index in range(self.stack.count()):
                self._commit_page(index)
        finally:
            self._committing = False

    def commit_current(self, _index: int | None = None) -> None:
        """Compatibility entry used by the startup entrance stability gate."""

        self.prepare_page(int(self.stack.currentIndex()))

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self.stack and event.type() in {
            QEvent.Type.Resize,
            QEvent.Type.Show,
        }:
            # Resize is a legitimate global reflow point.  Synchronize the hidden
            # sibling here so the next mode change itself never has to reflow it.
            self.prime_all()
        return False

    def cleanup(self) -> None:
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
