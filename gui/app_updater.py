from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PySide6.QtCore import QByteArray, QFile, QIODevice, QObject, Qt, QTimer, QUrl, QUrlQuery
from PySide6.QtGui import QDesktopServices
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QApplication,
    QBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QWidget,
)

from app.updater_core import (
    JOB_VERSION,
    RESULT_MARKER_FAILED,
    RESULT_OK,
    RESULT_RELAUNCH_FAILED,
    UpdaterJob,
)
from gui.update_runtime import (
    owned_qprocess_pids,
    prepare_standalone_updater,
    stable_updater_dir,
    update_download_dir,
    update_marker_path,
    update_state_dir,
    updater_log_path,
    updater_result_path,
)

_REPOSITORY = "yuchenm1303-png/ecommerce-agent"
_LATEST_RELEASE_API = f"https://api.github.com/repos/{_REPOSITORY}/releases/latest"
_MANIFEST_ASSET = "update.json"
_PORTAL_URL = "https://smirel.com/download/"
_PORTAL_HOSTS = {"smirel.com", "www.smirel.com"}
_PRIVATE_DOWNLOAD_HOSTS = {
    "nfzkphjbelyltrzgkdwt.supabase.co",
    "nfzkphjbelyltrzgkdwt.storage.supabase.co",
}
_PACKAGE_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[.-][0-9A-Za-z.-]+)?$")
_STABLE_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_CHECK_DELAY_MS = 1800
_AUTO_CHECK_INTERVAL_MS = 60 * 60 * 1000
_NETWORK_TIMEOUT_MS = 20_000
_RETRY_DELAY_MS = 650
_MAX_RELEASE_ATTEMPTS = 2
_MAX_MANIFEST_ATTEMPTS = 2
_MAX_PORTAL_ATTEMPTS = 2
_MAX_INSTALLER_ATTEMPTS = 2
_HANDOFF_ACK_TIMEOUT_S = 15.0
_MARKER_STALE_S = 24 * 60 * 60
_DIAGNOSTIC_LOG = "updater-network.jsonl"

_RETRIABLE_ERRORS = {
    QNetworkReply.NetworkError.RemoteHostClosedError,
    QNetworkReply.NetworkError.HostNotFoundError,
    QNetworkReply.NetworkError.TimeoutError,
    QNetworkReply.NetworkError.TemporaryNetworkFailureError,
    QNetworkReply.NetworkError.NetworkSessionFailedError,
    QNetworkReply.NetworkError.UnknownNetworkError,
}


def _version_key(value: str) -> tuple[int, int, int] | None:
    match = _PACKAGE_VERSION_RE.fullmatch(str(value or "").strip())
    if match is None:
        return None
    return tuple(int(part) for part in match.groups())


def installed_application_version() -> str:
    candidates: list[Path] = []
    if bool(getattr(sys, "frozen", False)):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "packaging" / "VERSION")
        candidates.append(
            Path(sys.executable).resolve().parent / "_internal" / "packaging" / "VERSION"
        )
    else:
        candidates.append(Path(__file__).resolve().parents[1] / "packaging" / "VERSION")

    for path in candidates:
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if _version_key(value) is not None:
            return value
    return "0.0.0"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while True:
                block = stream.read(1024 * 1024)
                if not block:
                    break
                digest.update(block)
    except OSError:
        return ""
    return digest.hexdigest()


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> bool:
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temp, path)
        return True
    except OSError:
        return False
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def _update_marker_path() -> Path:
    return update_marker_path()


def _write_update_marker(version: str) -> bool:
    return _atomic_write_json(
        update_marker_path(),
        {
            "version": str(version or "").strip().lstrip("v"),
            "created_at": time.time(),
        },
    )


def _consume_completed_update_marker(current_version: str) -> bool:
    path = update_marker_path()
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        payload = {}

    target = str(payload.get("version") or "").strip().lstrip("v") if isinstance(payload, dict) else ""
    current = str(current_version or "").strip().lstrip("v")
    if target and target == current:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return True

    # Do not consume a live target marker from the old version while an update
    # is between handoff and installation.  Only discard clearly stale markers.
    created_at = 0.0
    if isinstance(payload, dict):
        try:
            created_at = float(payload.get("created_at") or 0.0)
        except (TypeError, ValueError):
            created_at = 0.0
    if created_at <= 0:
        try:
            created_at = path.stat().st_mtime
        except OSError:
            created_at = 0.0
    if created_at > 0 and time.time() - created_at > _MARKER_STALE_S:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    return False


def _consume_previous_update_result(current_version: str) -> dict[str, Any] | None:
    path = updater_result_path()
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        payload = {}
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
    if not isinstance(payload, dict) or not payload:
        return None

    status = str(payload.get("status") or "")
    target = str(payload.get("target_version") or "").strip().lstrip("v")
    current = str(current_version or "").strip().lstrip("v")
    if status == RESULT_OK and target == current:
        return {"kind": "success", **payload}
    if status == RESULT_RELAUNCH_FAILED and target == current:
        return {"kind": "warning", **payload}
    if status == RESULT_MARKER_FAILED and target == current:
        return {"kind": "warning", **payload}
    if target == current and current:
        # A manual reinstall/recovery reached the requested version; do not keep
        # surfacing a stale failure from the superseded attempt.
        return None
    return {"kind": "failure", **payload}


def _expected_github_installer_path(version: str) -> str:
    clean = str(version or "").strip().lstrip("v")
    return f"/{_REPOSITORY}/releases/download/v{clean}/EcommerceAgent-Setup-{clean}.exe"


def _diagnostic_path() -> Path:
    return update_state_dir() / _DIAGNOSTIC_LOG


def _safe_url(value: str | QUrl) -> str:
    url = QUrl(value) if isinstance(value, str) else QUrl(value)
    if not url.isValid():
        return ""
    url.setQuery("")
    url.setFragment("")
    return url.toString()


def _http_status(reply: QNetworkReply) -> int | None:
    value = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _enum_code(value: Any) -> int | None:
    raw = getattr(value, "value", value)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _reply_diagnostic(stage: str, reply: QNetworkReply, *, attempt: int, source: str) -> dict[str, Any]:
    error = reply.error()
    return {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "stage": stage,
        "source": source,
        "attempt": attempt,
        "url": _safe_url(reply.url()),
        "http_status": _http_status(reply),
        "qt_error": _enum_code(error),
        "qt_error_name": getattr(error, "name", str(error)),
        "error": str(reply.errorString() or "").strip(),
    }


def _write_diagnostic(payload: dict[str, Any]) -> None:
    path = _diagnostic_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    except OSError:
        pass


def _failure_message(title: str, diagnostic: dict[str, Any] | None = None) -> str:
    if not diagnostic:
        return f"{title}\n\n已记录诊断：{_diagnostic_path()}"
    status = diagnostic.get("http_status")
    error_name = str(diagnostic.get("qt_error_name") or "NetworkError")
    error_text = str(diagnostic.get("error") or "unknown network error")
    detail = f"{error_name}: {error_text}"
    if status:
        detail = f"HTTP {status} · {detail}"
    return f"{title}\n\n{detail}\n\n已记录诊断：{_diagnostic_path()}"


def _request(url: str, version: str, *, accept: bytes, github_api: bool = False) -> QNetworkRequest:
    request = QNetworkRequest(QUrl(url))
    request.setRawHeader(b"User-Agent", f"EcommerceAgent/{version}".encode("ascii", "ignore"))
    request.setRawHeader(b"Accept", accept)
    request.setRawHeader(b"Cache-Control", b"no-cache")
    if github_api:
        request.setRawHeader(b"X-GitHub-Api-Version", b"2022-11-28")
    request.setTransferTimeout(_NETWORK_TIMEOUT_MS)
    request.setAttribute(
        QNetworkRequest.Attribute.RedirectPolicyAttribute,
        QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy,
    )
    return request


def _api_request(url: str, version: str) -> QNetworkRequest:
    return _request(url, version, accept=b"application/vnd.github+json", github_api=True)


def _asset_request(url: str, version: str, *, github_api: bool) -> QNetworkRequest:
    return _request(url, version, accept=b"application/octet-stream", github_api=github_api)


def _phase_label(label: str) -> str:
    text = str(label or "")
    if text.startswith("步骤 "):
        return text
    if text.startswith("正在验证更新权限"):
        return f"步骤 1/4 · {text}"
    if text.startswith("授权完成") or text.startswith("正在下载"):
        return f"步骤 2/4 · {text}"
    if text.startswith("下载完成") or text.startswith("正在校验"):
        return f"步骤 3/4 · {text}"
    if text.startswith("校验通过") or text.startswith("正在启动更新执行器"):
        return f"步骤 4/4 · {text}"
    return text


def ensure_updater_installed() -> Path | None:
    """Compatibility name backed by the hash+runtime-verified bootstrap."""

    return prepare_standalone_updater()


class ApplicationUpdater(QObject):
    """Single canonical Stable updater: resilient transport + verified handoff."""

    def __init__(self, window: QMainWindow, *, access_controller: Any | None = None) -> None:
        super().__init__(window)
        self.window = window
        self.access_controller = access_controller
        self.current_version = installed_application_version()
        self.network = QNetworkAccessManager(self)
        self._reply: QNetworkReply | None = None
        self._download_file: QFile | None = None
        self._download_path: Path | None = None
        self._download_write_failed = False
        self._progress: QProgressDialog | None = None
        self._manual_check = False
        self._last_prompted_version: str | None = None
        self._version_label: QLabel | None = None
        self._check_button: QPushButton | None = None
        self._release_attempt = 0
        self._manifest_attempt = 0
        self._manifest_source = ""
        self._manifest_asset_api_url = ""
        self._manifest_browser_url = ""
        self._release_version = ""
        self._release_target = ""
        self._release_installer_url = ""
        self._release_installer_digest = ""
        self._release_installer_size = 0
        self._auto_timer = QTimer(self)
        self._auto_timer.setInterval(_AUTO_CHECK_INTERVAL_MS)
        self._auto_timer.timeout.connect(self.check_for_updates)
        self._install_header_controls()

        completed = _consume_completed_update_marker(self.current_version)
        previous = _consume_previous_update_result(self.current_version)
        if completed:
            QTimer.singleShot(900, self._show_completed_update)
        elif previous:
            QTimer.singleShot(900, lambda payload=previous: self._show_previous_update_result(payload))

    @staticmethod
    def enabled() -> bool:
        return bool(getattr(sys, "frozen", False)) and os.getenv(
            "ECOMMERCE_AGENT_DISABLE_UPDATE_CHECK", ""
        ).strip().lower() not in {"1", "true", "yes"}

    def _bring_to_front(self, dialog: QMessageBox | QProgressDialog) -> None:
        try:
            if self.window.isMinimized():
                self.window.showNormal()
            self.window.raise_()
            self.window.activateWindow()
            dialog.raise_()
            dialog.activateWindow()
        except RuntimeError:
            pass

    def _show_update_message(
        self,
        icon: QMessageBox.Icon,
        text: str,
        *,
        informative: str = "",
        details: str = "",
    ) -> int:
        box = QMessageBox(self.window)
        box.setWindowTitle("Listing Studio 更新")
        box.setIcon(icon)
        box.setText(text)
        if informative:
            box.setInformativeText(informative)
        if details:
            box.setDetailedText(details)
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.setWindowModality(Qt.WindowModality.ApplicationModal)
        box.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        QTimer.singleShot(0, lambda: self._bring_to_front(box))
        return box.exec()

    def _show_completed_update(self) -> None:
        self._show_update_message(
            QMessageBox.Icon.Information,
            "更新已完成",
            informative=f"Listing Studio 已重新打开。\n\n当前版本：v{self.current_version}",
        )

    def _show_previous_update_result(self, payload: dict[str, Any]) -> None:
        kind = str(payload.get("kind") or "failure")
        status = str(payload.get("status") or "unknown")
        detail = str(payload.get("detail") or "unknown update failure")
        target = str(payload.get("target_version") or "").strip()
        if kind == "success":
            self._show_completed_update()
            return
        if kind == "warning":
            self._show_update_message(
                QMessageBox.Icon.Warning,
                "上次更新已安装，但自动收尾没有完整完成",
                informative=(
                    f"当前版本：v{self.current_version}\n"
                    f"目标版本：v{target or self.current_version}\n\n"
                    "程序现在可以继续使用；如需再次确认，可点击“检查更新”。"
                ),
                details=f"status={status}\n{detail}",
            )
            return
        self._show_update_message(
            QMessageBox.Icon.Critical,
            "上次自动更新未完成",
            informative=(
                f"目标版本：v{target or '?'}\n"
                f"当前版本：v{self.current_version}\n\n"
                "程序已恢复打开，没有继续执行失败的安装。请重新检查更新。"
            ),
            details=f"status={status}\n{detail}\nlog={updater_log_path()}",
        )

    def _install_header_controls(self) -> None:
        root = self.window.centralWidget()
        outer = root.layout() if isinstance(root, QWidget) else None
        header = outer.itemAt(0).layout() if outer is not None and outer.count() else None
        if not isinstance(header, QBoxLayout):
            return

        version_label = QLabel(f"v{self.current_version}  ·  STABLE", self.window)
        version_label.setObjectName("appVersionBadge")
        version_label.setFixedHeight(32)
        version_label.setToolTip(f"当前版本 v{self.current_version} · Stable channel")
        version_label.setStyleSheet(
            "QLabel#appVersionBadge { padding: 0 10px; color: rgba(255,255,255,194);"
            " background: rgba(20,24,34,70); border: 1px solid rgba(255,255,255,28);"
            " border-radius: 10px; font-size: 11px; font-weight: 650; }"
        )

        check_button = QPushButton("检查更新", self.window)
        check_button.setObjectName("checkUpdateButton")
        check_button.setFixedHeight(32)
        check_button.setToolTip("检查 Stable 更新通道")
        check_button.setStyleSheet(
            "QPushButton#checkUpdateButton { min-height: 30px; max-height: 30px; padding: 0 12px;"
            " color: rgba(255,255,255,220); background: rgba(20,24,34,62);"
            " border: 1px solid rgba(255,255,255,26); border-radius: 10px;"
            " font-size: 11px; font-weight: 650; }"
            "QPushButton#checkUpdateButton:hover { background: rgba(255,255,255,30);"
            " border-color: rgba(255,255,255,44); }"
            "QPushButton#checkUpdateButton:pressed { background: rgba(255,255,255,20); }"
        )
        check_button.clicked.connect(self.manual_check_for_updates)

        phase_badge = getattr(self.window, "phase_badge", None)
        index = header.indexOf(phase_badge) if isinstance(phase_badge, QWidget) else -1
        if index < 0:
            index = header.count()
        header.insertWidget(index, version_label, 0, Qt.AlignmentFlag.AlignBottom)
        header.insertWidget(index + 1, check_button, 0, Qt.AlignmentFlag.AlignBottom)
        self._version_label = version_label
        self._check_button = check_button
        self.window.app_version_label = version_label  # type: ignore[attr-defined]
        self.window.check_update_button = check_button  # type: ignore[attr-defined]

    def _set_manual_check_busy(self, busy: bool) -> None:
        if self._check_button is None:
            return
        try:
            self._check_button.setEnabled(not busy)
            self._check_button.setText("检查中…" if busy else "检查更新")
        except RuntimeError:
            pass

    def _finish_check(self, message: str | None = None, *, warning: bool = False) -> None:
        manual = self._manual_check
        self._manual_check = False
        if manual:
            self._set_manual_check_busy(False)
            if message:
                self._show_update_message(
                    QMessageBox.Icon.Warning if warning else QMessageBox.Icon.Information,
                    message,
                )

    def _ensure_progress(self, label: str, *, cancellable: bool) -> QProgressDialog:
        progress = self._progress
        if progress is None:
            progress = QProgressDialog(
                _phase_label(label),
                "取消" if cancellable else "",
                0,
                0,
                self.window,
            )
            progress.setWindowTitle("Listing Studio 更新")
            progress.setAutoClose(False)
            progress.setAutoReset(False)
            progress.setMinimumDuration(0)
            progress.setMinimumWidth(470)
            self._progress = progress
        else:
            progress.setLabelText(_phase_label(label))
            progress.setRange(0, 0)
            progress.setCancelButtonText("取消" if cancellable else "")
        progress.setWindowModality(Qt.WindowModality.ApplicationModal)
        progress.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        progress.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, cancellable)
        progress.show()
        self._bring_to_front(progress)
        return progress

    def _set_progress_phase(self, label: str, *, cancellable: bool = False) -> None:
        progress = self._ensure_progress(label, cancellable=cancellable)
        progress.setRange(0, 0)
        progress.setValue(0)
        self._bring_to_front(progress)

    def _close_progress(self) -> None:
        progress = self._progress
        self._progress = None
        if progress is not None:
            progress.close()
            progress.deleteLater()

    def schedule_startup_check(self) -> None:
        if self.enabled():
            QTimer.singleShot(_CHECK_DELAY_MS, self.check_for_updates)
            if not self._auto_timer.isActive():
                self._auto_timer.start()

    def manual_check_for_updates(self) -> None:
        if not self.enabled():
            self._show_update_message(
                QMessageBox.Icon.Information,
                f"当前版本 v{self.current_version}",
                informative="源码开发模式不连接 Stable 更新通道。",
            )
            return
        if self._reply is not None:
            self._show_update_message(QMessageBox.Icon.Information, "更新检查或下载正在进行。")
            return
        self.check_for_updates(manual=True)

    def check_for_updates(self, *, manual: bool = False) -> None:
        if not self.enabled():
            if manual:
                self.manual_check_for_updates()
            return
        if self._reply is not None:
            if manual:
                self._show_update_message(QMessageBox.Icon.Information, "更新检查或下载正在进行。")
            return
        self._manual_check = manual
        if manual:
            self._set_manual_check_busy(True)
        self._release_attempt = 0
        self._begin_release_request()

    def _begin_release_request(self) -> None:
        if self._reply is not None:
            return
        self._release_attempt += 1
        reply = self.network.get(_api_request(_LATEST_RELEASE_API, self.current_version))
        self._reply = reply
        reply.finished.connect(lambda: self._release_finished(reply))

    def _release_finished(self, reply: QNetworkReply) -> None:
        if reply is not self._reply:
            return
        error = reply.error()
        if error != QNetworkReply.NetworkError.NoError:
            diagnostic = _reply_diagnostic(
                "release_metadata", reply, attempt=self._release_attempt, source="github_api"
            )
            _write_diagnostic(diagnostic)
            self._reply = None
            reply.deleteLater()
            if error in _RETRIABLE_ERRORS and self._release_attempt < _MAX_RELEASE_ATTEMPTS:
                QTimer.singleShot(_RETRY_DELAY_MS, self._begin_release_request)
                return
            self._finish_check(
                _failure_message("检查更新失败，请检查网络后重试。", diagnostic),
                warning=True,
            )
            return

        parse_error = ""
        manifest_api = ""
        manifest_browser = ""
        try:
            payload = json.loads(bytes(reply.readAll()).decode("utf-8"))
            tag = str(payload.get("tag_name") or "").strip() if isinstance(payload, dict) else ""
            release_version = tag[1:] if tag.startswith("v") else ""
            if not _STABLE_VERSION_RE.fullmatch(release_version):
                raise ValueError("latest release tag is not a Stable semantic version")
            expected_setup = f"EcommerceAgent-Setup-{release_version}.exe"
            setup_found = False
            for asset in payload.get("assets", []) if isinstance(payload, dict) else []:
                if not isinstance(asset, dict):
                    continue
                name = str(asset.get("name") or "")
                if name == _MANIFEST_ASSET:
                    candidate_api = str(asset.get("url") or "").strip()
                    candidate_browser = str(asset.get("browser_download_url") or "").strip()
                    api_url = QUrl(candidate_api)
                    browser_url = QUrl(candidate_browser)
                    if (
                        api_url.isValid()
                        and api_url.scheme().lower() == "https"
                        and api_url.host().lower() == "api.github.com"
                        and api_url.path().startswith(f"/repos/{_REPOSITORY}/releases/assets/")
                    ):
                        manifest_api = candidate_api
                    if (
                        browser_url.isValid()
                        and browser_url.scheme().lower() == "https"
                        and browser_url.host().lower() == "github.com"
                        and browser_url.path()
                        == f"/{_REPOSITORY}/releases/download/v{release_version}/{_MANIFEST_ASSET}"
                    ):
                        manifest_browser = candidate_browser
                elif name == expected_setup:
                    setup_found = True
                    self._release_installer_url = str(asset.get("browser_download_url") or "").strip()
                    digest = str(asset.get("digest") or "").strip().lower()
                    self._release_installer_digest = (
                        digest.split(":", 1)[1] if digest.startswith("sha256:") else ""
                    )
                    try:
                        self._release_installer_size = int(asset.get("size") or 0)
                    except (TypeError, ValueError):
                        self._release_installer_size = 0
            if not manifest_api and not manifest_browser:
                raise ValueError("update.json asset is missing")
            if not setup_found:
                raise ValueError(f"{expected_setup} asset is missing")
            self._release_version = release_version
            self._release_target = str(payload.get("target_commitish") or "").strip()
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            parse_error = f"Stable Release 不完整或无效：{exc}"
        finally:
            self._reply = None
            reply.deleteLater()

        if parse_error:
            _write_diagnostic(
                {
                    "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "stage": "release_metadata_parse",
                    "attempt": self._release_attempt,
                    "error": parse_error,
                }
            )
            self._finish_check(parse_error, warning=True)
            return

        self._manifest_asset_api_url = manifest_api
        self._manifest_browser_url = manifest_browser
        self._manifest_source = "asset_api" if manifest_api else "browser_url"
        self._manifest_attempt = 0
        self._begin_manifest_request()

    def _begin_manifest_request(self) -> None:
        if self._reply is not None:
            return
        if self._manifest_source == "asset_api":
            url = self._manifest_asset_api_url
            github_api = True
        else:
            url = self._manifest_browser_url
            github_api = False
        if not url:
            self._finish_check("当前 Stable Release 没有有效的更新清单。", warning=True)
            return
        self._manifest_attempt += 1
        reply = self.network.get(_asset_request(url, self.current_version, github_api=github_api))
        self._reply = reply
        reply.finished.connect(lambda: self._manifest_finished(reply))

    def _validate_manifest(self, payload: Any) -> tuple[dict[str, Any] | None, bool, str]:
        if not isinstance(payload, dict):
            return None, False, "Stable 更新清单不是 JSON object。"
        if payload.get("schema_version") != 1 or payload.get("channel") != "stable":
            return None, False, "Stable 更新清单 schema/channel 无效。"

        latest = str(payload.get("version") or "").strip().lstrip("v")
        if not _STABLE_VERSION_RE.fullmatch(latest) or latest != self._release_version:
            return None, False, "更新版本与 latest Release 不一致。"
        latest_key = _version_key(latest)
        current_key = _version_key(self.current_version)
        if latest_key is None or current_key is None:
            return None, False, "更新版本信息无效。"

        required_value = payload.get("required", False)
        if not isinstance(required_value, bool):
            return None, False, "required 更新策略字段无效。"

        minimum = str(payload.get("min_supported_version") or "").strip().lstrip("v")
        minimum_key = _version_key(minimum)
        if not _STABLE_VERSION_RE.fullmatch(minimum) or minimum_key is None:
            return None, False, "min_supported_version 无效。"
        if minimum_key > latest_key:
            return None, False, "min_supported_version 不能高于发布版本。"

        checksum = str(payload.get("installer_sha256") or "").strip().lower()
        if not _SHA256_RE.fullmatch(checksum):
            return None, False, "更新文件 SHA-256 无效。"
        if self._release_installer_digest and checksum != self._release_installer_digest:
            return None, False, "update.json SHA-256 与 GitHub Release asset digest 不一致。"

        expected_path = _expected_github_installer_path(latest)
        installer_url = str(payload.get("installer_url") or self._release_installer_url).strip()
        installer_qurl = QUrl(installer_url)
        if (
            not installer_qurl.isValid()
            or installer_qurl.scheme().lower() != "https"
            or installer_qurl.host().lower() != "github.com"
            or installer_qurl.path() != expected_path
        ):
            return None, False, "更新安装包 URL 与目标版本不一致。"
        release_qurl = QUrl(self._release_installer_url)
        if release_qurl.isValid() and release_qurl.path() != expected_path:
            return None, False, "GitHub Release 安装包路径与版本不一致。"

        installer_size = payload.get("installer_size")
        if installer_size is not None:
            try:
                size_value = int(installer_size)
            except (TypeError, ValueError):
                return None, False, "installer_size 无效。"
            if size_value <= 0:
                return None, False, "installer_size 无效。"
            if self._release_installer_size and size_value != self._release_installer_size:
                return None, False, "update.json 安装包大小与 Release asset 不一致。"
            payload["installer_size"] = size_value
        elif self._release_installer_size:
            payload["installer_size"] = self._release_installer_size

        source_commit = str(payload.get("source_commit") or "").strip().lower()
        release_target = self._release_target.lower()
        if source_commit and re.fullmatch(r"[0-9a-f]{40}", source_commit):
            if re.fullmatch(r"[0-9a-f]{40}", release_target) and source_commit != release_target:
                return None, False, "更新清单 source_commit 与 Release target 不一致。"

        delivery = str(payload.get("delivery") or "portal").strip().lower()
        if delivery == "portal":
            portal_url = str(payload.get("portal_url") or _PORTAL_URL).strip()
            url = QUrl(portal_url)
            if (
                not url.isValid()
                or url.scheme().lower() != "https"
                or url.host().lower() not in _PORTAL_HOSTS
            ):
                return None, False, "更新下载门户地址无效。"
            payload["portal_url"] = portal_url
        elif delivery == "github":
            pass
        else:
            return None, False, "更新下载方式无效。"

        payload["version"] = latest
        payload["installer_sha256"] = checksum
        payload["installer_url"] = installer_url
        payload["delivery"] = delivery
        payload["required"] = required_value
        payload["min_supported_version"] = minimum
        return payload, latest_key <= current_key, ""

    def _manifest_finished(self, reply: QNetworkReply) -> None:
        if reply is not self._reply:
            return
        error = reply.error()
        if error != QNetworkReply.NetworkError.NoError:
            diagnostic = _reply_diagnostic(
                "manifest_download",
                reply,
                attempt=self._manifest_attempt,
                source=self._manifest_source,
            )
            _write_diagnostic(diagnostic)
            self._reply = None
            reply.deleteLater()
            if error == QNetworkReply.NetworkError.OperationCanceledError:
                self._finish_check()
                return
            if error in _RETRIABLE_ERRORS and self._manifest_attempt < _MAX_MANIFEST_ATTEMPTS:
                QTimer.singleShot(_RETRY_DELAY_MS, self._begin_manifest_request)
                return
            if self._manifest_source == "asset_api" and self._manifest_browser_url:
                self._manifest_source = "browser_url"
                self._manifest_attempt = 0
                QTimer.singleShot(_RETRY_DELAY_MS, self._begin_manifest_request)
                return
            self._finish_check(
                _failure_message("更新清单下载失败，请稍后重试。", diagnostic),
                warning=True,
            )
            return

        try:
            payload = json.loads(bytes(reply.readAll()).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            payload = None
        finally:
            self._reply = None
            reply.deleteLater()

        manifest, up_to_date, validation_error = self._validate_manifest(payload)
        if validation_error:
            self._finish_check(validation_error, warning=True)
            return
        if manifest is None:
            self._finish_check("没有找到可用更新。", warning=True)
            return
        if up_to_date:
            self._finish_check(f"当前已是最新版本 v{self.current_version}。")
            return

        latest = str(manifest["version"])
        manual = self._manual_check
        if not manual and latest == self._last_prompted_version:
            self._finish_check()
            return
        self._last_prompted_version = latest
        self._finish_check()
        self._prompt_for_update(manifest)

    def _prompt_for_update(self, manifest: dict[str, Any]) -> None:
        latest = str(manifest["version"])
        current_key = _version_key(self.current_version) or (0, 0, 0)
        minimum_key = _version_key(str(manifest["min_supported_version"]))
        required = bool(manifest["required"]) or (
            minimum_key is not None and current_key < minimum_key
        )
        notes = str(manifest.get("notes") or "").strip()
        delivery = str(manifest["delivery"])

        box = QMessageBox(self.window)
        box.setWindowTitle("Listing Studio 更新")
        box.setIcon(QMessageBox.Icon.Warning if required else QMessageBox.Icon.Information)
        box.setText(f"发现新版本 v{latest}")
        detail = notes or "包含稳定性与功能更新。"
        detail = (
            f"{detail}\n\n"
            "接下来会持续显示更新状态：\n"
            "1. 准备下载权限\n"
            "2. 下载更新包\n"
            "3. 校验文件完整性\n"
            "4. 验证独立更新器并安全安装\n\n"
            "安装阶段会关闭 Listing Studio；独立更新器确认安装版本后会重新打开程序。\n"
            "请先结束正在执行的上架任务。"
        )
        if delivery == "portal":
            detail += "\n\n更新包会使用当前已授权账号进行安全下载。"
        if required:
            detail = f"这是关键更新，需要更新后继续使用。\n\n{detail}"
        box.setInformativeText(detail)
        box.setDetailedText(
            f"Current: {self.current_version}\nLatest: {latest}\n"
            f"Channel: stable\nDelivery: {delivery}\nSHA256: {manifest['installer_sha256']}"
        )
        update_button = box.addButton("立即更新", QMessageBox.ButtonRole.AcceptRole)
        fallback_button = box.addButton(
            "退出程序" if required else "稍后", QMessageBox.ButtonRole.RejectRole
        )
        box.setDefaultButton(update_button)
        box.setWindowModality(Qt.WindowModality.ApplicationModal)
        box.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        if required:
            box.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
        QTimer.singleShot(0, lambda: self._bring_to_front(box))
        box.exec()

        if box.clickedButton() is update_button:
            if delivery == "portal":
                self._download_portal_update(manifest, required=required)
            else:
                self._download_update(manifest, attempt=1)
            return
        if required:
            QApplication.quit()
        elif box.clickedButton() is fallback_button:
            return

    def _download_portal_update(
        self,
        manifest: dict[str, Any],
        *,
        required: bool,
        attempt: int = 1,
    ) -> None:
        self._set_progress_phase(
            "正在验证更新权限…" if attempt == 1 else "步骤 1/4 · 权限请求暂时失败，正在重试…",
            cancellable=True,
        )
        access = self.access_controller
        token = ""
        if access is not None and hasattr(access, "bearer_token"):
            try:
                token = str(access.bearer_token() or "")
            except Exception:
                token = ""
        if not token:
            self._close_progress()
            self._open_portal_update(manifest, required=required)
            return

        function_url = str(getattr(access, "download_function_url", "") or "")
        publishable_key = str(getattr(access, "publishable_key", "") or "")
        endpoint = QUrl(function_url)
        if (
            not endpoint.isValid()
            or endpoint.scheme().lower() != "https"
            or endpoint.host().lower() not in _PRIVATE_DOWNLOAD_HOSTS
            or not publishable_key.startswith("sb_publishable_")
        ):
            self._portal_failure("安全下载服务配置无效。", required=required)
            return

        request = QNetworkRequest(endpoint)
        request.setRawHeader(b"Content-Type", b"application/json")
        request.setRawHeader(b"Accept", b"application/json")
        request.setRawHeader(b"Authorization", f"Bearer {token}".encode("utf-8"))
        request.setRawHeader(b"apikey", publishable_key.encode("utf-8"))
        request.setRawHeader(
            b"User-Agent", f"EcommerceAgent/{self.current_version}".encode("ascii", "ignore")
        )
        request.setTransferTimeout(_NETWORK_TIMEOUT_MS)
        request.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy,
        )
        body = QByteArray(
            json.dumps(
                {"action": "download", "version": str(manifest["version"])},
                separators=(",", ":"),
            ).encode("utf-8")
        )
        reply = self.network.post(request, body)
        self._reply = reply
        if self._progress is not None:
            self._progress.canceled.connect(reply.abort)
        reply.finished.connect(
            lambda: self._portal_download_finished(
                reply, manifest, required=required, attempt=attempt
            )
        )

    def _portal_download_finished(
        self,
        reply: QNetworkReply,
        manifest: dict[str, Any],
        *,
        required: bool,
        attempt: int,
    ) -> None:
        if reply is not self._reply:
            return
        error = reply.error()
        if error != QNetworkReply.NetworkError.NoError:
            diagnostic = _reply_diagnostic(
                "portal_authorization", reply, attempt=attempt, source="portal-download"
            )
            _write_diagnostic(diagnostic)
            self._reply = None
            reply.deleteLater()
            if error == QNetworkReply.NetworkError.OperationCanceledError:
                self._close_progress()
                return
            if error in _RETRIABLE_ERRORS and attempt < _MAX_PORTAL_ATTEMPTS:
                QTimer.singleShot(
                    _RETRY_DELAY_MS,
                    lambda: self._download_portal_update(
                        manifest, required=required, attempt=attempt + 1
                    ),
                )
                return
            self._portal_failure(
                _failure_message("授权下载请求失败，请稍后重试。", diagnostic),
                required=required,
            )
            return

        try:
            payload = json.loads(bytes(reply.readAll()).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("invalid response")
            installer_url = str(payload.get("url") or "").strip()
            remote_version = str(payload.get("version") or "").strip().lstrip("v")
            expected_version = str(manifest["version"]).strip().lstrip("v")
            url = QUrl(installer_url)
            if (
                not url.isValid()
                or url.scheme().lower() != "https"
                or url.host().lower() != "github.com"
                or url.path() != _expected_github_installer_path(expected_version)
                or remote_version != expected_version
            ):
                raise ValueError("authorized release mismatch")
            private_manifest = dict(manifest)
            private_manifest["installer_url"] = installer_url
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            self._reply = None
            reply.deleteLater()
            self._portal_failure("安全下载信息校验失败。", required=required)
            return

        self._reply = None
        reply.deleteLater()
        self._set_progress_phase("授权完成，正在准备下载…", cancellable=True)
        self._download_update(private_manifest, attempt=1)

    def _portal_failure(self, message: str, *, required: bool) -> None:
        self._last_prompted_version = None
        self._close_progress()
        self._show_update_message(
            QMessageBox.Icon.Critical if required else QMessageBox.Icon.Warning,
            message,
        )
        if required:
            QApplication.quit()

    def _open_portal_update(self, manifest: dict[str, Any], *, required: bool) -> None:
        box = QMessageBox(self.window)
        box.setWindowTitle("Listing Studio 更新")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText("需要转到安全下载页继续更新")
        box.setInformativeText(
            "当前登录状态暂时无法完成内置授权下载。\n\n"
            + (
                "点击下面的按钮后会打开官方安全下载页，Listing Studio 随后退出。"
                if required
                else "点击下面的按钮后会打开官方安全下载页，Listing Studio 会保持打开。"
            )
        )
        open_button = box.addButton(
            "打开下载页并退出" if required else "打开下载页",
            QMessageBox.ButtonRole.AcceptRole,
        )
        cancel_button = box.addButton(
            "退出程序" if required else "稍后", QMessageBox.ButtonRole.RejectRole
        )
        box.setDefaultButton(open_button)
        box.setWindowModality(Qt.WindowModality.ApplicationModal)
        box.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        if required:
            box.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
        QTimer.singleShot(0, lambda: self._bring_to_front(box))
        box.exec()
        if box.clickedButton() is not open_button:
            if required and box.clickedButton() is cancel_button:
                QApplication.quit()
            return
        url = QUrl(str(manifest.get("portal_url") or _PORTAL_URL))
        query = QUrlQuery(url)
        query.addQueryItem("version", str(manifest["version"]))
        url.setQuery(query)
        if not QDesktopServices.openUrl(url):
            self._show_update_message(
                QMessageBox.Icon.Warning,
                "无法打开安全下载页。",
                informative="请稍后重新检查更新。",
            )
            return
        if required:
            QApplication.quit()

    def _download_update(self, manifest: dict[str, Any], *, attempt: int) -> None:
        version = str(manifest["version"])
        checksum = str(manifest["installer_sha256"])
        target = update_download_dir() / f"EcommerceAgent-Setup-{version}-{checksum[:10]}.exe"
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass

        file = QFile(str(target))
        if not file.open(QIODevice.OpenModeFlag.WriteOnly | QIODevice.OpenModeFlag.Truncate):
            self._last_prompted_version = None
            self._close_progress()
            self._show_update_message(QMessageBox.Icon.Warning, "无法创建更新安装文件。")
            return

        progress = self._ensure_progress(
            (
                f"步骤 2/4 · 正在下载 v{version} 更新包…"
                if attempt == 1
                else f"步骤 2/4 · 网络中断，正在重新下载 v{version}…"
            ),
            cancellable=True,
        )
        progress.setRange(0, 100)
        progress.setValue(0)

        installer_url = str(manifest["installer_url"])
        github_api = QUrl(installer_url).host().lower() == "api.github.com"
        reply = self.network.get(
            _asset_request(installer_url, self.current_version, github_api=github_api)
        )
        self._reply = reply
        self._download_file = file
        self._download_path = target
        self._download_write_failed = False
        reply.readyRead.connect(lambda: self._write_download(reply))
        reply.downloadProgress.connect(self._download_progress)
        reply.finished.connect(
            lambda: self._download_finished(reply, manifest, attempt=attempt)
        )
        progress.canceled.connect(reply.abort)
        progress.show()
        self._bring_to_front(progress)

    def _write_download(self, reply: QNetworkReply) -> None:
        file = self._download_file
        if file is None or not file.isOpen() or self._download_write_failed:
            return
        data = reply.readAll()
        if not data:
            return
        written = file.write(data)
        if written != data.size():
            self._download_write_failed = True
            reply.abort()

    def _download_progress(self, received: int, total: int) -> None:
        progress = self._progress
        if progress is None:
            return
        if total > 0:
            percent = max(0, min(100, round(received * 100 / total)))
            progress.setRange(0, 100)
            progress.setValue(percent)
            progress.setLabelText(
                "步骤 2/4 · 正在下载更新… "
                f"{percent}%   ·   {received / 1024 / 1024:.1f} / {total / 1024 / 1024:.1f} MB"
            )
        else:
            progress.setRange(0, 0)
            progress.setLabelText("步骤 2/4 · 正在下载更新…")

    def _download_finished(
        self,
        reply: QNetworkReply,
        manifest: dict[str, Any],
        *,
        attempt: int,
    ) -> None:
        if reply is not self._reply:
            return
        self._write_download(reply)
        self._reply = None
        file = self._download_file
        if file is not None and file.isOpen():
            file.close()
        self._download_file = None
        path = self._download_path
        self._download_path = None
        write_failed = self._download_write_failed
        self._download_write_failed = False
        error = reply.error()
        diagnostic: dict[str, Any] | None = None
        if error not in {
            QNetworkReply.NetworkError.NoError,
            QNetworkReply.NetworkError.OperationCanceledError,
        }:
            diagnostic = _reply_diagnostic(
                "installer_download", reply, attempt=attempt, source="release_asset"
            )
            _write_diagnostic(diagnostic)
        reply.deleteLater()

        if write_failed:
            if path is not None:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            self._last_prompted_version = None
            self._close_progress()
            self._show_update_message(
                QMessageBox.Icon.Critical,
                "更新文件写入失败。",
                informative="请检查系统盘可用空间和当前用户的 AppData 写入权限后重试。",
            )
            return

        if error != QNetworkReply.NetworkError.NoError or path is None:
            if path is not None:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            if error == QNetworkReply.NetworkError.OperationCanceledError:
                self._close_progress()
                return
            if error in _RETRIABLE_ERRORS and attempt < _MAX_INSTALLER_ATTEMPTS:
                QTimer.singleShot(
                    _RETRY_DELAY_MS,
                    lambda: self._download_update(manifest, attempt=attempt + 1),
                )
                return
            self._last_prompted_version = None
            self._close_progress()
            self._show_update_message(
                QMessageBox.Icon.Warning,
                _failure_message("更新下载失败，请稍后重试。", diagnostic),
            )
            return

        expected_size = int(manifest.get("installer_size") or 0)
        try:
            actual_size = path.stat().st_size
        except OSError:
            actual_size = -1
        if expected_size > 0 and actual_size != expected_size:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            if attempt < _MAX_INSTALLER_ATTEMPTS:
                QTimer.singleShot(
                    _RETRY_DELAY_MS,
                    lambda: self._download_update(manifest, attempt=attempt + 1),
                )
                return
            self._last_prompted_version = None
            self._close_progress()
            self._show_update_message(
                QMessageBox.Icon.Critical,
                "更新包大小与 Release 清单不一致，已取消安装。",
            )
            return

        self._set_progress_phase(
            "步骤 3/4 · 下载完成，正在校验更新包完整性…",
            cancellable=False,
        )
        QTimer.singleShot(0, lambda: self._verify_and_install(path, manifest))

    def _handoff_installer(
        self,
        path: Path,
        manifest: dict[str, Any],
        arguments: list[str],
    ) -> tuple[bool, str]:
        updater = prepare_standalone_updater()
        if updater is None:
            return False, "独立更新器自检失败；主程序已保持打开，没有进入安装。"

        stable = stable_updater_dir()
        token = f"{os.getpid()}-{int(time.time() * 1000)}"
        ack_path = stable / f"handoff-{token}.json"
        job_path = stable / f"pending-update-{token}.json"
        result_path = updater_result_path()
        for stale in (ack_path, result_path):
            try:
                stale.unlink(missing_ok=True)
            except OSError:
                pass

        app_executable = Path(sys.executable).resolve()
        version_file = app_executable.parent / "_internal" / "packaging" / "VERSION"
        job = UpdaterJob(
            installer=str(path.resolve()),
            target_version=str(manifest["version"]),
            app_pid=os.getpid(),
            app_image_name=app_executable.stem,
            app_executable=str(app_executable),
            version_file=str(version_file),
            installer_sha256=str(manifest["installer_sha256"]),
            arguments=arguments,
            worker_pids=owned_qprocess_pids(self.window),
            ack_path=str(ack_path),
            marker_path=str(update_marker_path()),
            log_path=str(updater_log_path()),
            result_path=str(result_path),
        )
        try:
            job.save(job_path)
            proc = subprocess.Popen(
                [str(updater), "--job", str(job_path)],
                creationflags=(
                    getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                    | getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
                ),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            return False, f"无法启动独立更新器：{exc}"

        deadline = time.monotonic() + _HANDOFF_ACK_TIMEOUT_S
        accepted = False
        while time.monotonic() < deadline:
            QApplication.processEvents()
            if ack_path.is_file():
                try:
                    ack = json.loads(ack_path.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
                    ack = {}
                if (
                    isinstance(ack, dict)
                    and ack.get("status") == "accepted"
                    and int(ack.get("job_version") or 0) == JOB_VERSION
                    and str(ack.get("target_version") or "") == str(manifest["version"])
                ):
                    accepted = True
                    break
            if proc.poll() is not None:
                break
            time.sleep(0.05)

        try:
            ack_path.unlink(missing_ok=True)
        except OSError:
            pass

        if accepted:
            # Catch an immediate post-ACK crash before allowing the GUI to exit.
            end = time.monotonic() + 0.2
            while time.monotonic() < end:
                QApplication.processEvents()
                if proc.poll() is not None:
                    accepted = False
                    break
                time.sleep(0.02)
        if accepted:
            return True, ""

        if proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=1)
            except (OSError, subprocess.SubprocessError):
                try:
                    proc.kill()
                except OSError:
                    pass

        detail = "独立更新器没有确认接管安装任务；主程序已保持打开。"
        if result_path.is_file():
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
                if isinstance(result, dict) and result.get("detail"):
                    detail = f"独立更新器预检失败：{result.get('detail')}"
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
                pass
        return False, detail

    def _verify_and_install(self, path: Path, manifest: dict[str, Any]) -> None:
        digest = _sha256_file(path)
        if digest.lower() != str(manifest["installer_sha256"]).lower():
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            self._last_prompted_version = None
            self._close_progress()
            self._show_update_message(
                QMessageBox.Icon.Critical,
                "更新文件 SHA-256 校验失败，已取消安装。",
            )
            return

        version = str(manifest["version"]).strip().lstrip("v")
        if not _write_update_marker(version):
            self._last_prompted_version = None
            self._close_progress()
            self._show_update_message(
                QMessageBox.Icon.Critical,
                "无法写入更新交接状态，已取消安装。",
                informative="主程序保持打开；请检查 AppData 写入权限后重试。",
            )
            return

        self._set_progress_phase(
            "步骤 4/4 · 校验通过，正在启动更新执行器…\n"
            "执行器确认接管后 Listing Studio 才会关闭。",
            cancellable=False,
        )
        arguments = [
            "/SILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            "/CLOSEAPPLICATIONS",
            "/NORESTARTAPPLICATIONS",
        ]
        started, detail = self._handoff_installer(path, manifest, arguments)
        if not started:
            try:
                update_marker_path().unlink(missing_ok=True)
            except OSError:
                pass
            self._last_prompted_version = None
            self._close_progress()
            self._show_update_message(
                QMessageBox.Icon.Critical,
                "自动更新没有进入安装阶段。",
                informative=detail,
                details=f"Updater log: {updater_log_path()}",
            )
            return

        self._close_progress()
        QTimer.singleShot(120, QApplication.quit)


def install_application_updater(
    window: QMainWindow,
    *,
    access_controller: Any | None = None,
) -> ApplicationUpdater:
    existing = getattr(window, "_application_updater", None)
    if isinstance(existing, ApplicationUpdater):
        return existing
    updater = ApplicationUpdater(window, access_controller=access_controller)
    window._application_updater = updater  # type: ignore[attr-defined]
    updater.schedule_startup_check()
    return updater


__all__ = [
    "ApplicationUpdater",
    "ensure_updater_installed",
    "install_application_updater",
    "installed_application_version",
]
