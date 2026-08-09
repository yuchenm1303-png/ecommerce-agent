from __future__ import annotations

import base64
import json
import math
from pathlib import Path
from typing import Any

from PySide6.QtCore import Property, QAbstractListModel, QModelIndex, QObject, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QCursor, QDesktopServices, QImage, QPainter, QPixmap, QRadialGradient
from PySide6.QtQuick import QQuickImageProvider
from PySide6.QtWidgets import QGraphicsBlurEffect, QGraphicsPixmapItem, QGraphicsScene

from app.makro.listing_preflight import CORE_FORM_SECTIONS

from .readonly_runner import ReadOnlyRunner, RunnerConfig
from .real_execution import FULL_STEP3, PRODUCT_PHOTOS, RealExecutionConfig, RealExecutionRunner
from .result_loader import PhaseStats, RunResult


_WALLPAPER_ASSET = Path(__file__).resolve().parent / "assets" / "fuji_sakura_wallpaper.jpg.b64"


class LogModel(QAbstractListModel):
    TextRole = Qt.UserRole + 1

    def __init__(self, parent: QObject | None = None, *, limit: int = 12000) -> None:
        super().__init__(parent)
        self._rows: list[str] = []
        self._limit = max(100, int(limit))

    def roleNames(self) -> dict[int, bytes]:  # type: ignore[override]
        return {self.TextRole: b"text"}

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # type: ignore[override]
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> Any:  # type: ignore[override]
        if not index.isValid() or not (0 <= index.row() < len(self._rows)):
            return None
        if role in (Qt.DisplayRole, self.TextRole):
            return self._rows[index.row()]
        return None

    @Slot()
    def clear(self) -> None:
        if not self._rows:
            return
        self.beginResetModel()
        self._rows.clear()
        self.endResetModel()

    @Slot(str)
    def append(self, line: str) -> None:
        if len(self._rows) >= self._limit:
            drop = min(512, len(self._rows))
            self.beginRemoveRows(QModelIndex(), 0, drop - 1)
            del self._rows[:drop]
            self.endRemoveRows()
        row = len(self._rows)
        self.beginInsertRows(QModelIndex(), row, row)
        self._rows.append(str(line))
        self.endInsertRows()


class WallpaperProvider(QQuickImageProvider):
    """One decoded wallpaper plus one pre-blurred companion shared by the scene graph."""

    def __init__(self) -> None:
        super().__init__(QQuickImageProvider.Image)
        source = self._load_source()
        self._sharp = self._compose_vignette(source)
        self._blur = self._blur(source, 10.0)

    def requestImage(self, image_id: str, size, requested_size):  # noqa: N802
        del size, requested_size
        return QImage(self._blur if image_id.casefold() == "blur" else self._sharp)

    @staticmethod
    def _load_source() -> QImage:
        encoded = _WALLPAPER_ASSET.read_text(encoding="ascii")
        data = base64.b64decode("".join(encoded.split()), validate=True)
        image = QImage.fromData(data)
        if image.isNull():
            raise RuntimeError(f"Qt could not decode wallpaper: {_WALLPAPER_ASSET}")
        return image.convertToFormat(QImage.Format.Format_RGBA8888)

    @staticmethod
    def _compose_vignette(source: QImage) -> QImage:
        image = QImage(source)
        painter = QPainter(image)
        center = image.rect().center()
        radius = max(1.0, math.hypot(image.width() / 2.0, image.height() / 2.0))
        gradient = QRadialGradient(center, radius)
        gradient.setColorAt(0.0, Qt.transparent)
        from PySide6.QtGui import QColor
        gradient.setColorAt(1.0, QColor(0, 0, 0, 92))
        painter.fillRect(image.rect(), gradient)
        painter.end()
        return image

    @staticmethod
    def _blur(source: QImage, radius: float) -> QImage:
        pixmap = QPixmap.fromImage(source)
        result = QPixmap(pixmap.size())
        result.fill(Qt.transparent)
        scene = QGraphicsScene()
        item = QGraphicsPixmapItem(pixmap)
        effect = QGraphicsBlurEffect()
        effect.setBlurRadius(radius)
        item.setGraphicsEffect(effect)
        scene.addItem(item)
        painter = QPainter(result)
        scene.render(painter)
        painter.end()
        return result.toImage().convertToFormat(QImage.Format.Format_RGBA8888)


class QuickBridge(QObject):
    stateChanged = Signal()
    notice = Signal(str, str, str)
    focusRealConsole = Signal()

    def __init__(self, project_root: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.project_root = project_root.resolve()
        self.runner = ReadOnlyRunner(self.project_root, self)
        self.execution_runner = RealExecutionRunner(self.project_root, self)

        self.read_logs = LogModel(self)
        self.real_logs = LogModel(self)

        self._phase_text = "Idle · No Makro writes"
        self._progress = 0
        self._progress_text = "0% · idle"
        self._read_running = False
        self._real_running = False
        self._real_unlocked = False
        self._current_result: RunResult | None = None
        self._selected_images: list[Path] = []
        self._fields: list[dict[str, Any]] = []
        self._web_candidates: list[dict[str, Any]] = []
        self._telemetry: list[str] = [
            "Cold Local · waiting", "Cold Web · waiting", "Hot Local · waiting", "Hot Web · waiting",
            "Source cache · waiting", "Web cache · waiting", "Model calls · waiting", "Fields / Plan · waiting",
        ]
        self._safety = {"writes": "NO / 0", "save": "NO", "qc": "NO · LOCKED", "safe": True}
        self._real_command = ""
        self._real_summary = "等待真实执行报告"
        self._real_fields: list[dict[str, Any]] = []
        self._real_report = ""

        self.runner.log.connect(self.read_logs.append)
        self.runner.phase_changed.connect(self._set_phase)
        self.runner.progress_changed.connect(self._on_read_progress)
        self.runner.running_changed.connect(self._on_read_running)
        self.runner.result_updated.connect(self._apply_result)
        self.runner.completed.connect(self._on_read_completed)
        self.runner.failed.connect(self._on_read_failed)

        self.execution_runner.log.connect(self.real_logs.append)
        self.execution_runner.progress_changed.connect(self._on_real_progress)
        self.execution_runner.running_changed.connect(self._on_real_running)
        self.execution_runner.command_started.connect(self._on_real_command)
        self.execution_runner.completed.connect(self._on_real_completed)
        self.execution_runner.failed.connect(self._on_real_failed)

    @Property(QObject, constant=True)
    def readLogModel(self) -> QObject:  # noqa: N802
        return self.read_logs

    @Property(QObject, constant=True)
    def realLogModel(self) -> QObject:  # noqa: N802
        return self.real_logs

    @Property(str, notify=stateChanged)
    def phaseText(self) -> str:  # noqa: N802
        return self._phase_text

    @Property(int, notify=stateChanged)
    def progressValue(self) -> int:  # noqa: N802
        return self._progress

    @Property(str, notify=stateChanged)
    def progressText(self) -> str:  # noqa: N802
        return self._progress_text

    @Property(bool, notify=stateChanged)
    def readRunning(self) -> bool:  # noqa: N802
        return self._read_running

    @Property(bool, notify=stateChanged)
    def realRunning(self) -> bool:  # noqa: N802
        return self._real_running

    @Property(bool, notify=stateChanged)
    def realUnlocked(self) -> bool:  # noqa: N802
        return self._real_unlocked

    @Property("QVariantList", notify=stateChanged)
    def fields(self) -> list[dict[str, Any]]:
        return self._fields

    @Property("QVariantList", notify=stateChanged)
    def webCandidates(self) -> list[dict[str, Any]]:  # noqa: N802
        return self._web_candidates

    @Property("QVariantList", notify=stateChanged)
    def telemetry(self) -> list[str]:
        return self._telemetry

    @Property("QVariantMap", notify=stateChanged)
    def safety(self) -> dict[str, Any]:
        return self._safety

    @Property("QVariantMap", notify=stateChanged)
    def counts(self) -> dict[str, int]:
        result = self._current_result
        if result is None:
            return {"ready": -1, "missing": -1, "conflict": -1, "blocked": -1}
        return {
            "ready": result.ready,
            "missing": result.missing,
            "conflict": result.conflict,
            "blocked": result.blocked,
        }

    @Property(str, notify=stateChanged)
    def fieldsHint(self) -> str:  # noqa: N802
        if self._read_running:
            return "运行中"
        if self._current_result is None:
            return "等待只读测试结果"
        return f"{self._current_result.live_field_count} fields" if self._current_result.live_field_count else "partial result"

    @Property(str, notify=stateChanged)
    def webHint(self) -> str:  # noqa: N802
        return f"{len(self._web_candidates)} candidates" if self._current_result else "等待 Web research"

    @Property(int, notify=stateChanged)
    def selectedImageCount(self) -> int:  # noqa: N802
        return len(self._selected_images)

    @Property("QVariantList", constant=True)
    def realScopes(self) -> list[dict[str, str]]:  # noqa: N802
        rows = [{"label": f"Single · {section}", "value": section} for section in CORE_FORM_SECTIONS]
        rows.append({"label": f"Single · {PRODUCT_PHOTOS}", "value": PRODUCT_PHOTOS})
        rows.append({"label": "Full Step 3 · persisted acceptance", "value": FULL_STEP3})
        return rows

    @Property(str, notify=stateChanged)
    def realCommand(self) -> str:  # noqa: N802
        return self._real_command

    @Property(str, notify=stateChanged)
    def realSummary(self) -> str:  # noqa: N802
        return self._real_summary

    @Property("QVariantList", notify=stateChanged)
    def realFields(self) -> list[dict[str, Any]]:  # noqa: N802
        return self._real_fields

    @Property(str, notify=stateChanged)
    def realReport(self) -> str:  # noqa: N802
        return self._real_report

    @Slot(str, int, int, str, bool)
    def startReadOnly(self, product_url: str, makro_port: int, source_port: int, vertical: str, current_page: bool) -> None:  # noqa: N802
        if self._real_running:
            self.notice.emit("warning", "无法开始只读测试", "真实网页执行仍在运行。")
            return
        self._reset_read_state()
        config = RunnerConfig(
            product_url=product_url.strip(),
            expected_vertical=vertical.strip(),
            makro_cdp_port=int(makro_port),
            source_cdp_port=int(source_port),
            source_use_current_page=bool(current_page),
        )
        try:
            self.runner.start(config)
        except Exception as exc:
            self.notice.emit("error", "只读测试无法启动", str(exc))

    @Slot()
    def stopReadOnly(self) -> None:  # noqa: N802
        self.runner.stop()

    @Slot("QVariantList")
    def setSelectedImages(self, urls: list[Any]) -> None:  # noqa: N802
        selected: list[Path] = []
        for value in urls:
            url = value if isinstance(value, QUrl) else QUrl(str(value))
            path = Path(url.toLocalFile()).resolve() if url.isLocalFile() else Path(str(value)).resolve()
            if path.is_file():
                selected.append(path)
        self._selected_images = selected
        self.stateChanged.emit()

    @Slot(str, bool, bool)
    def startReal(self, scope: str, allow_save: bool, upload_enabled: bool) -> None:  # noqa: N802
        if self._read_running:
            self.notice.emit("warning", "无法开始真实测试", "read-only acceptance 仍在运行。")
            return
        result = self._current_result
        if result is None or not result.plan_summary:
            self.notice.emit("warning", "无法开始真实测试", "请先完整跑通 read-only 四阶段验收。")
            return
        if scope == FULL_STEP3 and not allow_save:
            self.notice.emit("warning", "Full Step 3 需要 Save 授权", "Full Step 3 是持久化验收，必须显式开启 Save。")
            return
        images: tuple[Path, ...] = ()
        if upload_enabled:
            if not self._selected_images:
                self.notice.emit("warning", "未选择图片", "已开启上传图片，请先选择实际 listing 图片。")
                return
            images = tuple(self._selected_images)
        config = RealExecutionConfig(
            read_only_run_dir=result.run_dir,
            scope=scope,
            expected_vertical=self.runner.config.expected_vertical if self.runner.config else "vehicle_camera_system",
            makro_cdp_port=self.runner.config.makro_cdp_port if self.runner.config else 9222,
            allow_save=bool(allow_save),
            upload_images=images,
        )
        try:
            self.real_logs.clear()
            self._real_fields = []
            self._real_report = ""
            self._real_summary = "真实网页执行中…"
            self.execution_runner.start(config)
            self.focusRealConsole.emit()
        except Exception as exc:
            self.notice.emit("error", "真实测试无法启动", str(exc))

    @Slot()
    def stopReal(self) -> None:  # noqa: N802
        self.execution_runner.stop()

    @Slot()
    def openRunDir(self) -> None:  # noqa: N802
        run_dir = self.runner.run_dir
        if run_dir is not None and run_dir.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(run_dir)))

    def _reset_read_state(self) -> None:
        self._current_result = None
        self._real_unlocked = False
        self._fields = []
        self._web_candidates = []
        self._telemetry = [
            "Cold Local · waiting", "Cold Web · waiting", "Hot Local · waiting", "Hot Web · waiting",
            "Source cache · waiting", "Web cache · waiting", "Model calls · waiting", "Fields / Plan · waiting",
        ]
        self._safety = {"writes": "NO / 0", "save": "NO", "qc": "NO · LOCKED", "safe": True}
        self.read_logs.clear()
        self.stateChanged.emit()

    def _set_phase(self, text: str) -> None:
        self._phase_text = text
        self.stateChanged.emit()

    def _on_read_progress(self, percent: int, text: str) -> None:
        self._progress = max(0, min(100, int(percent)))
        self._progress_text = f"{self._progress}% · {text}"
        self.stateChanged.emit()

    def _on_read_running(self, running: bool) -> None:
        self._read_running = bool(running)
        self.stateChanged.emit()

    def _apply_result(self, result: RunResult) -> None:
        self._current_result = result
        self._fields = [
            {
                "fieldName": row.field_name,
                "aiStatus": row.ai_status,
                "aiResult": row.ai_result,
                "finalStatus": row.final_status,
                "blockedReason": row.blocked_reason,
                "source": row.source,
                "fieldId": row.field_id,
            }
            for row in result.fields
        ]
        self._web_candidates = [
            {
                "match": item.match.upper(),
                "source": item.title or item.url,
                "url": item.url,
                "reason": item.reason,
                "evidence": "\n".join(item.identity_evidence),
            }
            for item in result.web_candidates
        ]
        self._telemetry = self._telemetry_for(result)
        self._safety = {
            "writes": f"{'YES' if result.safety.writes_performed else 'NO'} / {result.safety.writes_performed}",
            "save": "YES" if result.safety.save_clicked else "NO",
            "qc": "YES" if result.safety.send_to_qc_clicked else "NO · LOCKED",
            "safe": result.safety.safe,
        }
        self._real_unlocked = result.ready > 0 and bool(result.plan_summary)
        self.stateChanged.emit()

    def _on_read_completed(self, result: RunResult) -> None:
        self._apply_result(result)
        if result.safety.safe:
            self._phase_text = "完成 · 0 Write / 0 Save / 0 QC"
        else:
            self._phase_text = "警告 · Safety contract violated"
            self.notice.emit("error", "Makro write safety warning", "检测到本次 manifest 记录了写入/Save/QC。请立即检查日志。")
        self.stateChanged.emit()

    def _on_read_failed(self, message: str) -> None:
        self.notice.emit("warning", "只读测试未完成", message)

    def _on_real_progress(self, percent: int, text: str) -> None:
        self._progress = max(0, min(100, int(percent)))
        self._progress_text = f"REAL {self._progress}% · {text}"
        self._phase_text = self._progress_text
        self.stateChanged.emit()

    def _on_real_running(self, running: bool) -> None:
        self._real_running = bool(running)
        if running:
            self._phase_text = "REAL · browser execution running"
        self.stateChanged.emit()

    def _on_real_command(self, event: dict[str, Any]) -> None:
        self._real_command = (
            f"$ {event.get('command', '')}\n"
            f"cwd={event.get('cwd', '')}\n"
            f"output={event.get('output_dir', '')}"
        )
        self.stateChanged.emit()

    def _on_real_completed(self, report: dict[str, Any]) -> None:
        totals = report.get("field_totals") or {}
        photo = report.get("photo_upload") or {}
        self._real_summary = (
            "candidate={candidate} · attempted={attempted} · validated={validated} · persisted={persisted} · "
            "validation_failed={failed} · fill_error={errors} · sections_saved={saved} · photos={photos} · QC={qc}"
        ).format(
            candidate=totals.get("candidate_count", 0),
            attempted=totals.get("writes_attempted", 0),
            validated=totals.get("validated", 0),
            persisted=totals.get("persisted_verified", 0),
            failed=int(totals.get("validation_failed", 0)) + int(totals.get("persisted_validation_failed", 0)),
            errors=totals.get("fill_error", 0),
            saved=report.get("section_saved", 0),
            photos=photo.get("staged", 0) if isinstance(photo, dict) else 0,
            qc=report.get("send_to_qc_clicked", False),
        )
        fields: list[dict[str, str]] = []
        for section in report.get("section_reports") or []:
            if not isinstance(section, dict):
                continue
            persisted = {
                str(item.get("label") or item.get("attribute_key") or ""): str(item.get("status") or "")
                for item in section.get("persisted_verifications") or [] if isinstance(item, dict)
            }
            for item in section.get("results") or []:
                if not isinstance(item, dict):
                    continue
                key = str(item.get("label") or item.get("attribute_key") or "")
                answer = item.get("answer_values") or item.get("answer") or ""
                if isinstance(answer, list):
                    answer = " + ".join(str(value) for value in answer)
                fields.append({
                    "section": str(section.get("section") or ""),
                    "field": key,
                    "mode": str(item.get("preview_mode") or ""),
                    "execution": str(item.get("execution_status") or ""),
                    "answer": str(answer),
                    "persisted": persisted.get(key, ""),
                    "detail": str(item.get("detail") or ""),
                })
        self._real_fields = fields
        self._real_report = json.dumps(report, ensure_ascii=False, indent=2)
        attempted = int(totals.get("writes_attempted", 0) or 0)
        saved = int(report.get("section_saved", 0) or 0)
        qc = bool(report.get("send_to_qc_clicked", False))
        self._safety = {
            "writes": f"YES / {attempted}" if attempted else "NO / 0",
            "save": f"YES / {saved}" if saved else "NO",
            "qc": "YES" if qc else "NO · LOCKED",
            "safe": not qc,
        }
        self._phase_text = (
            f"REAL complete · attempted={attempted} · validated={int(totals.get('validated', 0) or 0)} · "
            f"persisted={int(totals.get('persisted_verified', 0) or 0)}"
        )
        self.stateChanged.emit()
        self.focusRealConsole.emit()

    def _on_real_failed(self, message: str) -> None:
        self._phase_text = "REAL failed"
        self._real_summary = "FAILED · " + message
        self.stateChanged.emit()
        self.notice.emit("warning", "真实网页验收未完成", message)
        self.focusRealConsole.emit()

    @staticmethod
    def _local_text(name: str, stats: PhaseStats) -> str:
        return f"{name} · batches={stats.batch_count} · calls={stats.model_calls} · cache={stats.cache_hits}/{stats.batch_count} · failed={stats.failed_batches}"

    @staticmethod
    def _web_text(name: str, stats: PhaseStats) -> str:
        return f"{name} · batches={stats.web_batch_count} · calls={stats.web_model_calls} · cache={stats.web_cache_hits}/{stats.web_batch_count} · failed={stats.web_failed_batches}"

    def _telemetry_for(self, result: RunResult) -> list[str]:
        cold, hot = result.cold, result.hot
        ai_counts: dict[str, int] = {}
        for row in result.fields:
            ai_counts[row.ai_status] = ai_counts.get(row.ai_status, 0) + 1
        ai_text = ", ".join(f"{key}={value}" for key, value in sorted(ai_counts.items())) or "waiting"
        local_calls = cold.model_calls + hot.model_calls
        web_calls = cold.web_model_calls + hot.web_model_calls
        return [
            self._local_text("Cold Local", cold),
            self._web_text("Cold Web", cold),
            self._local_text("Hot Local", hot),
            self._web_text("Hot Web", hot),
            f"Source cache · Cold={'HIT' if cold.source_cache_hit else 'MISS'} · Hot={'HIT' if hot.source_cache_hit else 'MISS'}",
            f"Web cache · Cold {cold.web_cache_hits}/{cold.web_batch_count} · Hot {hot.web_cache_hits}/{hot.web_batch_count}",
            f"Model calls · Local={local_calls} · Web={web_calls} · Total={local_calls + web_calls}",
            f"Fields / Plan · live={result.live_field_count} · final READY={result.ready} · BLOCKED={result.blocked} · AI[{ai_text}]",
        ]
