from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


BATCH_WORKER_MIN = 1
BATCH_WORKER_DEFAULT = 6
BATCH_WORKER_MAX = 16


def normalize_batch_concurrency(value: int) -> int:
    return max(BATCH_WORKER_MIN, min(BATCH_WORKER_MAX, int(value)))


BATCH_JOB_STATES = (
    "QUEUED",
    "CAPTURING",
    "UNDERSTANDING",
    "SELECTING_VERTICAL",
    "SELECTING_BRAND",
    "RESOLVING",
    "READY",
    "FILLING",
    "UPLOADING_IMAGES",
    "SAVING",
    "VERIFYING",
    "DONE",
    "REVIEW",
    "FAILED",
    "STOPPED",
)


@dataclass(slots=True)
class BatchJob:
    job_id: str
    product_url: str
    status: str = "QUEUED"
    stage_detail: str = "waiting"
    progress: int = 0
    vertical: str = ""
    brand: str = ""
    ready: int = 0
    blocked: int = 0
    required_blocked: int = 0
    product_name: str = ""
    makro_target_id: str = ""
    run_dir: str = ""
    execution_report: str = ""
    image_count: int = 0
    error: str = ""
    failure_stage: str = ""
    exit_code: int | None = None
    operation_phase: str = ""
    created_at: str = field(default_factory=lambda: _now())
    updated_at: str = field(default_factory=lambda: _now())

    def touch(self) -> None:
        self.updated_at = _now()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "BatchJob":
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{key: payload[key] for key in allowed if key in payload})


@dataclass(slots=True)
class BatchRun:
    batch_id: str
    root_dir: str
    jobs: list[BatchJob]
    status: str = "IDLE"
    prepare_concurrency: int = BATCH_WORKER_DEFAULT
    execute_concurrency: int = BATCH_WORKER_DEFAULT
    save_authorized: bool = False
    images_authorized: bool = False
    send_to_qc: bool = False
    created_at: str = field(default_factory=lambda: _now())
    updated_at: str = field(default_factory=lambda: _now())

    def __post_init__(self) -> None:
        self.prepare_concurrency = normalize_batch_concurrency(self.prepare_concurrency)
        self.execute_concurrency = normalize_batch_concurrency(self.execute_concurrency)

    def touch(self) -> None:
        self.updated_at = _now()

    def as_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "jobs": [job.as_dict() for job in self.jobs],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "BatchRun":
        jobs = [BatchJob.from_dict(item) for item in payload.get("jobs") or []]
        values = dict(payload)
        values["jobs"] = jobs
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{key: values[key] for key in allowed if key in values})

    def summary(self) -> dict[str, int]:
        processing = {
            "CAPTURING",
            "UNDERSTANDING",
            "SELECTING_VERTICAL",
            "SELECTING_BRAND",
            "RESOLVING",
            "FILLING",
            "UPLOADING_IMAGES",
            "SAVING",
            "VERIFYING",
        }
        return {
            "total": len(self.jobs),
            "processing": sum(job.status in processing for job in self.jobs),
            "ready": sum(job.status == "READY" for job in self.jobs),
            "done": sum(job.status == "DONE" for job in self.jobs),
            "review": sum(job.status == "REVIEW" for job in self.jobs),
            "failed": sum(job.status == "FAILED" for job in self.jobs),
        }


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def normalize_batch_urls(text: str) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for raw in str(text or "").replace("\r", "\n").split("\n"):
        value = raw.strip()
        if not value:
            continue
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"不是完整 http(s) 商品链接：{value}")
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        output.append(value)
    if not output:
        raise ValueError("请至少输入一个供应商商品链接。")
    return output


def create_batch_run(
    project_root: Path,
    urls: list[str],
    *,
    prepare_concurrency: int = BATCH_WORKER_DEFAULT,
    execute_concurrency: int = BATCH_WORKER_DEFAULT,
) -> BatchRun:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    batch_id = f"batch-{stamp}"
    root = project_root.resolve() / "logs" / "batch-runs" / batch_id
    root.mkdir(parents=True, exist_ok=False)
    jobs: list[BatchJob] = []
    for index, url in enumerate(urls, start=1):
        job_id = f"JOB-{index:03d}"
        job_root = root / "jobs" / job_id
        job_root.mkdir(parents=True, exist_ok=True)
        jobs.append(
            BatchJob(
                job_id=job_id,
                product_url=url,
                run_dir=str((job_root / "workflow").resolve()),
            )
        )
    batch = BatchRun(
        batch_id=batch_id,
        root_dir=str(root.resolve()),
        jobs=jobs,
        status="QUEUED",
        prepare_concurrency=normalize_batch_concurrency(prepare_concurrency),
        execute_concurrency=normalize_batch_concurrency(execute_concurrency),
    )
    save_batch_run(batch)
    return batch


def save_batch_run(batch: BatchRun) -> Path:
    batch.touch()
    root = Path(batch.root_dir)
    root.mkdir(parents=True, exist_ok=True)
    target = root / "batch.json"
    temp = root / ".batch.json.tmp"
    temp.write_text(json.dumps(batch.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, target)
    return target


def load_batch_run(path: str | Path) -> BatchRun:
    source = Path(path)
    if source.is_dir():
        source = source / "batch.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("batch.json root must be an object")
    return BatchRun.from_dict(payload)


__all__ = [
    "BATCH_JOB_STATES",
    "BATCH_WORKER_DEFAULT",
    "BATCH_WORKER_MAX",
    "BATCH_WORKER_MIN",
    "BatchJob",
    "BatchRun",
    "create_batch_run",
    "load_batch_run",
    "normalize_batch_concurrency",
    "normalize_batch_urls",
    "save_batch_run",
]
