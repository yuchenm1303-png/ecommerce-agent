from __future__ import annotations

import json
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit, urlunsplit


class ProbeDiagnostics:
    """Log transport metadata only; never prompts, images, keys or response bodies."""

    def __init__(self, binding, timeout: float, directory: Path | None = None):
        if directory is None:
            from .ai_service_settings import settings_directory
            directory = settings_directory() / "ai-probe-logs"
        self.secret = binding.api_key
        parts = urlsplit(binding.base_url)
        host = parts.hostname or ""
        if parts.port:
            host += f":{parts.port}"
        self.base_url = urlunsplit((parts.scheme, host, parts.path, "", ""))
        self.role = binding.role
        self.model = binding.model
        self.timeout = timeout
        self.stage = "client_init"
        self.started = time.monotonic()
        self.path = Path(directory) / f"probe-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:12]}.jsonl"
        self.write("probe_started")

    def clean(self, value) -> str:
        text = str(value).replace(self.secret, "***") if self.secret else str(value)
        text = re.sub(r"(?i)Bearer\s+\S+", "Bearer ***", text)
        text = re.sub(r"(?i)sk-[A-Za-z0-9_-]+", "***", text)
        text = re.sub(r"https?://[^\s\"'<>]+", "[URL omitted]", text)
        return text[:1200]

    def set_stage(self, stage):
        self.stage = stage
        callback = getattr(self, "progress_callback", None)
        if callback is not None:
            callback(stage)

    def write(self, event: str, **fields):
        if self.path is None:
            return
        record = dict(timestamp=datetime.now(timezone.utc).isoformat(), event=event,
                      role=self.role, model=self.clean(self.model), base_url=self.base_url,
                      stage=self.stage, timeout_seconds=self.timeout, sdk_max_retries=0, **fields)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            # A logging failure must not replace the actual capability result.
            self.path = None

    def call(self, endpoint, method, **kwargs):
        started = time.monotonic()
        self.write("request_started", endpoint=endpoint,
                   response_format=(kwargs.get("response_format") or {}).get("type"),
                   tool_types=[item.get("type") for item in kwargs.get("tools", [])],
                   tool_choice=kwargs.get("tool_choice"),
                   extension_fields=sorted((kwargs.get("extra_body") or {}).keys()))
        try:
            result = method(**kwargs)
        except Exception as exc:
            chain = []
            cause = exc.__cause__ or exc.__context__
            for _ in range(4):
                if cause is None:
                    break
                chain.append(dict(type=type(cause).__name__, message=self.clean(cause)))
                cause = cause.__cause__ or cause.__context__
            response = getattr(exc, "response", None)
            headers = getattr(response, "headers", {}) or {}
            self.write("request_failed", endpoint=endpoint,
                       elapsed_seconds=round(time.monotonic() - started, 3),
                       error_type=type(exc).__name__, error=self.clean(exc), causes=chain,
                       status_code=getattr(exc, "status_code", None),
                       request_id=self.clean(getattr(exc, "request_id", None) or headers.get("x-request-id", "")))
            raise
        choices = getattr(result, "choices", ()) or ()
        message = getattr(choices[0], "message", None) if choices else None
        content = getattr(message, "content", None)
        self.write("request_succeeded", endpoint=endpoint,
                   choice_count=len(choices),
                   finish_reason=getattr(choices[0], "finish_reason", None) if choices else None,
                   content_type=type(content).__name__,
                   content_length=len(content) if isinstance(content, (str, list)) else 0,
                   refusal_present=bool(getattr(message, "refusal", None)),
                   elapsed_seconds=round(time.monotonic() - started, 3),
                   request_id=self.clean(getattr(result, "_request_id", "") or ""))
        return result

    def wrap(self, client):
        def validation_retry():
            self.write("validation_retry", reason="invalid_json", attempt=2)
            callback = getattr(self, "progress_callback", None)
            if callback is not None:
                callback(self.stage + "_retry")
        def endpoint(path, method):
            return lambda **kwargs: self.call(path, method, **kwargs)
        # Resolve SDK resources lazily so test doubles and partial clients work.
        class Resource:
            def __init__(self, owner, path):
                self.owner, self.path = owner, path
            def create(self, **kwargs):
                resource = client
                for name in self.path.split("."):
                    resource = getattr(resource, name)
                return endpoint("/" + self.path.replace(".", "/"), resource.create)(**kwargs)
        return SimpleNamespace(
            chat=SimpleNamespace(completions=Resource(self, "chat.completions")),
            responses=Resource(self, "responses"),
            validation_retry=validation_retry,
        )

    def finish(self, report):
        from dataclasses import replace
        self.write("probe_finished", passed=report.passed,
                   elapsed_seconds=round(time.monotonic() - self.started, 3),
                   checks=[dict(name=item.name, passed=item.passed) for item in report.checks],
                   error=self.clean(report.error))
        return replace(report, failed_stage="" if report.passed else self.stage,
                       log_path=str(self.path) if self.path else "")
