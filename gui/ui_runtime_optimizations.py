from __future__ import annotations

from threading import RLock
from types import MethodType
from typing import Any

from PySide6.QtCore import QEvent, QObject, QTimer, QUrl, Qt
from PySide6.QtGui import QBrush, QCursor, QImage
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


class _MinimizeRestoreKeeper(QObject):
    """Keep the last complete Quick/glass frame intact while the window is hidden.

    The QWidget tree is never rebuilt during minimize/restore, but the native
    Quick owner can temporarily become unexposed and the embedded QWidget surface
    can emit Hide events. If the glass geometry timer flushes during that gap,
    ``GlassCardModel`` legitimately observes every card as invisible and publishes
    an empty mask. The restore path then has to rebuild the glass card-by-card.

    This controller freezes geometry publication while the top-level presentation
    is hidden/minimized, retains the Quick scene graph/GPU resources for the whole
    app lifetime, and performs one coalesced geometry refresh only after both the
    Quick owner and QWidget overlay are visible again.
    """

    def __init__(self, window: QMainWindow, background: Any) -> None:
        super().__init__(window)
        self.window = window
        self.background = background
        self.quick = getattr(background, "quick_window", None)
        self._original_flush = getattr(background, "_flush_geometry", None)
        self._suspended = True

        if self.quick is None or not callable(self._original_flush):
            return

        # The visible scene is small enough that retaining its resources is far
        # cheaper than recreating textures, scene-graph nodes and glass delegates
        # every time the user restores the app from the taskbar.
        self.quick.setPersistentGraphics(True)
        self.quick.setPersistentSceneGraph(True)
        self.quick.installEventFilter(self)
        self.window.installEventFilter(self)

        timer = getattr(background, "_geometry_timer", None)
        if timer is not None:
            try:
                timer.timeout.disconnect(self._original_flush)
            except (RuntimeError, TypeError):
                pass
            timer.timeout.connect(self._flush_geometry)

        self._sync_state()

    def _should_suspend(self) -> bool:
        quick = self.quick
        if quick is None:
            return True
        try:
            minimized = bool(quick.windowState() & Qt.WindowState.WindowMinimized)
            return (
                minimized
                or not quick.isVisible()
                or not quick.isExposed()
                or not self.window.isVisible()
            )
        except RuntimeError:
            return True

    def _suspend(self) -> None:
        self._suspended = True
        self.background._geometry_dirty = True  # noqa: SLF001

        geometry_timer = getattr(self.background, "_geometry_timer", None)
        if geometry_timer is not None:
            try:
                geometry_timer.stop()
            except RuntimeError:
                pass

        pointer_timer = getattr(self.background, "_pointer_timer", None)
        if pointer_timer is not None:
            try:
                pointer_timer.stop()
            except RuntimeError:
                pass

        if self.quick is not None:
            try:
                self.quick.setProperty("animationRunning", False)
            except RuntimeError:
                pass

    def _modal_holds_underlay(self) -> bool:
        modal = getattr(self.window, "_static_modal_interaction", None)
        return bool(getattr(modal, "_underlay_suspended", False))

    def _resume_pointer_if_allowed(self) -> None:
        if self._modal_holds_underlay():
            return
        pointer_timer = getattr(self.background, "_pointer_timer", None)
        if pointer_timer is None:
            return
        try:
            if not pointer_timer.isActive():
                pointer_timer.start()
        except RuntimeError:
            pass

    def _resume(self) -> None:
        if self._should_suspend():
            self._suspend()
            return

        was_suspended = self._suspended
        self._suspended = False
        self.background._last_pointer_norm = None  # noqa: SLF001
        self._resume_pointer_if_allowed()

        # Keep the old mask on screen until this one coalesced refresh is ready.
        # Never clear _mask_ready or card_model state during restore.
        if was_suspended or bool(getattr(self.background, "_geometry_dirty", False)):
            self.background._geometry_dirty = True  # noqa: SLF001
            geometry_timer = getattr(self.background, "_geometry_timer", None)
            if geometry_timer is not None:
                try:
                    if not geometry_timer.isActive():
                        geometry_timer.start()
                except RuntimeError:
                    pass

    def _sync_state(self) -> None:
        if self._should_suspend():
            self._suspend()
        else:
            self._resume()

    def _flush_geometry(self) -> None:
        # This guard is the key to preventing a transient all-hidden QWidget tree
        # from replacing the previously complete glass mask with an empty one.
        if self._should_suspend():
            self._suspend()
            return

        if self._suspended:
            self._suspended = False
            self.background._last_pointer_norm = None  # noqa: SLF001
            self._resume_pointer_if_allowed()
        self._original_flush()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        event_type = event.type()

        if watched is self.quick:
            if event_type == QEvent.Type.Hide:
                self._suspend()
            elif event_type == QEvent.Type.WindowStateChange:
                try:
                    minimized = bool(
                        self.quick.windowState() & Qt.WindowState.WindowMinimized
                    )
                except RuntimeError:
                    minimized = True
                if minimized:
                    self._suspend()
                else:
                    QTimer.singleShot(0, self._sync_state)
            elif event_type in {QEvent.Type.Show, QEvent.Type.Expose}:
                QTimer.singleShot(0, self._sync_state)

        elif watched is self.window:
            if event_type == QEvent.Type.Hide:
                self._suspend()
            elif event_type == QEvent.Type.Show:
                QTimer.singleShot(0, self._sync_state)

        return False


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
        self._original_pointer_sample = None
        self._last_pointer_global: tuple[int, int] | None = None
        self._last_pointer_quick_geometry: tuple[int, int, int, int] | None = None
        self._default_brush = QBrush()
        self._ai_status_brushes = {key: QBrush(color) for key, color in _AI_STATUS_COLORS.items()}
        self._final_status_brushes = {key: QBrush(color) for key, color in STATUS_COLORS.items()}
        self._batch_status_brushes = {key: QBrush(color) for key, color in _STATUS_COLORS.items()}
        self._batch_row_fingerprints: list[tuple[str, ...]] = []
        self._prep_log_filter = None
        self._real_log_filter = None
        self._minimize_restore_keeper = None

        self._install_minimize_restore_keeper()
        self._install_idle_pointer_fast_path()
        self._install_in_memory_glass_mask()
        self._install_in_place_single_tables()
        self._install_in_place_batch_tables()
        self._install_progress_log_filters()

    def _install_minimize_restore_keeper(self) -> None:
        background = getattr(self.visual, "background", None)
        if background is None:
            return
        self._minimize_restore_keeper = _MinimizeRestoreKeeper(self.window, background)

    def _install_idle_pointer_fast_path(self) -> None:
        """Keep 8 ms sampling but skip coordinate work while pointer/owner are static."""

        background = getattr(self.visual, "background", None)
        timer = getattr(background, "_pointer_timer", None)
        original = getattr(background, "_sample_pointer", None)
        if background is None or timer is None or not callable(original):
            return

        try:
            timer.timeout.disconnect(original)
        except (RuntimeError, TypeError):
            return

        self._original_pointer_sample = original
        controller = self

        def sample_pointer(bg) -> None:  # noqa: ANN001
            quick = getattr(bg, "quick_window", None)
            if bool(getattr(bg, "_shutting_down", False)) or quick is None:
                return
            try:
                if not quick.isVisible() or quick.windowState() & Qt.WindowState.WindowMinimized:
                    return
                global_pos = QCursor.pos()
                point = (global_pos.x(), global_pos.y())
                geometry = (
                    int(quick.x()),
                    int(quick.y()),
                    int(quick.width()),
                    int(quick.height()),
                )
            except RuntimeError:
                return

            if (
                getattr(bg, "_last_pointer_norm", None) is not None
                and controller._last_pointer_global == point
                and controller._last_pointer_quick_geometry == geometry
            ):
                return

            controller._last_pointer_global = point
            controller._last_pointer_quick_geometry = geometry
            original()

        background._sample_pointer = MethodType(sample_pointer, background)  # type: ignore[method-assign]  # noqa: SLF001
        timer.timeout.connect(background._sample_pointer)  # noqa: SLF001

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

        payload: list[tuple[tuple[str, ...], str]] = []
        new_fingerprints: list[tuple[str, ...]] = []
        for job in jobs:
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
            fingerprint = (*values, str(job.status))
            payload.append((values, str(job.status)))
            new_fingerprints.append(fingerprint)

        previous = self._batch_row_fingerprints
        table_changed = old_rows != new_rows or previous != new_fingerprints
        if table_changed:
            table.setUpdatesEnabled(False)
            try:
                if old_rows != new_rows:
                    table.setRowCount(new_rows)
                for row, (values, status) in enumerate(payload):
                    if row < len(previous) and previous[row] == new_fingerprints[row]:
                        continue
                    for column, value in enumerate(values):
                        item = self._ensure_item(table, row, column, value)
                        if column == 2:
                            brush = self._brush(self._batch_status_brushes, status)
                            if item.foreground() != brush:
                                item.setForeground(brush)
            finally:
                table.setUpdatesEnabled(True)
            self._batch_row_fingerprints = new_fingerprints

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
