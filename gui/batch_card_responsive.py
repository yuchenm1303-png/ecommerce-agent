from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QLabel, QScrollArea, QSizePolicy, QWidget


class BatchCardResponsiveController(QObject):
    """Synchronously bind Batch job-card geometry to the visible scroll viewport.

    Width and vertical content extent are layout ownership, not deferred presentation
    work. A hidden Batch page must already have its final card widths and scroll range
    before QStackedWidget exposes it; there is intentionally no zero-delay timer here.
    """

    def __init__(self, workspace: QWidget) -> None:
        super().__init__(workspace)
        self.workspace = workspace
        self.scroll = getattr(workspace, "job_scroll", None)
        self.jobs_host = getattr(workspace, "jobs_host", None)
        self.jobs_layout = getattr(workspace, "jobs_layout", None)
        self.viewport = self.scroll.viewport() if isinstance(self.scroll, QScrollArea) else None
        self._committing = False

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

    def _content_height(self) -> int:
        if self.viewport is None or self.jobs_layout is None:
            return max(1, int(self.jobs_host.height()))
        try:
            self.jobs_layout.invalidate()
            self.jobs_layout.activate()
            layout_height = int(self.jobs_layout.sizeHint().height())
            viewport_height = int(self.viewport.height())
        except RuntimeError:
            return max(1, int(self.jobs_host.height()))
        return max(1, viewport_height, layout_height)

    def _sync_geometry(self) -> None:
        if self.viewport is None or not isinstance(self.jobs_host, QWidget):
            return
        viewport_width = max(1, int(self.viewport.width()))

        self.jobs_host.setMaximumWidth(viewport_width)

        content_width = viewport_width
        if self.jobs_layout is not None:
            margins = self.jobs_layout.contentsMargins()
            content_width -= margins.left() + margins.right()
        content_width = max(1, content_width)

        cards = getattr(self.workspace, "_job_cards", {})
        if isinstance(cards, dict):
            for card in cards.values():
                if not isinstance(card, QWidget):
                    continue
                self._apply_card_constraints(card)
                card.setMaximumWidth(content_width)
                self._elide_url(card)

        # QScrollArea normally updates a widgetResizable child's vertical extent
        # through deferred LayoutRequest/resize events. The hidden QWidget tree is
        # now a business/layout host behind Quick, so waiting for Expose/minimize is
        # not a valid geometry boundary. Commit the content extent synchronously from
        # the authoritative layout sizeHint and make the scrollbar range real now.
        content_height = self._content_height()
        self.jobs_host.setMinimumHeight(content_height)
        if self.jobs_host.width() != viewport_width or self.jobs_host.height() != content_height:
            self.jobs_host.resize(viewport_width, content_height)

    def commit_now(self) -> None:
        """Commit viewport/card geometry in the same GUI turn that owns the layout."""

        if self._committing:
            return
        self._committing = True
        try:
            self._sync_geometry()
        except RuntimeError:
            pass
        finally:
            self._committing = False

    def schedule_refresh(self) -> None:
        """Compatibility entry: geometry refreshes are intentionally synchronous."""

        self.commit_now()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched in {self.viewport, self.jobs_host} and event.type() in {
            QEvent.Type.Resize,
            QEvent.Type.Show,
            QEvent.Type.LayoutRequest,
        }:
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
