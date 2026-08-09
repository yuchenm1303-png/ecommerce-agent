from __future__ import annotations

import json
import os
import sys
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

from .result_loader import (
    latest_live_schema,
    latest_resolver_manifest,
    load_run_result,
)


@dataclass(slots=True)
class RunnerConfig:
    product_url: str
    expected_vertical: str = "vehicle_camera_system"
    makro_cdp_port: int = 9222
    source_cdp_port: int = 9333
    source_use_current_page: bool = False
    provider: str = "openai-compatible"
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    local_model: str = "qwen3.7-plus"
    web_model: str = "qwen3.7-max"
    api_key_env: str = "DASHSCOPE_API_KEY"


class ReadOnlyRunner(QObject):
    log = Signal(str)
    phase_changed = Signal(str)
    running_changed = Signal(bool)
    result_updated = Signal(object)
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, project_root: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.project_root = project_root.resolve()
        self.process: QProcess | None = None
        self.config: RunnerConfig | None = None
        self.run_dir: Path | None = None
        self.current_phase = "idle"
        self._stopping = False

    @property
    def is_running(self) -> bool:
        return self.process is not None and self.process.state() != QProcess.NotRunning

    def start(self, config: RunnerConfig) -> None:
        if self.is_running:
            raise RuntimeError("A read-only test is already running.")
        self._validate_config(config)
        self._assert_makro_cdp_available(config.makro_cdp_port)

        self.config = config
        self._stopping = False
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.run_dir = self.project_root / "logs" / "gui-runs" / f"readonly-{stamp}"
        self.run_dir.mkdir(parents=True, exist_ok=False)
        (self.run_dir / "_cache" / "source").mkdir(parents=True, exist_ok=True)
        (self.run_dir / "_cache" / "semantic").mkdir(parents=True, exist_ok=True)
        self._write_metadata(status="running")

        self.running_changed.emit(True)
        self._emit_log("===== GUI READ-ONLY TEST =====")
        self._emit_log(f"run_dir={self.run_dir}")
        self._emit_log(f"product_url={config.product_url}")
        self._emit_log("Safety contract: read-only scan + resolver + read-only Fill Plan only.")
        self._emit_log("makro_execute_listing.py is not invoked by this GUI.")
        self._start_scan()

    def stop(self) -> None:
        if self.process is None or self.process.state() == QProcess.NotRunning:
            return
        self._stopping = True
        self._emit_log("Stop requested. Terminating current subprocess...")
        self.process.terminate()
        if not self.process.waitForFinished(2000):
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
        if not config.expected_vertical.strip():
            raise ValueError("expected vertical 不能为空。")

    def _assert_makro_cdp_available(self, port: int) -> None:
        url = f"http://127.0.0.1:{port}/json/version"
        try:
            with urllib.request.urlopen(url, timeout=1.2) as response:
                if response.status != 200:
                    raise RuntimeError(f"HTTP {response.status}")
        except Exception as exc:
            raise RuntimeError(
                f"Makro Edge CDP 127.0.0.1:{port} 不可用。为保护长期 Makro profile，"
                "GUI 不会自动启动或重启 Makro Edge；请先按现有方式启动后再测试。"
            ) from exc

    def _start_scan(self) -> None:
        assert self.config is not None and self.run_dir is not None
        self.current_phase = "scan"
        self.phase_changed.emit("1/4  Fresh Makro live schema")
        output = self.run_dir / "01-live-schema"
        args = [
            "makro_plan_listing.py",
            "--scan-live-schema",
            "--expected-vertical",
            self.config.expected_vertical,
            "--cdp-port",
            str(self.config.makro_cdp_port),
            "--output-dir",
            str(output),
        ]
        self._start_process(args)

    def _start_cold(self) -> None:
        assert self.config is not None and self.run_dir is not None
        live_schema = latest_live_schema(self.run_dir)
        if live_schema is None:
            raise RuntimeError("fresh live-schema.json 未生成。")
        self.current_phase = "cold"
        self.phase_changed.emit("2/4  Cold Resolver")
        args = self._resolver_args(
            live_schema,
            output_dir=self.run_dir / "02-cold-resolver",
            refresh_source=True,
        )
        self._start_process(args)

    def _start_hot(self) -> None:
        assert self.run_dir is not None
        live_schema = latest_live_schema(self.run_dir)
        if live_schema is None:
            raise RuntimeError("live-schema.json 不存在。")
        self.current_phase = "hot"
        self.phase_changed.emit("3/4  Hot Resolver / cache verification")
        args = self._resolver_args(
            live_schema,
            output_dir=self.run_dir / "03-hot-resolver",
            refresh_source=False,
        )
        self._start_process(args)

    def _resolver_args(self, live_schema: Path, *, output_dir: Path, refresh_source: bool) -> list[str]:
        assert self.config is not None and self.run_dir is not None
        args = [
            "makro_resolve_ai.py",
            "--provider",
            self.config.provider,
            "--base-url",
            self.config.base_url,
            "--model",
            self.config.local_model,
            "--web-search-model",
            self.config.web_model,
            "--api-key-env",
            self.config.api_key_env,
            "--live-schema",
            str(live_schema),
            "--product-url",
            self.config.product_url,
            "--disable-thinking",
            "--web-enrich",
            "auto",
            "--source-cdp-port",
            str(self.config.source_cdp_port),
            "--source-cache-dir",
            str(self.run_dir / "_cache" / "source"),
            "--semantic-cache-dir",
            str(self.run_dir / "_cache" / "semantic"),
            "--output-dir",
            str(output_dir),
        ]
        if refresh_source:
            args.append("--refresh-source")
        if self.config.source_use_current_page:
            args.append("--source-use-current-page")
        return args

    def _start_plan(self) -> None:
        assert self.config is not None and self.run_dir is not None
        live_schema = latest_live_schema(self.run_dir)
        hot_manifest_path = latest_resolver_manifest(self.run_dir, "03-hot-resolver")
        if live_schema is None or hot_manifest_path is None:
            raise RuntimeError("Hot Resolver 产物不完整，无法 strict rebind Fill Plan。")
        manifest = json.loads(hot_manifest_path.read_text(encoding="utf-8"))
        outputs = manifest.get("outputs") or {}
        decision_packet = Path(str(outputs.get("final_decisions") or ""))
        snapshot = Path(str(outputs.get("primary_source_snapshot") or ""))
        screenshot = Path(str(outputs.get("primary_source_screenshot") or ""))
        images = [Path(str(value)) for value in outputs.get("primary_source_product_images") or []]
        product_url = str(manifest.get("primary_product_url") or self.config.product_url)
        required_paths = [decision_packet, snapshot, screenshot]
        missing = [str(path) for path in required_paths if not path.is_file()]
        if missing:
            raise RuntimeError("Hot Resolver strict-rebind 文件缺失：" + " | ".join(missing))

        self.current_phase = "plan"
        self.phase_changed.emit("4/4  Read-only Fill Plan")
        args = [
            "makro_plan_listing.py",
            "--decision-packet",
            str(decision_packet),
            "--live-schema",
            str(live_schema),
            "--product-url",
            product_url,
            "--supplier-snapshot",
            str(snapshot),
            "--image",
            str(screenshot),
            "--expected-vertical",
            self.config.expected_vertical,
            "--cdp-port",
            str(self.config.makro_cdp_port),
            "--output-dir",
            str(self.run_dir / "04-fill-plan"),
        ]
        for image in images:
            if image.is_file():
                args.extend(["--image", str(image)])
        self._start_process(args)

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
        self._emit_log("")
        self._emit_log("$ " + " ".join([sys.executable, *args]))
        process.start(sys.executable, args)

    def _read_output(self) -> None:
        if self.process is None:
            return
        raw = bytes(self.process.readAllStandardOutput())
        text = raw.decode("utf-8", errors="replace")
        for line in text.splitlines():
            self._emit_log(line)

    def _process_finished(self, exit_code: int, _exit_status: QProcess.ExitStatus) -> None:
        phase = self.current_phase
        self.process = None
        if self._stopping:
            self._finish_failure("测试已由用户停止。")
            return
        if exit_code != 0:
            self._finish_failure(f"{phase} 阶段退出码={exit_code}。请查看实时日志。")
            return

        try:
            if self.run_dir is not None:
                self.result_updated.emit(load_run_result(self.run_dir))
            if phase == "scan":
                self._start_cold()
            elif phase == "cold":
                self._start_hot()
            elif phase == "hot":
                self._start_plan()
            elif phase == "plan":
                self._finish_success()
            else:
                self._finish_failure(f"未知运行阶段：{phase}")
        except Exception as exc:
            self._finish_failure(str(exc))

    def _process_error(self, error: QProcess.ProcessError) -> None:
        if self._stopping:
            return
        if error == QProcess.FailedToStart:
            self._finish_failure("Python 子进程启动失败。")

    def _finish_success(self) -> None:
        assert self.run_dir is not None
        result = load_run_result(self.run_dir)
        self.phase_changed.emit("完成 · Read-only acceptance")
        self._emit_log("")
        self._emit_log("===== GUI READ-ONLY ACCEPTANCE COMPLETE =====")
        self._emit_log(
            f"READY={result.ready} MISSING={result.missing} CONFLICT={result.conflict} BLOCKED={result.blocked}"
        )
        self._emit_log(
            "Makro safety: "
            f"writes={result.safety.writes_performed}, "
            f"save={result.safety.save_clicked}, send_to_qc={result.safety.send_to_qc_clicked}"
        )
        self._write_metadata(status="completed")
        self.running_changed.emit(False)
        self.result_updated.emit(result)
        self.completed.emit(result)

    def _finish_failure(self, message: str) -> None:
        self._emit_log("")
        self._emit_log("ERROR: " + message)
        self.phase_changed.emit("失败")
        self._write_metadata(status="failed", error=message)
        self.running_changed.emit(False)
        self.failed.emit(message)

    def _emit_log(self, line: str) -> None:
        self.log.emit(line)
        if self.run_dir is None:
            return
        log_path = self.run_dir / "gui-run.log"
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def _write_metadata(self, *, status: str, error: str = "") -> None:
        if self.run_dir is None or self.config is None:
            return
        payload: dict[str, Any] = {
            "mode": "windows_local_development_read_only_gui",
            "status": status,
            "product_url": self.config.product_url,
            "expected_vertical": self.config.expected_vertical,
            "makro_cdp_port": self.config.makro_cdp_port,
            "source_cdp_port": self.config.source_cdp_port,
            "source_use_current_page": self.config.source_use_current_page,
            "provider": self.config.provider,
            "local_model": self.config.local_model,
            "web_model": self.config.web_model,
            "api_key_env": self.config.api_key_env,
            "core_write_runner_invoked": False,
        }
        if error:
            payload["error"] = error
        (self.run_dir / "gui-run.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
