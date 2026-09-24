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
    """Keep the legacy Batch fallback cards compact without owning scroll extent.

    QScrollArea is now fallback-only; Qt owns its content height and scrollbar range.
    This controller only applies card width/density when card topology or viewport
    width changes. Runtime job-state updates do not trigger layout work.
    """

    def __init__(self, workspace: QWidget) -> None:
        super().__init__(workspace)
        self.workspace = workspace
        self.scroll = getattr(workspace, "job_scroll", None)
        self.jobs_host = getattr(workspace, "jobs_host", None)
        self.jobs_layout = getattr(workspace, "jobs_layout", None)
        self.viewport = self.scroll.viewport() if isinstance(self.scroll, QScrollArea) else None
        self._committing = False
        self._known_cards: tuple[int, ...] = ()
        self._jobs_signal = None

        if self.viewport is None or not isinstance(self.jobs_host, QWidget):
            return

        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.jobs_host.setMinimumWidth(0)
        self.jobs_host.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        if self.jobs_layout is not None:
            self.jobs_layout.setContentsMargins(1, 1, 1, 1)
            self.jobs_layout.setSpacing(_JOB_SPACING)
        self.viewport.installEventFilter(self)

        controller = getattr(workspace, "controller", None)
        jobs_changed = getattr(controller, "jobs_changed", None)
        if jobs_changed is not None and hasattr(jobs_changed, "connect"):
            try:
                jobs_changed.connect(self._on_jobs_changed)
                self._jobs_signal = jobs_changed
            except (RuntimeError, TypeError):
                self._jobs_signal = None

        self.commit_now()
        workspace.destroyed.connect(self.cleanup)

    @staticmethod
    def _soft_horizontal(widget: QWidget | None) -> None:
        if widget is None:
            return
        widget.setMinimumWidth(0)
        widget.setSizePolicy(QSizePolicy.Policy.Ignored, widget.sizePolicy().verticalPolicy())

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
            root.setContentsMargins(_CARD_MARGIN_X, _CARD_MARGIN_Y, _CARD_MARGIN_X, _CARD_MARGIN_Y)
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
        label.setText(
            label.fontMetrics().elidedText(
                url,
                Qt.TextElideMode.ElideMiddle,
                max(80, available),
            )
        )
        label.setToolTip(url)

    def _card_ids(self) -> tuple[int, ...]:
        cards = getattr(self.workspace, "_job_cards", None)
        if not isinstance(cards, dict):
            return ()
        return tuple(id(card) for card in cards.values() if isinstance(card, QWidget))

    def _on_jobs_changed(self, _jobs: object = None) -> None:
        topology = self._card_ids()
        if topology == self._known_cards:
            return
        self.commit_now()

    def commit_now(self) -> None:
        if self._committing or self.viewport is None:
            return
        self._committing = True
        try:
            viewport_width = max(1, int(self.viewport.width()))
            content_width = viewport_width
            if self.jobs_layout is not None:
                margins = self.jobs_layout.contentsMargins()
                content_width -= margins.left() + margins.right()
            content_width = max(1, content_width)

            cards = getattr(self.workspace, "_job_cards", None)
            if isinstance(cards, dict):
                for card in cards.values():
                    if not isinstance(card, QWidget):
                        continue
                    self._apply_card_constraints(card)
                    card.setMaximumWidth(content_width)
                    self._elide_url(card)
            self._known_cards = self._card_ids()
        except RuntimeError:
            pass
        finally:
            self._committing = False

    def schedule_refresh(self) -> None:
        self.commit_now()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self.viewport and event.type() in {QEvent.Type.Resize, QEvent.Type.Show}:
            self.commit_now()
        return False

    def cleanup(self) -> None:
        signal = self._jobs_signal
        self._jobs_signal = None
        if signal is not None:
            try:
                signal.disconnect(self._on_jobs_changed)
            except (RuntimeError, TypeError):
                pass
        if self.viewport is not None:
            try:
                self.viewport.removeEventFilter(self)
            except RuntimeError:
                pass


def install_batch_card_responsive(workspace: QWidget) -> BatchCardResponsiveController:
    existing = getattr(workspace, "_batch_card_responsive", None)
    if isinstance(existing, BatchCardResponsiveController):
        return existing
    controller = BatchCardResponsiveController(workspace)
    setattr(workspace, "_batch_card_responsive", controller)
    return controller


__all__ = ["BatchCardResponsiveController", "install_batch_card_responsive"]
