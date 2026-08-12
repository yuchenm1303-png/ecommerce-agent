from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

from .result_loader import load_run_result


@dataclass(slots=True)
class RunnerConfig:
    product_url: str
    expected_vertical: str = ""
    makro_cdp_port: int = 9222
    source_cdp_port: int = 9333
    source_use_current_page: bool = False
    provider: str = "openai-compatible"
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    local_model: str = "qwen3.7-plus"
    fact_model: str = "qwen3.7-max"
    web_model: str = "qwen3.7-max"
    api_key_env: str = "AI_API_KEY"


_PHASE_META = {
    "scan": (1, "Source Capture"),
    "cold": (2, "Step 1 · Vertical"),
    "hot": (3, "Step 2 · Brand"),
    "plan": (4, "Step 3 · Resolve / Fill Plan"),
}
_MODE_PHASES = {
    "step1": ("scan", "cold"),
    "step2": ("scan", "hot"),
    "step3": ("scan", "plan"),
    "full": ("scan", "cold", "hot", "plan"),
}
_PHASE_LINE = re.compile(
    r"^GUI_PHASE\s+(scan|cold|hot|plan)\s+(START|COMPLETE|FAILED|SKIPPED)"
    r"(?:\s+detail=(.*))?$"
)


class ReadOnlyRunner(QObject):
    """GUI bridge to the current staged one-link acceptance workflow."""

    log = Signal(str)
    phase_changed = Signal(str)
    running_changed = Signal(bool)
    result_updated = Signal(object)
    completed = Signal(object)
    failed = Signal(str)
    progress_changed = Signal(int, str)
    phase_event = Signal(object)
    command_started = Signal(object)

    def __init__(self, project_root: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.project_root = project_root.resolve()
        self.process: QProcess | None = None
        self.config: RunnerConfig | None = None
        self.run_dir: Path | None = None
        self.mode = "full"
        self.current_phase = "idle"
        self._stopping = False
        self._stdout_tail = ""
        self._phase_started: dict[str, tuple[float, str]] = {}
        self._completed_active: set[str] = set()

    @property
    def is_running(self) -> bool:
        return self.process is not None and self.process.state() != QProcess.NotRunning

    def _immediate_resume_url(self, config: RunnerConfig, mode: str) -> str:
        """Return the exact failed Step 2/3 URL only for a same-product retry.

        This is intentionally session-local. A successful run, a different URL,
        a different mode, or a missing/changed prior page disables automatic
        resume. ``makro_gui_workflow.py`` still verifies that this exact URL is
        the one unique Add Listing tab before adopting it.
        """

        if mode != "full" or self.mode != "full" or self.config is None or self.run_dir is None:
            return ""
        if self.config.product_url.strip() != config.product_url.strip():
            return ""
        manifest_path = self.run_dir / "run-manifest.json"
        if not manifest_path.is_file():
            return ""
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return ""
        if str(payload.get("status") or "").casefold() != "failed":
            return ""
        if str(payload.get("failed_phase") or "").casefold() not in {"step2", "step3"}:
            return ""
        failed_url = str(payload.get("failed_page_url") or "").strip()
        parsed = urlparse(failed_url)
        if parsed.scheme not in {"http", "https"} or parsed.hostname != "seller.makro.co.za":
            return ""
        return failed_url

    def start(self, config: RunnerConfig, *, mode: str = "full") -> None:
        if self.is_running:
            raise RuntimeError("Makro workflow 已在运行。")
        if mode not in _MODE_PHASES:
            raise ValueError(f"未知 workflow mode={mode!r}")
        self._validate_config(config)
        self._assert_makro_cdp_available(config.makro_cdp_port)
        resume_current_url = self._immediate_resume_url(config, mode)

        self.config = config
        self.mode = mode
        self.current_phase = "idle"
        self._stopping = False
        self._stdout_tail = ""
        self._phase_started.clear()
        self._completed_active.clear()

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        self.run_dir = self.project_root / "logs" / "gui-runs" / f"workflow-{mode}-{stamp}"
        self.run_dir.mkdir(parents=True, exist_ok=False)
        source_cache = self.run_dir / "_cache" / "source"
        semantic_cache = self.run_dir / "_cache" / "semantic"
        source_cache.mkdir(parents=True, exist_ok=True)
        semantic_cache.mkdir(parents=True, exist_ok=True)

        args = [
            "makro_gui_workflow.py",
            "--mode",
            mode,
            "--product-url",
            config.product_url,
            "--provider",
            config.provider,
            "--base-url",
            config.base_url,
            "--model",
            config.local_model,
            "--fact-model",
            config.fact_model,
            "--web-search-model",
            config.web_model,
            "--api-key-env",
            config.api_key_env,
            "--structured-mode",
            "json_object",
            "--disable-thinking",
            "--cdp-port",
            str(config.makro_cdp_port),
            "--source-cdp-port",
            str(config.source_cdp_port),
            "--source-cache-dir",
            str(source_cache),
            "--semantic-cache-dir",
            str(semantic_cache),
            "--output-dir",
            str(self.run_dir),
        ]
        if config.source_use_current_page:
            args.append("--source-use-current-page")
        if resume_current_url:
            args.extend(["--resume-current-url", resume_current_url])

        self.running_changed.emit(True)
        self.progress_changed.emit(0, f"{mode} · preparing")
        self._emit_log("===== GUI CURRENT MAKRO WORKFLOW =====")
        self._emit_log(f"mode={mode}")
        self._emit_log(f"run_dir={self.run_dir}")
        self._emit_log(f"product_url={config.product_url}")
        if resume_current_url:
            self._emit_log(
                "resume_current=YES · exact same-product failed Step 2/3 page will be verified before reuse"
            )
            self._emit_log(f"resume_page_url={resume_current_url}")
        else:
            self._emit_log("resume_current=NO · fresh/normal staged preparation")
        self._emit_log(
            "Backend: current one-link Step 1/2 + current Resolver cold/hot + current read-only Fill Plan."
        )
        self._emit_log("Safety: Step 3 writes=0 · Save=False · Send to QC=False.")
        self._start_process(args)

    def stop(self) -> None:
        if self.process is None or self.process.state() == QProcess.NotRunning:
            return
        self._stopping = True
        self._emit_log("Stop requested. Terminating current workflow subprocess...")
        self.process.terminate()
        if not self.process.waitForFinished(2500):
            self.process.kill()

    def _validate_config(self, config: RunnerConfig) -> None:
        parsed = urlparse(config.product_url.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("请输入完整的 1688 / 供应商 http(s) 商品 URL。")
        if not os.getenv(config.api_key_env, "").strip():
            raise ValueError(
                f"环境变量 {config.api_key_env} 未设置。GUI 不保存 API key，请先在当前终端/系统环境中设置。"
            )
        if not (1 <= int(config.makro_cdp_port) <= 65535):
            raise ValueError("Makro CDP 端口无效。")
        if not (1 <= int(config.source_cdp_port) <= 65535):
            raise ValueError("Source CDP 端口无效。")

    def _assert_makro_cdp_available(self, port: int) -> None:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/json/version", timeout=1.2
            ) as response:
                if response.status != 200:
                    raise RuntimeError(f"HTTP {response.status}")
        except Exception as exc:
            raise RuntimeError(
                f"Makro Browser/CDP 127.0.0.1:{port} 当前不可用；正式 GUI 浏览器管理器未能在任务开始前恢复它。"
            ) from exc

    def _start_process(self, args: list[str]) -> None:
        process = QProcess(self)
        self.process = process
        process.setWorkingDirectory(str(self.project_root))
        process.setProcessChannelMode(QProcess.MergedChannels)
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONUTF8", "1")
        environment.insert("PYTHONIOENCODING", "utf-8")
        process.setProcessEnvironment(environment)
        process.readyReadStandardOutput.connect(self._read_output)
        process.finished.connect(self._process_finished)
        process.errorOccurred.connect(self._process_error)

        full_argv = [sys.executable, *args]
        command = subprocess.list2cmdline(full_argv)
        self.command_started.emit(
            {
                "phase": "workflow",
                "command": command,
                "cwd": str(self.project_root),
                "output_dir": str(self.run_dir or ""),
                "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            }
        )
        self._emit_log("$ " + command)
        self._emit_log(f"cwd={self.project_root}")
        process.start(sys.executable, args)

    def _read_output(self) -> None:
        if self.process is None:
            return
        raw = bytes(self.process.readAllStandardOutput())
        if not raw:
            return
        text = self._stdout_tail + raw.decode("utf-8", errors="replace")
        self._stdout_tail = ""
        for part in text.splitlines(keepends=True):
            if part.endswith(("\n", "\r")):
                line = part.rstrip("\r\n")
                self._emit_log(line)
                self._observe_phase(line)
            else:
                self._stdout_tail = part

    def _flush_tail(self) -> None:
        if self._stdout_tail:
            line = self._stdout_tail
            self._stdout_tail = ""
            self._emit_log(line)
            self._observe_phase(line)

    def _observe_phase(self, line: str) -> None:
        match = _PHASE_LINE.match(line.strip())
        if match is None:
            return
        phase, state, detail = match.groups()
        state = state.casefold()
        detail = (detail or "").strip()
        index, label = _PHASE_META[phase]
        now_wall = datetime.now().astimezone().isoformat(timespec="seconds")

        if state == "start":
            self.current_phase = phase
            self._phase_started[phase] = (time.monotonic(), now_wall)
            self.phase_changed.emit(f"{label} · running")
            self.phase_event.emit(
                {
                    "phase": phase,
                    "index": index,
                    "label": label,
                    "status": "running",
                    "started_at": now_wall,
                    "output_dir": str(self.run_dir or ""),
                }
            )
            self._emit_progress(label)
            return

        started_mono, started_wall = self._phase_started.get(
            phase, (time.monotonic(), now_wall)
        )
        elapsed = max(0.0, time.monotonic() - started_mono)
        status = {
            "complete": "completed",
            "failed": "failed",
            "skipped": "skipped",
        }[state]
        if status == "completed" and phase in _MODE_PHASES[self.mode]:
            self._completed_active.add(phase)
        self.phase_event.emit(
            {
                "phase": phase,
                "index": index,
                "label": label,
                "status": status,
                "started_at": started_wall,
                "elapsed_s": elapsed,
                "error": detail if status == "failed" else "",
                "output_dir": str(self.run_dir or ""),
            }
        )
        if status == "failed":
            self.phase_changed.emit(f"{label} · failed")
        self._emit_progress(detail or label)

    def _emit_progress(self, detail: str) -> None:
        active = _MODE_PHASES[self.mode]
        percent = round(100 * len(self._completed_active) / max(1, len(active)))
        self.progress_changed.emit(percent, f"{self.mode} · {len(self._completed_active)}/{len(active)} · {detail}")

    def _process_finished(
        self, exit_code: int, _exit_status: QProcess.ExitStatus
    ) -> None:
        self._read_output()
        self._flush_tail()
        self.process = None
        if self._stopping:
            self.running_changed.emit(False)
            self.phase_changed.emit("已停止")
            self.failed.emit("测试已由用户停止；浏览器现场保留。")
            return

        if exit_code != 0:
            message = self._manifest_error() or f"{self.mode} workflow 退出码={exit_code}。请查看 Live Console。"
            self.running_changed.emit(False)
            self.phase_changed.emit("失败")
            self.failed.emit(message)
            return

        try:
            if self.run_dir is None:
                raise RuntimeError("workflow run_dir 未初始化")
            result = load_run_result(self.run_dir)
        except Exception as exc:
            self.running_changed.emit(False)
            self.failed.emit(f"workflow 已结束，但读取结果失败：{exc}")
            return

        self.progress_changed.emit(100, f"{self.mode} · complete")
        self.phase_changed.emit(f"完成 · {self.mode}")
        self.result_updated.emit(result)
        self.running_changed.emit(False)
        self.completed.emit(result)

    def _manifest_error(self) -> str:
        if self.run_dir is None:
            return ""
        path = self.run_dir / "run-manifest.json"
        if not path.is_file():
            return ""
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return ""
        return str(payload.get("error") or "").strip()

    def _process_error(self, error: QProcess.ProcessError) -> None:
        if self._stopping:
            return
        if error == QProcess.FailedToStart:
            self.process = None
            self.running_changed.emit(False)
            self.failed.emit("GUI workflow Python 子进程启动失败。")

    def _emit_log(self, line: str) -> None:
        self.log.emit(line)
        if self.run_dir is not None:
            with (self.run_dir / "gui-workflow.log").open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
