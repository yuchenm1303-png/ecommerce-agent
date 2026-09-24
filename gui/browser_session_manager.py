from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QLabel, QVBoxLayout

from app.browser_session import (
    DEFAULT_CDP_PORT,
    DEFAULT_START_URL,
    launch_detached_edge,
)
from app.cdp_automation_health import (
    cdp_endpoint_token,
    clear_cdp_poison,
    looks_like_cdp_transport_failure,
    mark_cdp_poisoned,
    poison_matches_current_generation,
    probe_cdp_automation,
)
from app.update_browser_gate import close_managed_browser


class ManagedMakroBrowser(QObject):
    """Own the formal GUI's one long-lived Makro browser generation.

    All endpoint and Playwright health I/O runs on lifecycle worker threads. The
    Qt presentation thread only consumes the cached browser-generation state, so
    clicking Single/Batch never waits behind HTTP or connect_over_cdp probes.

    The actual business child process still owns the canonical task-time CDP
    attach. Idle health monitoring exists to keep the managed browser warm and to
    rotate poisoned generations before the next task, not to duplicate that attach
    synchronously on every Start click.
    """

    status_changed = Signal(str, str)
    endpoint_observed = Signal(str)
    _POLL_MS = 1500
    _PROBE_TIMEOUT_MS = 6_000
    _HOT_STATES = {"READY", "LOGIN"}

    def __init__(self, window: Any, *, port: int = DEFAULT_CDP_PORT) -> None:
        super().__init__(window)
        self.window = window
        self.project_root = Path(window.project_root).resolve()
        self.port = int(port)
        self.profile_dir = self.project_root / "browser_profiles" / "makro-edge"

        self._state = "CHECKING"
        self._detail = "正在后台检查 Makro 浏览器"
        self._launch_lock = threading.Lock()
        self._launch_thread: threading.Thread | None = None
        self._endpoint_thread: threading.Thread | None = None
        self._poison_thread: threading.Thread | None = None
        self._instance_token = ""
        self._generation = 0
        self._single_prepared_generation: int | None = None
        self._batch_prepare_generation: int | None = None
        self._update_quiesced = False

        self._original_single_start: Callable[..., Any] = window.runner.start
        self._original_real_start: Callable[..., Any] = window.execution_runner.start
        self._batch_controller = getattr(getattr(window, "batch_workspace", None), "controller", None)
        self._original_batch_prepare: Callable[..., Any] | None = None
        self._original_batch_execute: Callable[..., Any] | None = None

        self._single_label: QLabel | None = None
        self._batch_label: QLabel | None = None
        self._install_status_labels()
        self.status_changed.connect(self._apply_status)
        self.endpoint_observed.connect(self._apply_endpoint_observation)

        window.runner.start = self._start_single
        window.execution_runner.start = self._start_real
        window.runner.completed.connect(self._single_prepared)
        window.runner.failed.connect(self._observe_failure)
        window.execution_runner.failed.connect(self._observe_failure)

        if self._batch_controller is not None:
            self._original_batch_prepare = self._batch_controller.start_prepare
            self._original_batch_execute = self._batch_controller.start_execution
            self._batch_controller.start_prepare = self._start_batch_prepare
            self._batch_controller.start_execution = self._start_batch_execute
            self._batch_controller.failed.connect(self._observe_failure)

        self._emit_status("CHECKING", "正在后台验证 Makro Browser 自动化控制")
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(self._POLL_MS)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start()
        # No network call occurs in __init__. Let the first paint/event turn finish,
        # then warm the browser entirely on the lifecycle worker.
        QTimer.singleShot(0, self.ensure_async)

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def update_quiesced(self) -> bool:
        return self._update_quiesced

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
            "CHECKING": "#f4cb7a",
            "STARTING": "#f4cb7a",
            "LOGIN": "#f4cb7a",
            "POISONED": "#f18da0",
            "RECOVERING": "#8fc5ff",
            "OFFLINE": "#f18da0",
            "ERROR": "#f18da0",
            "UPDATING": "#8fc5ff",
        }.get(state, "rgba(255,255,255,180)")
        text = f"Makro Browser · {state} · {detail}"
        if self._single_label is not None:
            self._single_label.setText(text)
            self._single_label.setStyleSheet(f"color: {color};")
        if self._batch_label is not None:
            self._batch_label.setText(text + " · shared login / owned tabs")
            self._batch_label.setStyleSheet(f"color: {color};")

    def _cdp_instance_token(self) -> str:
        """Blocking endpoint I/O; callers must be lifecycle workers only."""

        return cdp_endpoint_token(self.port, timeout_s=0.45)

    def _observe_instance(self, token: str) -> None:
        token = str(token or "").strip()
        if not token:
            return
        if self._instance_token and token != self._instance_token:
            self._generation += 1
        self._instance_token = token

    def _observe_recovered_instance(self, token: str, previous_token: str) -> None:
        """Commit a recovered generation even if Chromium reuses its WS token."""

        token = str(token or "").strip()
        previous = str(previous_token or "").strip()
        if previous and token == previous:
            self._generation += 1
            self._instance_token = token
            return
        self._observe_instance(token)

    @staticmethod
    def _looks_like_login_failure(message: str) -> bool:
        text = str(message or "").casefold()
        return any(
            marker in text
            for marker in (
                "登录", "login", "authentication", "authenticated", "sign in", "signin"
            )
        )

    def _looks_like_makro_cdp_failure(self, message: str) -> bool:
        text = str(message or "").casefold()
        if "9333" in text and str(self.port) not in text:
            return False
        return looks_like_cdp_transport_failure(message)

    def _mark_poisoned_async(self, message: str) -> None:
        if self._poison_thread is not None and self._poison_thread.is_alive():
            return
        cached_token = self._instance_token

        def worker() -> None:
            mark_cdp_poisoned(
                self.port,
                endpoint_token=cached_token,
                reason=message,
            )

        self._poison_thread = threading.Thread(
            target=worker,
            name="managed-makro-edge-poison-record",
            daemon=True,
        )
        self._poison_thread.start()

    def _observe_failure(self, message: str) -> None:
        if self._looks_like_login_failure(message):
            self._emit_status(
                "LOGIN",
                "需要登录 · 请在已打开的 Makro Browser 完成正常登录后直接重试",
            )
            return
        if self._looks_like_makro_cdp_failure(message):
            # Recording a poison marker may need an endpoint lookup when no cached
            # token exists, so even the error path keeps that I/O off the Qt thread.
            self._mark_poisoned_async(message)
            self._emit_status(
                "POISONED",
                "浏览器自动化通道失效 · 当前任务会安全失败，空闲后自动恢复",
            )

    def _is_busy(self) -> bool:
        if self.window.runner.is_running or self.window.execution_runner.is_running:
            return True
        batch_workspace = getattr(self.window, "batch_workspace", None)
        return bool(batch_workspace is not None and batch_workspace.is_running)

    def is_busy(self) -> bool:
        return self._is_busy()

    def begin_update_quiesce(self) -> tuple[bool, str]:
        if self._update_quiesced:
            return True, ""
        if self._is_busy():
            return False, "当前仍有商品准备、真实填写或批量任务正在运行。请等待任务结束后再更新。"
        self._update_quiesced = True
        try:
            self._poll_timer.stop()
        except RuntimeError:
            pass
        self._emit_status("UPDATING", "更新准备中 · 已暂停浏览器自动恢复和新任务启动")
        return True, ""

    def wait_for_update_quiesce(self, timeout_s: float = 20.0) -> tuple[bool, str]:
        timeout = max(0.0, float(timeout_s))
        current = threading.current_thread()
        threads = (self._launch_thread, self._endpoint_thread, self._poison_thread)
        for thread in threads:
            if thread is not None and thread.is_alive() and thread is not current:
                thread.join(timeout=timeout)
        if any(thread is not None and thread.is_alive() for thread in threads):
            return False, "Makro Browser 后台生命周期线程仍在运行，无法安全进入安装阶段。"
        if not self._update_quiesced:
            return False, "更新冻结状态意外解除。"
        return True, ""

    def resume_after_update_failure(self) -> None:
        if not self._update_quiesced:
            return
        self._update_quiesced = False
        try:
            if not self._poll_timer.isActive():
                self._poll_timer.start()
        except RuntimeError:
            pass
        self._emit_status("STARTING", "更新未进入安装 · 正在恢复 Makro Browser")
        self.ensure_async()

    def _assert_task_start_allowed(self) -> None:
        if self._update_quiesced:
            raise RuntimeError("Listing Studio 正在准备更新，暂时不能启动新的上架任务。")
        if self._is_busy():
            raise RuntimeError(
                "已有 Single/真实填写/Batch 浏览器工作域正在运行。"
                "为保证一个 Makro Edge 只存在一个正式自动化工作域，请等待当前任务结束后再启动。"
            )

    def _assert_cached_browser_ready(self) -> None:
        """O(1) task-start gate using the background lifecycle's cached state."""

        token = self._instance_token
        if (
            token
            and self._state in self._HOT_STATES
            and not poison_matches_current_generation(self.port, token)
        ):
            return
        # Never turn Start back into a network wait. Ask the lifecycle worker to
        # recover/verify and fail immediately with a deterministic UI message.
        self.ensure_async()
        raise RuntimeError(
            "Makro Browser 正在后台启动或验证自动化控制。"
            "界面不会再为浏览器探针卡住；状态变为 READY 后请直接再次点击启动。"
        )

    def _recover_poisoned_locked(self, reason: str, previous_token: str) -> bool:
        if self._is_busy():
            raise RuntimeError(
                "Makro Browser automation generation 已失效，但当前任务仍在运行；"
                "为保护现场不会中途重启，任务结束后会自动恢复。"
            )

        self._emit_status("RECOVERING", f"{reason} · 正在安全重建 Makro Browser")
        closed = close_managed_browser(port=self.port, deadline_s=6.0)
        if not closed.ok:
            self._emit_status("ERROR", f"无法安全关闭失效浏览器：{closed.detail}")
            raise RuntimeError(
                "Makro Browser automation 已失效，但无法证明并安全关闭专用 Edge；"
                f"已保留现场。{closed.detail}"
            )

        launch_detached_edge(
            profile_dir=self.profile_dir,
            port=self.port,
            start_url=DEFAULT_START_URL,
        )
        probe = probe_cdp_automation(self.port, timeout_ms=self._PROBE_TIMEOUT_MS)
        if not probe.automation_ready:
            mark_cdp_poisoned(
                self.port,
                endpoint_token=probe.endpoint_token,
                reason=probe.error or "recovery automation probe failed",
            )
            self._emit_status("POISONED", "新浏览器已启动，但自动化控制仍不可用")
            raise RuntimeError(
                "Makro Browser 已用原 Profile 重启，但 Playwright 自动化探针仍失败："
                f"{probe.error or probe.state}"
            )

        self._observe_recovered_instance(probe.endpoint_token, previous_token)
        clear_cdp_poison(self.port)
        self._emit_status(
            "READY",
            "Makro Browser 已安全重建 · 原登录 Profile 已保留 · 旧 owned tabs 已失效",
        )
        return True

    def ensure_ready(self, reason: str = "task") -> bool:
        """Blocking lifecycle operation. It is never called by a Start click."""

        if self._update_quiesced:
            raise RuntimeError("Makro Browser 已进入更新冻结状态，不能在安装前重新启动。")

        with self._launch_lock:
            if self._update_quiesced:
                raise RuntimeError("Makro Browser 已进入更新冻结状态，已取消后台恢复。")

            token = self._cdp_instance_token()
            if token:
                if self._is_busy():
                    if poison_matches_current_generation(self.port, token):
                        raise RuntimeError(
                            "Makro Browser automation generation 已标记失效；"
                            "当前任务结束前不会中途重启。"
                        )
                    return False

                probe = probe_cdp_automation(self.port, timeout_ms=self._PROBE_TIMEOUT_MS)
                if probe.automation_ready:
                    self._observe_instance(probe.endpoint_token)
                    clear_cdp_poison(self.port)
                    self._emit_status("READY", "Makro Browser 自动化就绪 · 复用现有登录会话")
                    return False

                mark_cdp_poisoned(
                    self.port,
                    endpoint_token=probe.endpoint_token or token,
                    reason=probe.error or probe.state,
                )
                self._emit_status(
                    "POISONED",
                    "CDP 端点仍在线，但 Playwright 自动化不可用",
                )
                return self._recover_poisoned_locked(reason, token)

            if self._is_busy():
                raise RuntimeError(
                    "Makro Browser 在任务运行期间离线；为保护当前页面现场不会中途启动新 generation。"
                )

            self._emit_status("STARTING", f"{reason} · 正在启动 Makro Browser")
            try:
                launch_detached_edge(
                    profile_dir=self.profile_dir,
                    port=self.port,
                    start_url=DEFAULT_START_URL,
                )
                probe = probe_cdp_automation(self.port, timeout_ms=self._PROBE_TIMEOUT_MS)
                if not probe.automation_ready:
                    mark_cdp_poisoned(
                        self.port,
                        endpoint_token=probe.endpoint_token,
                        reason=probe.error or probe.state,
                    )
                    raise RuntimeError(
                        "Edge 已启动但 Playwright automation probe 未通过："
                        f"{probe.error or probe.state}"
                    )
                self._observe_instance(probe.endpoint_token)
                clear_cdp_poison(self.port)
                self._emit_status("READY", "Makro Browser 已自动启动 · 专用登录 Profile 已载入")
                return True
            except Exception as exc:
                if self._update_quiesced:
                    raise RuntimeError("更新准备期间已取消 Makro Browser 自动恢复。") from exc
                if self._state != "POISONED":
                    self._emit_status("ERROR", f"Makro Browser 启动失败：{exc}")
                raise RuntimeError(
                    "无法建立可自动化的 Makro Browser。请确认 Microsoft Edge 已安装且专用浏览器端口未被其他程序占用。"
                ) from exc

    def ensure_async(self) -> None:
        """Schedule full readiness work without performing any caller-thread I/O."""

        if self._update_quiesced or self._is_busy():
            return
        if self._launch_thread is not None and self._launch_thread.is_alive():
            return
        if (
            self._instance_token
            and self._state in self._HOT_STATES
            and not poison_matches_current_generation(self.port, self._instance_token)
        ):
            return

        def worker() -> None:
            try:
                self.ensure_ready("GUI background recovery")
            except Exception:
                pass

        self._launch_thread = threading.Thread(
            target=worker,
            name="managed-makro-edge-lifecycle",
            daemon=True,
        )
        self._launch_thread.start()

    def _poll(self) -> None:
        """Launch one non-blocking endpoint observation for this timer tick."""

        if self._update_quiesced:
            return
        if self._endpoint_thread is not None and self._endpoint_thread.is_alive():
            return

        def worker() -> None:
            token = self._cdp_instance_token()
            self.endpoint_observed.emit(token)

        self._endpoint_thread = threading.Thread(
            target=worker,
            name="managed-makro-edge-endpoint-poll",
            daemon=True,
        )
        self._endpoint_thread.start()

    def _apply_endpoint_observation(self, token: str) -> None:
        if self._update_quiesced:
            return
        token = str(token or "").strip()
        if token:
            previous_generation = self._generation
            self._observe_instance(token)
            generation_changed = self._generation != previous_generation

            if generation_changed:
                if self._is_busy():
                    self._emit_status(
                        "OFFLINE",
                        "浏览器在任务运行中被替换 · 当前任务会安全失败，空闲后自动恢复",
                    )
                    return
                self._emit_status(
                    "CHECKING",
                    "检测到新的 Makro Browser generation · 正在验证自动化控制",
                )
                self.ensure_async()
                return

            if poison_matches_current_generation(self.port, token):
                if self._is_busy():
                    if self._state != "POISONED":
                        self._emit_status(
                            "POISONED",
                            "自动化通道已失效 · 当前任务会安全失败，空闲后自动恢复",
                        )
                    return
                if self._state != "RECOVERING":
                    self._emit_status("RECOVERING", "失效 browser generation 正在等待安全重建")
                self.ensure_async()
                return

            if self._state not in self._HOT_STATES:
                if self._is_busy():
                    return
                self._emit_status("CHECKING", "CDP 端点在线 · 正在验证 Playwright automation")
                self.ensure_async()
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
        self._assert_task_start_allowed()
        self._assert_cached_browser_ready()
        self._single_prepared_generation = None
        return self._original_single_start(config, mode=mode)

    def _single_prepared(self, result: Any) -> None:
        if getattr(result, "plan_summary", None):
            self._single_prepared_generation = self._generation
        self._emit_status("READY", "Makro Browser 已连接 · 当前商品准备完成")

    def _start_real(self, config: Any) -> Any:
        self._assert_task_start_allowed()
        self._assert_cached_browser_ready()
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
        self._assert_task_start_allowed()
        self._assert_cached_browser_ready()
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
        self._assert_task_start_allowed()
        self._assert_cached_browser_ready()
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
