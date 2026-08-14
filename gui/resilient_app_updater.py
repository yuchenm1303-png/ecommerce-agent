from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PySide6.QtCore import QFile, QIODevice, QTimer, QUrl
from PySide6.QtNetwork import QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import QMainWindow, QMessageBox

from gui.app_updater import ApplicationUpdater as _BaseApplicationUpdater


_REPOSITORY = "yuchenm1303-png/ecommerce-agent"
_LATEST_RELEASE_API = f"https://api.github.com/repos/{_REPOSITORY}/releases/latest"
_MANIFEST_ASSET = "update.json"
_NETWORK_TIMEOUT_MS = 20_000
_RETRY_DELAY_MS = 650
_MAX_RELEASE_ATTEMPTS = 2
_MAX_MANIFEST_ATTEMPTS = 2
_DIAGNOSTIC_LOG = "updater-network.jsonl"

_RETRIABLE_ERRORS = {
    QNetworkReply.NetworkError.RemoteHostClosedError,
    QNetworkReply.NetworkError.HostNotFoundError,
    QNetworkReply.NetworkError.TimeoutError,
    QNetworkReply.NetworkError.TemporaryNetworkFailureError,
    QNetworkReply.NetworkError.NetworkSessionFailedError,
    QNetworkReply.NetworkError.UnknownNetworkError,
}


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
    return _request(
        url,
        version,
        accept=b"application/vnd.github+json",
        github_api=True,
    )


def _asset_request(url: str, version: str, *, github_api: bool) -> QNetworkRequest:
    return _request(
        url,
        version,
        accept=b"application/octet-stream",
        github_api=github_api,
    )


def _safe_url(value: str | QUrl) -> str:
    url = QUrl(value) if isinstance(value, str) else QUrl(value)
    if not url.isValid():
        return ""
    url.setQuery("")
    url.setFragment("")
    return url.toString()


def _diagnostic_path() -> Path:
    base = Path(os.getenv("LOCALAPPDATA") or tempfile.gettempdir()) / "ListingStudio"
    return base / _DIAGNOSTIC_LOG


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


def _reply_diagnostic(stage: str, reply: QNetworkReply, *, attempt: int, source: str = "") -> dict[str, Any]:
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


def _failure_message(title: str, diagnostic: dict[str, Any]) -> str:
    status = diagnostic.get("http_status")
    error_name = str(diagnostic.get("qt_error_name") or "NetworkError")
    error_text = str(diagnostic.get("error") or "unknown network error")
    detail = f"{error_name}: {error_text}"
    if status:
        detail = f"HTTP {status} · {detail}"
    return (
        f"{title}\n\n"
        f"{detail}\n\n"
        f"已记录诊断：{_diagnostic_path()}"
    )


def _valid_manifest_api_url(value: str) -> bool:
    url = QUrl(value)
    return (
        url.isValid()
        and url.scheme().lower() == "https"
        and url.host().lower() == "api.github.com"
        and url.path().startswith(f"/repos/{_REPOSITORY}/releases/assets/")
    )


def _valid_manifest_browser_url(value: str) -> bool:
    url = QUrl(value)
    return (
        url.isValid()
        and url.scheme().lower() == "https"
        and url.host().lower() == "github.com"
        and url.path().startswith(f"/{_REPOSITORY}/releases/download/")
        and url.path().endswith(f"/{_MANIFEST_ASSET}")
    )


class ApplicationUpdater(_BaseApplicationUpdater):
    """Updater with transport-specific GitHub requests, fallback and bounded retry."""

    def __init__(self, window: QMainWindow, *, access_controller: Any | None = None) -> None:
        self._release_attempt = 0
        self._manifest_attempt = 0
        self._manifest_source = ""
        self._manifest_asset_api_url = ""
        self._manifest_browser_url = ""
        super().__init__(window, access_controller=access_controller)

    def check_for_updates(self, *, manual: bool = False) -> None:
        if not self.enabled():
            if manual:
                self.manual_check_for_updates()
            return
        if self._reply is not None:
            if manual:
                QMessageBox.information(self.window, "Listing Studio 更新", "更新检查或下载正在进行。")
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
        reply.finished.connect(lambda: self._release_finished_resilient(reply))

    def _release_finished_resilient(self, reply: QNetworkReply) -> None:
        if reply is not self._reply:
            return

        error = reply.error()
        if error != QNetworkReply.NetworkError.NoError:
            diagnostic = _reply_diagnostic(
                "release_metadata",
                reply,
                attempt=self._release_attempt,
                source="github_api",
            )
            _write_diagnostic(diagnostic)
            self._reply = None
            reply.deleteLater()
            if error in _RETRIABLE_ERRORS and self._release_attempt < _MAX_RELEASE_ATTEMPTS:
                QTimer.singleShot(_RETRY_DELAY_MS, self._begin_release_request)
                return
            self._finish_check(_failure_message("检查更新失败，请检查网络后重试。", diagnostic), warning=True)
            return

        asset_api_url = ""
        browser_url = ""
        parse_error = ""
        try:
            payload = json.loads(bytes(reply.readAll()).decode("utf-8"))
            assets = payload.get("assets", []) if isinstance(payload, dict) else []
            for asset in assets if isinstance(assets, list) else []:
                if not isinstance(asset, dict) or asset.get("name") != _MANIFEST_ASSET:
                    continue
                candidate_api = str(asset.get("url") or "").strip()
                candidate_browser = str(asset.get("browser_download_url") or "").strip()
                if _valid_manifest_api_url(candidate_api):
                    asset_api_url = candidate_api
                if _valid_manifest_browser_url(candidate_browser):
                    browser_url = candidate_browser
                break
            if not asset_api_url and not browser_url:
                parse_error = "当前 Stable Release 没有有效的更新清单。"
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            parse_error = "更新信息解析失败，请稍后重试。"
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

        self._manifest_asset_api_url = asset_api_url
        self._manifest_browser_url = browser_url
        self._manifest_source = "asset_api" if asset_api_url else "browser_url"
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
        reply.finished.connect(lambda: self._manifest_finished_resilient(reply))

    def _manifest_finished_resilient(self, reply: QNetworkReply) -> None:
        if reply is not self._reply:
            return
        error = reply.error()
        if error == QNetworkReply.NetworkError.NoError:
            # Delegate validation, version policy and prompt behavior to the canonical updater.
            super()._manifest_finished(reply)
            return

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

    def _download_update(self, manifest: dict[str, Any]) -> None:
        version = str(manifest["version"])
        checksum = str(manifest["installer_sha256"])
        target = Path(tempfile.gettempdir()) / f"EcommerceAgent-Setup-{version}-{checksum[:10]}.exe"
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass

        file = QFile(str(target))
        if not file.open(QIODevice.OpenModeFlag.WriteOnly | QIODevice.OpenModeFlag.Truncate):
            self._close_progress()
            QMessageBox.warning(self.window, "Listing Studio 更新", "无法创建更新安装文件。")
            return

        progress = self._ensure_progress(
            f"正在下载 v{version.lstrip('v')} 更新包…",
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

        reply.readyRead.connect(lambda: self._write_download(reply))
        reply.downloadProgress.connect(self._download_progress)
        reply.finished.connect(lambda: self._download_finished_resilient(reply, manifest))
        progress.canceled.connect(reply.abort)
        progress.show()

    def _download_finished_resilient(
        self,
        reply: QNetworkReply,
        manifest: dict[str, Any],
    ) -> None:
        error = reply.error()
        if error not in {
            QNetworkReply.NetworkError.NoError,
            QNetworkReply.NetworkError.OperationCanceledError,
        }:
            _write_diagnostic(
                _reply_diagnostic(
                    "installer_download",
                    reply,
                    attempt=1,
                    source="release_asset",
                )
            )
        super()._download_finished(reply, manifest)


def install_application_updater(
    window: QMainWindow,
    *,
    access_controller: Any | None = None,
) -> ApplicationUpdater:
    existing = getattr(window, "_application_updater", None)
    if isinstance(existing, _BaseApplicationUpdater):
        return existing  # type: ignore[return-value]
    updater = ApplicationUpdater(window, access_controller=access_controller)
    window._application_updater = updater  # type: ignore[attr-defined]
    updater.schedule_startup_check()
    return updater


__all__ = [
    "ApplicationUpdater",
    "install_application_updater",
]
