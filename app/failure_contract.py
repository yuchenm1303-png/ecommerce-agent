from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import os
from pathlib import Path
import sys
import traceback
from typing import Any, Callable, Sequence

from .providers.transient_retry import exception_text, is_retryable_ai_error


class FailureSeverity(str, Enum):
    """Business consequence of one failure observation."""

    DEGRADED = "degraded"
    FATAL = "fatal"


class FailureScope(str, Enum):
    """Smallest unit whose outcome is invalidated by the failure."""

    ARTIFACT = "artifact"
    IMAGE = "image"
    QUERY = "query"
    FIELD = "field"
    SECTION = "section"
    STAGE = "stage"
    PRODUCT = "product"
    BATCH = "batch"


@dataclass(slots=True, frozen=True)
class FailureIssue:
    scope: FailureScope
    severity: FailureSeverity
    reason_code: str
    detail: str
    retryable: bool = False
    item: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def fatal(self) -> bool:
        return self.severity is FailureSeverity.FATAL

    def as_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope.value,
            "severity": self.severity.value,
            "reason_code": self.reason_code,
            "detail": self.detail,
            "retryable": self.retryable,
            "item": self.item,
            "metadata": dict(self.metadata),
        }


_PROVIDER_ACCOUNT_FATAL_MARKERS = (
    "arrearage",
    "insufficient balance",
    "insufficient_balance",
    "invalid api key",
    "invalid_api_key",
    "authentication",
    "unauthorized",
    "forbidden",
    "permission denied",
    "billing",
)

_PROVIDER_MEDIA_REJECTION_MARKERS = (
    "datainspectionfailed",
    "data inspection failed",
    "input image data",
    "image data may contain inappropriate",
    "inappropriate image",
    "invalid image data",
    "invalid image",
    "unsupported image",
    "unsupported image format",
    "image format is not supported",
    "image_url is invalid",
    "image url is invalid",
    "failed to process image",
    "failed to parse image",
    "image length and width do not meet the model restrictions",
    "image dimensions do not meet the model restrictions",
    "image size does not meet the model restrictions",
    "must be larger than 10",
    "content filter",
    "content_filter",
)


def classify_provider_failure(
    exc: BaseException,
    *,
    media_present: bool = False,
    item: str = "",
) -> FailureIssue:
    """Classify provider failures by blast radius, not exception ancestry.

    Provider adapters intentionally wrap SDK exceptions. The complete exception
    chain is therefore inspected so an HTTP-400 media moderation, decoding or
    provider-dimension rejection can be isolated to the media item while
    account/auth/billing failures remain a hard product-stage boundary. Transient
    transport errors are retryable and are not mistaken for bad media merely because
    the request happened to contain an image.
    """

    flattened = exception_text(exc)
    detail = f"{type(exc).__name__}: {exc}"

    if any(marker in flattened for marker in _PROVIDER_ACCOUNT_FATAL_MARKERS):
        return FailureIssue(
            scope=FailureScope.STAGE,
            severity=FailureSeverity.FATAL,
            reason_code="provider_account_unavailable",
            detail=detail,
            retryable=False,
            item=item,
        )

    if media_present and any(marker in flattened for marker in _PROVIDER_MEDIA_REJECTION_MARKERS):
        return FailureIssue(
            scope=FailureScope.IMAGE,
            severity=FailureSeverity.DEGRADED,
            reason_code="provider_media_rejected",
            detail=detail,
            retryable=False,
            item=item,
        )

    if is_retryable_ai_error(exc):
        return FailureIssue(
            scope=FailureScope.STAGE,
            severity=FailureSeverity.DEGRADED,
            reason_code="provider_transient_failure",
            detail=detail,
            retryable=True,
            item=item,
        )

    return FailureIssue(
        scope=FailureScope.STAGE,
        severity=FailureSeverity.FATAL,
        reason_code="provider_request_failed",
        detail=detail,
        retryable=False,
        item=item,
    )


def is_provider_media_rejection(exc: BaseException) -> bool:
    return classify_provider_failure(exc, media_present=True).reason_code == "provider_media_rejected"


def _cli_argument_value(argv: Sequence[str], name: str) -> str:
    prefix = name + "="
    for index, raw in enumerate(argv):
        value = str(raw)
        if value == name and index + 1 < len(argv):
            return str(argv[index + 1]).strip()
        if value.startswith(prefix):
            return value[len(prefix) :].strip()
    return ""


def _single_workflow_root(output_dir: str | Path) -> Path | None:
    """Resolve the owning single-GUI workflow from one nested child output dir."""

    path = Path(output_dir).expanduser().resolve()
    for candidate in (path, *path.parents):
        if candidate.name.startswith("workflow-"):
            return candidate
    return None


def _single_workflow_prepare_log(argv: Sequence[str]) -> Path | None:
    output_dir = _cli_argument_value(argv, "--output-dir")
    if not output_dir:
        return None
    workflow_root = _single_workflow_root(output_dir)
    if workflow_root is None:
        return None
    return workflow_root / "diagnostics" / "prepare.log"


def _append_single_workflow_child_failure(
    *,
    argv: Sequence[str],
    stage: str,
    error_type: str,
    message: str,
    traceback_text: str = "",
) -> None:
    """Synchronously persist the deepest single-workflow child failure.

    The GUI process still owns its normal ``gui-workflow.log``. This second,
    synchronous source exists specifically so a child traceback can never be lost
    merely because the outer QProcess/journal disappears before telemetry is built.
    Every child failure appends to the canonical ``diagnostics/prepare.log`` that
    ``task_failure_diagnostics`` already treats as a full stage-log truth source.
    """

    target = _single_workflow_prepare_log(argv)
    if target is None:
        return

    timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    header = (
        "===== CHILD FAILURE "
        f"stage={stage} ts={timestamp} pid={os.getpid()} ====="
    )
    body = str(traceback_text or "").rstrip()
    if not body:
        body = f"{error_type}: {message}"
    elif f"{error_type}: {message}" not in body:
        body += f"\n{error_type}: {message}"
    payload = f"{header}\n{body}\n===== END CHILD FAILURE =====\n"

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception as journal_exc:
        # Never replace or suppress the business exception. The outer process log
        # still receives this explicit telemetry-journal failure marker.
        print(
            "TELEMETRY_FAILURE_JOURNAL_ERROR "
            f"stage={stage} error={type(journal_exc).__name__}: {journal_exc}",
            file=sys.stderr,
            flush=True,
        )


def run_cli_with_failure_journal(
    main: Callable[[], int],
    *,
    stage: str,
    argv: Sequence[str] | None = None,
) -> int:
    """Run a Step-3 child CLI while durably preserving every fatal exit.

    This wrapper does not classify, retry, reinterpret, or recover the task. It
    only makes the original child failure durable before process exit. Ordinary
    successful output remains untouched.
    """

    effective_argv = tuple(sys.argv[1:] if argv is None else argv)
    try:
        result = main()
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        if int(code or 0) != 0:
            _append_single_workflow_child_failure(
                argv=effective_argv,
                stage=stage,
                error_type=type(exc).__name__,
                message=str(exc),
                traceback_text=traceback.format_exc(),
            )
        raise
    except BaseException as exc:
        _append_single_workflow_child_failure(
            argv=effective_argv,
            stage=stage,
            error_type=type(exc).__name__,
            message=str(exc),
            traceback_text=traceback.format_exc(),
        )
        raise

    status = int(result or 0)
    if status != 0:
        _append_single_workflow_child_failure(
            argv=effective_argv,
            stage=stage,
            error_type="ChildProcessExitError",
            message=f"{stage} returned exit code {status}",
        )
    return status


__all__ = [
    "FailureIssue",
    "FailureScope",
    "FailureSeverity",
    "classify_provider_failure",
    "is_provider_media_rejection",
    "run_cli_with_failure_journal",
]
