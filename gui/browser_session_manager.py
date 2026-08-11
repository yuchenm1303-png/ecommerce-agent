from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QLabel, QVBoxLayout

from app.browser_session import (
    DEFAULT_CDP_PORT,
    DEFAULT_START_URL,
    cdp_endpoint,
    is_cdp_ready,
    launch_detached_edge,
)


class ManagedMakroBrowser(QObject):
    """Own the formal GUI's single long-lived Makro browser session.

    The browser is still a detached Edge process with a localhost CDP endpoint,
    but the GUI now treats that as an implementation detail:

    - one dedicated ``browser_profiles/makro-edge`` profile is reused;
    - the browser starts automatically when absent;
    - Single and Batch share that one browser/login session;
    - Batch continues to isolate jobs by owned tabs/target ids, not browsers;
    - a browser restart invalidates prepared Step-3/tab ownership and therefore
      refuses a stale real-execution attempt instead of guessing another tab.

    The manager never reads cookies/tokens and never closes the external Edge.
    Authentication remains inside Edge's dedicated profile. If Makro expires the
    login, the user completes the normal Makro login page in that browser and
    retries; every new tab in the same profile shares that authenticated session.
    """

    status_changed = Signal(str, str)

    _POLL_MS = 1500

    def __init__(self, window: Any, *, port: int = DEFAULT_CDP_PORT) -> None:
        super().__init__(window)
        self.window = window
        self.project_root = Path(window.project_root).resolve()
        self.port = int(port)
        self.profile_dir = self.project_root / "browser_profiles" / "makro-edge"

        self._state = "CHECKING"
        self._detail = "正在检查 Makro 浏览器"
        self._launch_lock = threading.Lock()
        self._launch_thread: threading.Thread | None = None
        self._instance_token = ""
        self._generation = 0
        self._single_prepared_generation: int | None = None
        self._batch_prepare_generation: int | None = None

        self._original_single_start: Callable[..., Any] = window.runner.start
        self._original_real_start: Callable[..., Any] = window.execution_runner.start
        self._batch_controller = getattr(getattr(window, "batch_workspace", None), "controller", None)
        self._original_batch_prepare: Callable[..., Any] | None = None
        self._original_batch_execute: Callable[..., Any] | None = None

        self._single_label: QLabel | None = None
        self._batch_label: QLabel | None = None
        self._install_status_labels()
        self.status_changed.connect(self._apply_status)

        # Wrap only the formal GUI entry points. Core CLI/browser helpers remain
        # unchanged and available for developer/external-CDP diagnostics.
        window.runner.start = self._start_single
        window.execution_runner.start = self._start_real
        window.runner.completed.connect(self._single_prepared)

        if self._batch_controller is not None:
            self._original_batch_prepare = self._batch_controller.start_prepare
            self._original_batch_execute = self._batch_controller.start_execution
            self._batch_controller.start_prepare = self._start_batch_prepare
            self._batch_controller.start_execution = self._start_batch_execute

        token = self._cdp_instance_token()
        if token:
            self._instance_token = token
            self._emit_status("READY", "Makro Browser 已连接 · 登录会话由专用 Profile 复用")
        else:
            self._emit_status("STARTING", "Makro Browser 未运行 · 正在自动启动")

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(self._POLL_MS)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start()

        # Do not block the first GUI paint while Edge starts.
        QTimer.singleShot(250, self.ensure_async)

    @property
    def generation(self) -> int:
        return self._generation

    def _install_status_labels(self) -> None:
        makro_port = getattr(self.window, "makro_port", None)
        if makro_port is not None:
            makro_port.setVisible(False)
            parent = makro_port.parentWidget()
            layout = parent.layout() if parent is not None else None
            if isinstance(layout, QVBoxLayout):
                self._single_label = QLabel("Makro Browser · CHECKING")
                self._single_label.setObjectName("cardHint")
                self._single_label.setToolTip(
                    "正式 GUI 自动管理一个专用 Makro Edge。端口与 Profile 属于高级实现细节；"
                    "同一个浏览器里的 Single/Batch 标签页共享一次登录。"
                )
                layout.addWidget(self._single_label)

        batch_workspace = getattr(self.window, "batch_workspace", None)
        batch_port = getattr(batch_workspace, "makro_port", None)
        if batch_port is not None:
            batch_port.setVisible(False)
            parent = batch_port.parentWidget()
            layout = parent.layout() if parent is not None else None
            if isinstance(layout, QVBoxLayout):
                self._batch_label = QLabel(
                    "Makro Browser · CHECKING · 一个登录会话，多 owned tabs 并行"
                )
                self._batch_label.setObjectName("cardHint")
                self._batch_label.setToolTip(
                    "Batch 不会为每个商品启动一个浏览器。所有 worker 共用同一 Makro Profile/登录，"
                    "每个商品只拥有自己的标签页 targetId。"
                )
                layout.addWidget(self._batch_label)

    def _emit_status(self, state: str, detail: str) -> None:
        self._state = str(state).upper()
        self._detail = str(detail)
        self.status_changed.emit(self._state, self._detail)

    def _apply_status(self, state: str, detail: str) -> None:
        color = {
            "READY": "#8fe1b9",
            "STARTING": "#f4cb7a",
            "OFFLINE": "#f18da0",
            "ERROR": "#f18da0",
        }.get(state, "rgba(255,255,255,180)")
        text = f"Makro Browser · {state} · {detail}"
        if self._single_label is not None:
            self._single_label.setText(text)
            self._single_label.setStyleSheet(f"color: {color};")
        if self._batch_label is not None:
            self._batch_label.setText(text + " · shared login / owned tabs")
            self._batch_label.setStyleSheet(f"color: {color};")

    def _cdp_instance_token(self) -> str:
        """Return Chromium browser-instance UUID from /json/version.

        ``webSocketDebuggerUrl`` changes whenever Edge is restarted. Tracking it
        lets us distinguish a healthy reconnect from a stale Step-3/tab token.
        """

        try:
            with urllib.request.urlopen(
                f"{cdp_endpoint(self.port)}/json/version", timeout=0.45
            ) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace"))
            return str(payload.get("webSocketDebuggerUrl") or "").strip()
        except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError):
            return ""

    def _observe_instance(self, token: str) -> None:
        token = str(token or "").strip()
        if not token:
            return
        if self._instance_token and token != self._instance_token:
            self._generation += 1
        self._instance_token = token

    def _is_busy(self) -> bool:
        if self.window.runner.is_running or self.window.execution_runner.is_running:
            return True
        batch_workspace = getattr(self.window, "batch_workspace", None)
        return bool(batch_workspace is not None and batch_workspace.is_running)

    def ensure_ready(self, reason: str = "task") -> bool:
        """Synchronously ensure the one managed Makro Edge exists.

        Returns True only when this call had to launch Edge. It never launches a
        second browser when the managed CDP endpoint is already healthy.
        """

        token = self._cdp_instance_token()
        if token:
            self._observe_instance(token)
            self._emit_status("READY", "Makro Browser 已连接 · 复用现有登录会话")
            return False

        self._emit_status("STARTING", f"{reason} · 正在恢复 Makro Browser")
        with self._launch_lock:
            token = self._cdp_instance_token()
            if token:
                self._observe_instance(token)
                self._emit_status("READY", "Makro Browser 已恢复 · 复用专用 Profile")
                return False
            try:
                launch_detached_edge(
                    profile_dir=self.profile_dir,
                    port=self.port,
                    start_url=DEFAULT_START_URL,
                )
                token = self._cdp_instance_token()
                if not token or not is_cdp_ready(self.port):
                    raise RuntimeError("Edge 已启动但 CDP 尚未就绪")
                self._observe_instance(token)
                self._emit_status("READY", "Makro Browser 已自动启动 · 专用登录 Profile 已载入")
                return True
            except Exception as exc:
                self._emit_status("ERROR", f"Makro Browser 启动失败：{exc}")
                raise RuntimeError(
                    "无法自动启动 Makro Browser。请确认 Microsoft Edge 已安装且 9222 未被其他程序占用。"
                ) from exc

    def ensure_async(self) -> None:
        if self._launch_thread is not None and self._launch_thread.is_alive():
            return
        if self._cdp_instance_token():
            self._poll()
            return

        def worker() -> None:
            try:
                self.ensure_ready("GUI startup")
            except Exception:
                # Status is already surfaced by ensure_ready; task start will
                # raise the same actionable error if the user tries to proceed.
                pass

        self._launch_thread = threading.Thread(
            target=worker,
            name="managed-makro-edge-start",
            daemon=True,
        )
        self._launch_thread.start()

    def _poll(self) -> None:
        token = self._cdp_instance_token()
        if token:
            previous_generation = self._generation
            self._observe_instance(token)
            if self._generation != previous_generation:
                self._emit_status(
                    "READY",
                    "Makro Browser 已重新连接 · 旧准备页/owned tab 已失效",
                )
            elif self._state != "READY":
                self._emit_status("READY", "Makro Browser 已连接 · 复用专用 Profile")
            return

        if self._is_busy():
            if self._state != "OFFLINE":
                self._emit_status(
                    "OFFLINE",
                    "浏览器在任务运行中被关闭 · 当前任务会安全失败，空闲后自动恢复",
                )
            return
        if self._state != "STARTING":
            self._emit_status("STARTING", "Makro Browser 已关闭 · 正在自动恢复")
        self.ensure_async()

    def _start_single(self, config: Any, *, mode: str = "full") -> Any:
        self._single_prepared_generation = None
        self.ensure_ready("Single preparation")
        return self._original_single_start(config, mode=mode)

    def _single_prepared(self, result: Any) -> None:
        if getattr(result, "plan_summary", None):
            self._single_prepared_generation = self._generation

    def _start_real(self, config: Any) -> Any:
        self.ensure_ready("Real execution")
        if (
            self._single_prepared_generation is not None
            and self._single_prepared_generation != self._generation
        ):
            raise RuntimeError(
                "Makro Browser 在准备完成后被重启过。原 Step 3 标签页/草稿现场不能安全复用；"
                "浏览器已经自动恢复，请重新执行“完整流程准备”后再开始真实填写。"
            )
        return self._original_real_start(config)

    def _start_batch_prepare(
        self,
        urls: list[str],
        config: Any,
        *,
        prepare_concurrency: int = 2,
    ) -> Any:
        assert self._original_batch_prepare is not None
        self.ensure_ready("Batch preparation")
        self._batch_prepare_generation = self._generation
        return self._original_batch_prepare(
            urls,
            config,
            prepare_concurrency=prepare_concurrency,
        )

    def _start_batch_execute(
        self,
        *,
        allow_save: bool,
        upload_images: bool,
        execute_concurrency: int = 2,
    ) -> Any:
        assert self._original_batch_execute is not None
        self.ensure_ready("Batch execution")
        if (
            self._batch_prepare_generation is not None
            and self._batch_prepare_generation != self._generation
        ):
            raise RuntimeError(
                "Makro Browser 在 Batch 准备后被重启过，之前保存的 owned-tab targetId 已失效。"
                "浏览器已经自动恢复，请重新“批量准备”以重新创建每个商品的独立标签页。"
            )
        return self._original_batch_execute(
            allow_save=allow_save,
            upload_images=upload_images,
            execute_concurrency=execute_concurrency,
        )


def install_managed_makro_browser(window: Any) -> ManagedMakroBrowser:
    existing = getattr(window, "_managed_makro_browser", None)
    if isinstance(existing, ManagedMakroBrowser):
        return existing
    manager = ManagedMakroBrowser(window)
    window._managed_makro_browser = manager
    return manager


__all__ = ["ManagedMakroBrowser", "install_managed_makro_browser"]
