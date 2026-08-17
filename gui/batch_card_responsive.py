from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QLabel, QScrollArea, QSizePolicy, QWidget


class BatchCardResponsiveController(QObject):
    """Keep owned Batch job cards constrained to the visible scroll viewport.

    Width ownership is synchronous.  In particular, showing a previously hidden
    Batch workspace must not expose the QScrollArea's provisional child width and
    correct it one event-loop turn later; that delayed correction was visible as a
    one-frame reflow during Single/Batch transitions.
    """

    def __init__(self, workspace: QWidget) -> None:
        super().__init__(workspace)
        self.workspace = workspace
        self.scroll = getattr(workspace, "job_scroll", None)
        self.jobs_host = getattr(workspace, "jobs_host", None)
        self.jobs_layout = getattr(workspace, "jobs_layout", None)
        self.viewport = self.scroll.viewport() if isinstance(self.scroll, QScrollArea) else None

        if self.viewport is None or not isinstance(self.jobs_host, QWidget):
            return

        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.jobs_host.setMinimumWidth(0)
        self.jobs_host.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )
        self.viewport.installEventFilter(self)
        self.jobs_host.installEventFilter(self)

        controller = getattr(workspace, "controller", None)
        jobs_changed = getattr(controller, "jobs_changed", None)
        if jobs_changed is not None and hasattr(jobs_changed, "connect"):
            # BatchWorkspace._apply_jobs is connected before this controller is
            # installed, so the card tree already exists when this slot runs.
            jobs_changed.connect(lambda _jobs: self.commit_now())

        self.commit_now()

    @staticmethod
    def _soft_horizontal(widget: QWidget | None) -> None:
        if widget is None:
            return
        widget.setMinimumWidth(0)
        widget.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            widget.sizePolicy().verticalPolicy(),
        )

    def _apply_card_constraints(self, card: QWidget) -> None:
        card.setMinimumWidth(0)
        card.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            card.sizePolicy().verticalPolicy(),
        )

        # These labels can contain unbounded supplier/log/product text.  Their
        # sizeHint must not become the minimum width of the entire Job card.
        for name in (
            "product_label",
            "url_label",
            "phase_label",
            "meta_label",
            "detail_label",
            "error_label",
            "log_preview",
            "details_meta",
        ):
            widget = getattr(card, name, None)
            if isinstance(widget, QWidget):
                self._soft_horizontal(widget)

        product = getattr(card, "product_label", None)
        if isinstance(product, QLabel):
            product.setWordWrap(True)

        meta = getattr(card, "meta_label", None)
        if isinstance(meta, QLabel):
            meta.setWordWrap(True)

        detail = getattr(card, "detail_label", None)
        if isinstance(detail, QLabel):
            detail.setWordWrap(True)

        # Per-job controls are injected after the card itself is built.  Keep
        # their descriptive hint flexible so the fixed action buttons stay visible.
        controls_manager = getattr(self.workspace, "_batch_job_controls", None)
        controls_map = getattr(controls_manager, "_controls", None)
        job_id = str(getattr(card, "job_id", ""))
        if isinstance(controls_map, dict):
            controls = controls_map.get(job_id)
            host = getattr(controls, "host", None)
            hint = getattr(controls, "hint", None)
            if isinstance(host, QWidget):
                host.setMinimumWidth(0)
                host.setSizePolicy(
                    QSizePolicy.Policy.Expanding,
                    host.sizePolicy().verticalPolicy(),
                )
            if isinstance(hint, QWidget):
                self._soft_horizontal(hint)

    def _elide_url(self, card: QWidget) -> None:
        label = getattr(card, "url_label", None)
        job = getattr(card, "_job", None)
        url = str(getattr(job, "product_url", "") or "")
        if not isinstance(label, QLabel) or not url:
            return

        available = int(label.width())
        if available <= 40:
            available = max(80, int(card.width()) - 36)
        preview = label.fontMetrics().elidedText(
            url,
            Qt.TextElideMode.ElideMiddle,
            max(80, available),
        )
        label.setText(preview)
        label.setToolTip(url)

    def _sync_width(self) -> None:
        if self.viewport is None or not isinstance(self.jobs_host, QWidget):
            return
        viewport_width = max(1, int(self.viewport.width()))

        # QScrollArea(widgetResizable=True) normally performs this resize itself,
        # but a child's enormous minimumSizeHint can override it.  Bind the host
        # to the real viewport synchronously, before any visible frame is sampled.
        self.jobs_host.setMaximumWidth(viewport_width)
        if self.jobs_host.width() != viewport_width:
            self.jobs_host.resize(viewport_width, self.jobs_host.height())

        content_width = viewport_width
        if self.jobs_layout is not None:
            margins = self.jobs_layout.contentsMargins()
            content_width -= margins.left() + margins.right()
        content_width = max(1, content_width)

        cards = getattr(self.workspace, "_job_cards", {})
        if not isinstance(cards, dict):
            return
        for card in cards.values():
            if not isinstance(card, QWidget):
                continue
            self._apply_card_constraints(card)
            card.setMaximumWidth(content_width)
            self._elide_url(card)

    def commit_now(self) -> None:
        """Synchronously bind Batch cards to the current viewport geometry."""

        try:
            self._sync_width()
        except RuntimeError:
            return

    def schedule_refresh(self) -> None:
        """Compatibility alias: width refreshes are intentionally never deferred."""

        self.commit_now()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched in {self.viewport, self.jobs_host} and event.type() in {
            QEvent.Type.Resize,
            QEvent.Type.Show,
        }:
            # Show is especially important: a hidden QScrollArea can report a
            # provisional child width. Correct it in the same event, not with a
            # zero-delay timer after the transition has already captured pixels.
            self.commit_now()
        return False


def install_batch_card_responsive(workspace: QWidget) -> BatchCardResponsiveController:
    existing = getattr(workspace, "_batch_card_responsive", None)
    if isinstance(existing, BatchCardResponsiveController):
        return existing
    controller = BatchCardResponsiveController(workspace)
    setattr(workspace, "_batch_card_responsive", controller)
    return controller


__all__ = ["BatchCardResponsiveController", "install_batch_card_responsive"]
