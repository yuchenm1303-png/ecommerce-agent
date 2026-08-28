from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QLabel, QProgressBar, QPushButton, QScrollArea, QSizePolicy, QWidget


_CARD_MARGIN_X = 12
_CARD_MARGIN_Y = 8
_CARD_SPACING = 4
_CARD_BUTTON_HEIGHT = 28
_CONTROL_MARGIN_X = 8
_CONTROL_MARGIN_Y = 4
_CONTROL_SPACING = 6
_JOB_SPACING = 6


class BatchCardResponsiveController(QObject):
    """Synchronously own Batch job-card geometry inside the visible viewport.

    The hidden QWidget tree is the authoritative layout/state host for the Quick
    mirror. Card density therefore belongs here too: one compact geometry contract
    is applied before the mirror snapshots each card, rather than layering fixed
    heights or clipping on top of the rendered Quick scene.
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
        if self.jobs_layout is not None:
            self.jobs_layout.setContentsMargins(1, 1, 1, 1)
            self.jobs_layout.setSpacing(_JOB_SPACING)
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

    @staticmethod
    def _single_line(label: QLabel | None) -> None:
        if not isinstance(label, QLabel):
            return
        label.setWordWrap(False)
        label.setMinimumHeight(0)
        label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def _compact_control_strip(self, controls: object | None) -> None:
        if controls is None:
            return
        host = getattr(controls, "host", None)
        hint = getattr(controls, "hint", None)
        if isinstance(host, QWidget):
            host.setMinimumWidth(0)
            host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            layout = host.layout()
            if layout is not None:
                layout.setContentsMargins(
                    _CONTROL_MARGIN_X,
                    _CONTROL_MARGIN_Y,
                    _CONTROL_MARGIN_X,
                    _CONTROL_MARGIN_Y,
                )
                layout.setSpacing(_CONTROL_SPACING)
        if isinstance(hint, QLabel):
            self._soft_horizontal(hint)
            self._single_line(hint)
        for name in ("run_button", "pause_button", "fill", "stop", "delete"):
            button = getattr(controls, name, None)
            if isinstance(button, QPushButton):
                button.setFixedHeight(_CARD_BUTTON_HEIGHT)

    def _apply_card_constraints(self, card: QWidget) -> None:
        card.setMinimumWidth(0)
        card.setMinimumHeight(0)
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        root = card.layout()
        if root is not None:
            root.setContentsMargins(
                _CARD_MARGIN_X,
                _CARD_MARGIN_Y,
                _CARD_MARGIN_X,
                _CARD_MARGIN_Y,
            )
            root.setSpacing(_CARD_SPACING)

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

        # The list card is a summary surface. Full product/error/log text remains
        # available through tooltips and the existing detail modal; allowing these
        # labels to wrap makes a single long value consume an arbitrary card height.
        for name in ("product_label", "meta_label", "detail_label", "error_label", "log_preview"):
            self._single_line(getattr(card, name, None))

        progress = getattr(card, "progress_bar", None)
        if isinstance(progress, QProgressBar):
            progress.setFixedHeight(5)

        for name in ("open_url_button", "open_dir_button", "modal_button", "toggle_button"):
            button = getattr(card, name, None)
            if isinstance(button, QPushButton):
                button.setFixedHeight(_CARD_BUTTON_HEIGHT)

        job_id = str(getattr(card, "job_id", ""))
        legacy_manager = getattr(self.workspace, "_batch_job_controls", None)
        legacy_map = getattr(legacy_manager, "_controls", None)
        if isinstance(legacy_map, dict):
            self._compact_control_strip(legacy_map.get(job_id))

        individual_manager = getattr(self.workspace, "_batch_individual_controls", None)
        individual_map = getattr(individual_manager, "_cards", None)
        if isinstance(individual_map, dict):
            self._compact_control_strip(individual_map.get(job_id))

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
