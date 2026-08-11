from __future__ import annotations

from threading import RLock
from types import MethodType
from typing import Any

from PySide6.QtCore import QObject, QUrl
from PySide6.QtGui import QBrush, QImage
from PySide6.QtQuick import QQuickImageProvider
from PySide6.QtWidgets import QMainWindow, QTableWidget, QTableWidgetItem

from .batch_workspace import _STAGE_LABELS, _STATUS_COLORS, _product_label
from .console_window import _AI_STATUS_COLORS
from .main_window import STATUS_COLORS
from .result_loader import RunResult


_MASK_PROVIDER_ID = "ecommerceGlassMask"


class _GlassMaskImageProvider(QQuickImageProvider):
    """Serve the already-rendered glass mask directly from memory.

    The legacy fallback in ``NativeQuickBackground`` serializes the same QImage to
    PNG and immediately asks QML to decode it again. Keeping the exact image in
    memory removes PNG encoding and filesystem I/O from scroll/layout updates
    without changing a single mask pixel.
    """

    def __init__(self) -> None:
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._lock = RLock()
        self._image = QImage()

    def publish(self, image: QImage) -> None:
        with self._lock:
            self._image = QImage(image)

    def requestImage(self, _id: str, size, _requested_size):  # noqa: ANN001, N802
        with self._lock:
            image = QImage(self._image)
        if size is not None:
            size.setWidth(image.width())
            size.setHeight(image.height())
        return image


class UiRuntimeOptimizations(QObject):
    """Presentation-only hot-path optimizations for the formal QWidget/Quick UI.

    Business runners, permission gates, values, geometry, colors and transition
    durations stay owned by the existing GUI. This controller only removes
    avoidable allocation/serialization/parsing work from paths that can run
    repeatedly.
    """

    def __init__(self, window: QMainWindow, visual: Any) -> None:
        super().__init__(window)
        self.window = window
        self.visual = visual
        self._mask_provider: _GlassMaskImageProvider | None = None
        self._original_mask_update = None
        self._default_brush = QBrush()
        self._ai_status_brushes = {key: QBrush(color) for key, color in _AI_STATUS_COLORS.items()}
        self._final_status_brushes = {key: QBrush(color) for key, color in STATUS_COLORS.items()}
        self._batch_status_brushes = {key: QBrush(color) for key, color in _STATUS_COLORS.items()}
        self._prep_log_filter = None
        self._real_log_filter = None

        self._install_in_memory_glass_mask()
        self._install_in_place_single_tables()
        self._install_in_place_batch_tables()
        self._install_progress_log_filters()

    def _install_in_memory_glass_mask(self) -> None:
        background = getattr(self.visual, "background", None)
        engine = getattr(background, "engine", None)
        if background is None or engine is None:
            return

        provider = _GlassMaskImageProvider()
        engine.addImageProvider(_MASK_PROVIDER_ID, provider)
        self._mask_provider = provider
        self._original_mask_update = background._update_mask_texture  # noqa: SLF001

        def update_mask_texture(bg) -> None:  # noqa: ANN001
            quick = getattr(bg, "quick_window", None)
            if bool(getattr(bg, "_shutting_down", False)) or quick is None:
                return
            try:
                image = bg.card_model.render_mask(quick.width(), quick.height())
                bg._mask_revision += 1  # noqa: SLF001
                provider.publish(image)
                quick.setProperty(
                    "maskUrl",
                    QUrl(f"image://{_MASK_PROVIDER_ID}/{bg._mask_revision}"),  # noqa: SLF001
                )
                bg._mask_ready = True  # noqa: SLF001
            except Exception:
                # Rendering correctness wins over optimization. If an unusual Qt
                # runtime rejects the image-provider path, retain the proven PNG
                # fallback for this update rather than changing the visible UI.
                self._original_mask_update()

        background._update_mask_texture = MethodType(update_mask_texture, background)  # type: ignore[method-assign]  # noqa: SLF001
        background.schedule_mask_update()

    @staticmethod
    def _ensure_item(
        table: QTableWidget,
        row: int,
        column: int,
        text: str,
        *,
        tooltip: str | None = None,
    ) -> QTableWidgetItem:
        item = table.item(row, column)
        if item is None:
            item = QTableWidgetItem(text)
            table.setItem(row, column, item)
        elif item.text() != text:
            item.setText(text)
        expected_tooltip = text if tooltip is None else tooltip
        if item.toolTip() != expected_tooltip:
            item.setToolTip(expected_tooltip)
        return item

    def _brush(self, brushes: dict[str, QBrush], key: str) -> QBrush:
        return brushes.get(key, self._default_brush)

    def _install_in_place_single_tables(self) -> None:
        if not hasattr(self.window, "_populate_fields") or not hasattr(self.window, "_populate_web"):
            return

        controller = self

        def populate_fields(window, result: RunResult) -> None:  # noqa: ANN001
            table = window.field_table
            old_rows = table.rowCount()
            new_rows = len(result.fields)
            table.setUpdatesEnabled(False)
            try:
                if old_rows != new_rows:
                    table.setRowCount(new_rows)
                for row_index, row in enumerate(result.fields):
                    values = (
                        row.field_name,
                        row.ai_status,
                        row.ai_result,
                        row.final_status,
                        row.blocked_reason,
                        row.source,
                        row.field_id,
                    )
                    for column, value in enumerate(values):
                        item = controller._ensure_item(table, row_index, column, value)
                        if column == 1:
                            brush = controller._brush(controller._ai_status_brushes, row.ai_status)
                            if item.foreground() != brush:
                                item.setForeground(brush)
                        elif column == 3:
                            brush = controller._brush(controller._final_status_brushes, row.final_status)
                            if item.foreground() != brush:
                                item.setForeground(brush)
            finally:
                table.setUpdatesEnabled(True)
            if old_rows != new_rows:
                table.resizeRowsToContents()

        def populate_web(window, result: RunResult) -> None:  # noqa: ANN001
            table = window.web_table
            candidates = result.web_candidates
            old_rows = table.rowCount()
            new_rows = len(candidates)
            table.setUpdatesEnabled(False)
            try:
                if old_rows != new_rows:
                    table.setRowCount(new_rows)
                hint = f"{new_rows} candidates"
                if window.web_hint.text() != hint:
                    window.web_hint.setText(hint)
                for row_index, candidate in enumerate(candidates):
                    match_text = candidate.match.upper()
                    source_text = candidate.title or candidate.url
                    values = (match_text, source_text, candidate.reason)
                    for column, value in enumerate(values):
                        tooltip = value
                        if column == 1:
                            tooltip = candidate.url
                        elif column == 2 and candidate.identity_evidence:
                            tooltip += "\n\nIdentity evidence:\n- " + "\n- ".join(candidate.identity_evidence)
                        item = controller._ensure_item(
                            table,
                            row_index,
                            column,
                            value,
                            tooltip=tooltip,
                        )
                        if column == 0:
                            brush = controller._brush(controller._final_status_brushes, match_text)
                            if item.foreground() != brush:
                                item.setForeground(brush)
            finally:
                table.setUpdatesEnabled(True)
            if old_rows != new_rows:
                table.resizeRowsToContents()

        self.window._populate_fields = MethodType(populate_fields, self.window)  # type: ignore[method-assign]  # noqa: SLF001
        self.window._populate_web = MethodType(populate_web, self.window)  # type: ignore[method-assign]  # noqa: SLF001

    def _install_in_place_batch_tables(self) -> None:
        workspace = getattr(self.window, "batch_workspace", None)
        controller = getattr(workspace, "controller", None)
        table = getattr(workspace, "table", None)
        if workspace is None or controller is None or not isinstance(table, QTableWidget):
            return

        try:
            controller.jobs_changed.disconnect(workspace._apply_jobs)  # noqa: SLF001
        except (RuntimeError, TypeError):
            pass
        try:
            controller.summary_changed.disconnect(workspace._apply_summary)  # noqa: SLF001
        except (RuntimeError, TypeError):
            pass
        try:
            controller.state_changed.disconnect(workspace._set_state)  # noqa: SLF001
        except (RuntimeError, TypeError):
            pass

        controller.jobs_changed.connect(self._apply_batch_jobs)
        controller.summary_changed.connect(self._apply_batch_summary)
        controller.state_changed.connect(self._apply_batch_state)

    def _apply_batch_jobs(self, jobs: list[Any]) -> None:
        workspace = self.window.batch_workspace
        table: QTableWidget = workspace.table
        workspace._jobs = list(jobs)  # noqa: SLF001
        old_rows = table.rowCount()
        new_rows = len(jobs)

        table.setUpdatesEnabled(False)
        try:
            if old_rows != new_rows:
                table.setRowCount(new_rows)
            for row, job in enumerate(jobs):
                values = (
                    job.job_id,
                    job.product_name or _product_label(job.product_url),
                    _STAGE_LABELS.get(job.status, job.status),
                    job.vertical or "—",
                    job.brand or "—",
                    str(job.ready),
                    str(job.blocked),
                    f"{max(0, min(100, job.progress))}%",
                    job.error or job.stage_detail,
                )
                for column, value in enumerate(values):
                    item = self._ensure_item(table, row, column, value)
                    if column == 2:
                        brush = self._brush(self._batch_status_brushes, job.status)
                        if item.foreground() != brush:
                            item.setForeground(brush)
        finally:
            table.setUpdatesEnabled(True)

        enabled = not workspace.controller.is_running and any(job.status == "READY" for job in jobs)
        if workspace.execute_button.isEnabled() != enabled:
            workspace.execute_button.setEnabled(enabled)

    def _apply_batch_summary(self, summary: dict[str, int]) -> None:
        workspace = self.window.batch_workspace
        for key, label in workspace.summary_labels.items():
            text = str(summary.get(key, 0))
            if label.text() != text:
                label.setText(text)

    def _apply_batch_state(self, text: str) -> None:
        label = self.window.batch_workspace.state_label
        if label.text() != text:
            label.setText(text)

    def _install_progress_log_filters(self) -> None:
        activity = getattr(self.window, "_activity_presence_controller", None)
        if activity is None:
            return

        prep = getattr(self.window, "runner", None)
        prep_handler = getattr(activity, "_on_prep_log", None)
        if prep is not None and callable(prep_handler):
            disconnected = False
            try:
                prep.log.disconnect(prep_handler)
                disconnected = True
            except (RuntimeError, TypeError):
                pass
            if disconnected:
                prep_markers = (
                    "STEP 3 CURRENT RESOLVER · COLD",
                    "STEP 3 CURRENT RESOLVER · HOT/CACHE",
                    "STEP 3 CURRENT READ-ONLY FILL PLAN",
                )

                def prep_filter(line: str) -> None:
                    text = str(line or "")
                    if any(marker in text for marker in prep_markers):
                        prep_handler(line)

                self._prep_log_filter = prep_filter
                prep.log.connect(prep_filter)

        real = getattr(self.window, "execution_runner", None)
        real_handler = getattr(activity, "_on_real_log", None)
        if real is None or not callable(real_handler):
            return
        disconnected = False
        try:
            real.log.disconnect(real_handler)
            disconnected = True
        except (RuntimeError, TypeError):
            pass
        if not disconnected:
            return

        anchored_prefixes = (
            "GUI_EXEC_FIELD\t",
            "Price, Stock and Shipping Information:",
            "Product Description:",
            "Additional Description:",
            "photos:",
        )
        loose_markers = (
            "MAKRO STEP 3 DIRECT ACCEPTANCE",
            "ACCEPTANCE COMPLETE",
            "PREVIEW READY",
        )

        def real_filter(line: str) -> None:
            text = str(line or "").strip()
            if text.startswith(anchored_prefixes) or any(marker in text for marker in loose_markers):
                real_handler(line)

        self._real_log_filter = real_filter
        real.log.connect(real_filter)


def install_ui_runtime_optimizations(window: QMainWindow, visual: Any) -> UiRuntimeOptimizations:
    existing = getattr(window, "_ui_runtime_optimizations", None)
    if isinstance(existing, UiRuntimeOptimizations):
        return existing
    controller = UiRuntimeOptimizations(window, visual)
    window._ui_runtime_optimizations = controller  # type: ignore[attr-defined]
    return controller
