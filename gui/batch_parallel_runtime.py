from __future__ import annotations

from types import MethodType
from typing import Any

from .batch_browser_pool import (
    bind_batch_browser_lanes,
    bind_job_browser_lane,
    browser_lane_token,
    ensure_batch_browser_lanes,
    lane_tokens,
    pop_next_lane_ready_job,
)
from .batch_model import normalize_batch_concurrency


def _set_cli_option(args: list[str], name: str, value: str) -> None:
    try:
        index = args.index(name)
    except ValueError:
        args.extend([name, value])
        return
    if index + 1 >= len(args):
        args.append(value)
    else:
        args[index + 1] = value


class BatchParallelRuntime:
    """Turn Batch's bounded concurrency into real isolated Makro concurrency.

    BatchController remains the sole process/task owner. This layer only assigns
    each job to one persistent browser lane, rewrites the child CLI to that exact
    port/profile, and makes queue pumping lane-aware. The existing per-port CDP
    lease stays exclusive, so jobs on different lanes run concurrently without
    allowing two processes to race the same Edge instance.
    """

    def __init__(self, window: Any) -> None:
        self.window = window
        self.project_root = window.project_root.resolve()
        self.controller = window.batch_workspace.controller
        self.manager = getattr(window, "_managed_makro_browser", None)
        if self.manager is None:
            raise RuntimeError("Batch parallel runtime requires ManagedMakroBrowser")

        self._lane_count = 0
        self._base_port = 0
        self._prepared_tokens: dict[int, str] = {}
        self._original_start_prepare = self.controller.start_prepare
        self._original_start_execution = self.controller.start_execution
        self._original_spawn = self.controller._spawn
        self._original_start_source = self.controller._start_source

        self._install_controller_routing()
        self._install_individual_lane_pump()
        self.controller._batch_parallel_runtime = self
        self.manager.status_changed.connect(self._decorate_batch_status)

    def _install_controller_routing(self) -> None:
        runtime = self

        def start_prepare(
            _controller: Any,
            urls: list[str],
            config: Any,
            *,
            prepare_concurrency: int = 6,
        ):
            requested = normalize_batch_concurrency(prepare_concurrency)
            active_lanes = min(requested, max(1, len(urls)))
            base_port = int(config.makro_cdp_port)
            runtime.manager.ensure_ready("Batch parallel preparation")
            lanes = ensure_batch_browser_lanes(
                runtime.project_root,
                base_port=base_port,
                count=active_lanes,
            )
            runtime._lane_count = len(lanes)
            runtime._base_port = base_port
            runtime._prepared_tokens = lane_tokens(lanes)
            batch = runtime._original_start_prepare(
                urls,
                config,
                prepare_concurrency=requested,
            )
            bind_batch_browser_lanes(
                batch,
                project_root=runtime.project_root,
                base_port=base_port,
            )
            _controller._persist_emit(immediate=True)
            runtime._decorate_batch_status("READY", "Makro Browser 已连接")
            return batch

        def start_execution(
            _controller: Any,
            *,
            allow_save: bool,
            upload_images: bool,
            execute_concurrency: int = 6,
        ) -> None:
            runtime._assert_prepared_lanes_alive()
            runtime._original_start_execution(
                allow_save=allow_save,
                upload_images=upload_images,
                execute_concurrency=execute_concurrency,
            )

        def spawn(_controller: Any, job_id: str, stage: str, args: list[str]) -> None:
            routed = list(args)
            if stage in {"prepare", "execute"}:
                job = _controller._job(job_id)
                runtime._ensure_job_lane(job)
                _set_cli_option(routed, "--cdp-port", str(job.makro_cdp_port))
                _set_cli_option(routed, "--profile-dir", str(job.makro_profile_dir))
                _controller._emit_log_now(
                    f"[{job_id}] BROWSER_LANE lane={job.browser_lane + 1} "
                    f"port={job.makro_cdp_port}"
                )
            runtime._original_spawn(job_id, stage, routed)

        def start_source(_controller: Any, job_id: str) -> None:
            runtime._ensure_job_lane(_controller._job(job_id))
            runtime._original_start_source(job_id)

        def pump_prepare(_controller: Any) -> None:
            runtime._pump_prepare()

        def pump_execute(_controller: Any) -> None:
            runtime._pump_execute()

        self.controller.start_prepare = MethodType(start_prepare, self.controller)
        self.controller.start_execution = MethodType(start_execution, self.controller)
        self.controller._spawn = MethodType(spawn, self.controller)
        self.controller._start_source = MethodType(start_source, self.controller)
        self.controller._pump_prepare = MethodType(pump_prepare, self.controller)
        self.controller._pump_execute = MethodType(pump_execute, self.controller)

    def _ensure_job_lane(self, job: Any) -> None:
        if int(getattr(job, "makro_cdp_port", 0) or 0) > 0 and str(
            getattr(job, "makro_profile_dir", "") or ""
        ):
            return
        batch = self.controller.batch
        config = self.controller.config
        if batch is None or config is None:
            raise RuntimeError("Batch browser lane cannot be assigned before Batch/config exists")

        base_port = self._base_port or int(config.makro_cdp_port)
        desired_lanes = min(
            normalize_batch_concurrency(batch.prepare_concurrency),
            max(1, len(batch.jobs)),
        )
        if desired_lanes > self._lane_count:
            lanes = ensure_batch_browser_lanes(
                self.project_root,
                base_port=base_port,
                count=desired_lanes,
            )
            self._lane_count = len(lanes)
            self._base_port = base_port
            for port, token in lane_tokens(lanes).items():
                self._prepared_tokens.setdefault(port, token)
            self._decorate_batch_status("READY", "Makro Browser 已扩展")

        try:
            ordinal = list(batch.jobs).index(job)
        except ValueError as exc:
            raise RuntimeError(f"Unknown Batch job for browser lane: {job.job_id}") from exc
        lane_count = self._lane_count or desired_lanes
        bind_job_browser_lane(
            job,
            project_root=self.project_root,
            base_port=base_port,
            lane_count=lane_count,
            ordinal=ordinal,
        )

    def _next(self, queue: list[str], *, stage: str, concurrency: int) -> str | None:
        return pop_next_lane_ready_job(
            queue,
            jobs=self.controller._jobs(),
            processes=self.controller._processes,
            stage=stage,
            concurrency=concurrency,
        )

    def _pump_prepare(self) -> None:
        controller = self.controller
        batch = controller.batch
        if batch is None or controller.config is None or controller._mode != "prepare":
            return

        source_active = any(stage == "source" for _, stage in controller._processes.values())
        if controller._source_queue and not source_active:
            controller._start_source(controller._source_queue.pop(0))

        while controller._prepare_queue:
            job_id = self._next(
                controller._prepare_queue,
                stage="prepare",
                concurrency=batch.prepare_concurrency,
            )
            if job_id is None:
                break
            controller._start_prepare_job(job_id)

        if not controller._source_queue and not controller._prepare_queue and not controller._processes:
            batch.status = "PREPARED"
            controller._mode = "idle"
            controller._persist_emit(immediate=True)
            controller.running_changed.emit(False)
            controller.state_changed.emit("批量准备完成")

    def _pump_execute(self) -> None:
        controller = self.controller
        batch = controller.batch
        if batch is None or controller._mode != "execute":
            return

        while controller._execute_queue:
            job_id = self._next(
                controller._execute_queue,
                stage="execute",
                concurrency=batch.execute_concurrency,
            )
            if job_id is None:
                break
            controller._start_execute_job(job_id)

        if not controller._execute_queue and not controller._processes:
            batch.status = "COMPLETE"
            controller._mode = "idle"
            controller._persist_emit(immediate=True)
            controller.running_changed.emit(False)
            controller.state_changed.emit("Batch 执行完成")

    def _pump_individual(self) -> None:
        controller = self.controller
        batch = controller.batch
        if batch is None or controller.config is None:
            return

        source_active = any(stage == "source" for _, stage in controller._processes.values())
        if controller._source_queue and not source_active:
            controller._start_source(controller._source_queue.pop(0))

        while controller._prepare_queue:
            job_id = self._next(
                controller._prepare_queue,
                stage="prepare",
                concurrency=batch.prepare_concurrency,
            )
            if job_id is None:
                break
            controller._start_prepare_job(job_id)

        while controller._execute_queue:
            job_id = self._next(
                controller._execute_queue,
                stage="execute",
                concurrency=batch.execute_concurrency,
            )
            if job_id is None:
                break
            controller._start_execute_job(job_id)

    def _install_individual_lane_pump(self) -> None:
        from .batch_individual_controls import BatchIndividualControls

        marker = "_batch_parallel_original_pump_lanes"
        if hasattr(BatchIndividualControls, marker):
            return
        original = BatchIndividualControls._pump_lanes
        setattr(BatchIndividualControls, marker, original)

        def pump_lanes(instance: Any) -> None:
            runtime = getattr(instance.controller, "_batch_parallel_runtime", None)
            if isinstance(runtime, BatchParallelRuntime):
                runtime._pump_individual()
                return
            original(instance)

        BatchIndividualControls._pump_lanes = pump_lanes

    def _expected_job_ports(self) -> set[int]:
        batch = self.controller.batch
        if batch is None:
            return set()
        return {
            int(job.makro_cdp_port)
            for job in batch.jobs
            if str(job.makro_target_id or "") and int(job.makro_cdp_port or 0) > 0
        }

    def _assert_prepared_lanes_alive(self) -> None:
        batch = self.controller.batch
        if batch is None:
            return
        legacy = [
            str(job.job_id)
            for job in batch.jobs
            if str(job.makro_target_id or "")
            and (
                int(job.makro_cdp_port or 0) <= 0
                or not str(job.makro_profile_dir or "").strip()
            )
        ]
        if legacy:
            raise RuntimeError(
                "这个 Batch 来自并行 browser-lane 所有权引入之前，旧 targetId 无法安全迁移；"
                f"请重新批量准备。legacy_jobs={legacy}"
            )

        ports = self._expected_job_ports()
        if not ports:
            return
        missing = [port for port in sorted(ports) if not browser_lane_token(port)]
        if missing:
            raise RuntimeError(
                "Batch 准备后的 Makro browser lane 已关闭，owned targetId 已失效；"
                f"请重新批量准备。missing_ports={missing}"
            )
        if self._prepared_tokens:
            changed = [
                port
                for port in sorted(ports)
                if self._prepared_tokens.get(port)
                and browser_lane_token(port) != self._prepared_tokens.get(port)
            ]
            if changed:
                raise RuntimeError(
                    "Batch 准备后的 Makro browser lane 被重启过，owned targetId 已失效；"
                    f"请重新批量准备。restarted_ports={changed}"
                )

    def _decorate_batch_status(self, state: str, detail: str) -> None:
        label = getattr(self.manager, "_batch_label", None)
        if label is None or self._lane_count <= 1:
            return
        label.setText(
            f"Makro Browser · {state} · {detail} · {self._lane_count} 条独立并行 lanes"
        )
        label.setToolTip(
            "Batch 为每个并发 lane 使用独立 Edge Profile + CDP 端口；"
            "登录态从主 Makro Browser 安全同步。不同 lane 真正并行，同一 lane 仍保持独占。"
        )


def install_batch_parallel_runtime(window: Any) -> BatchParallelRuntime:
    existing = getattr(window, "_batch_parallel_runtime", None)
    if isinstance(existing, BatchParallelRuntime):
        return existing
    runtime = BatchParallelRuntime(window)
    window._batch_parallel_runtime = runtime
    return runtime


__all__ = ["BatchParallelRuntime", "install_batch_parallel_runtime"]
