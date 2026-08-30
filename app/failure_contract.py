from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

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

    Provider adapters intentionally wrap SDK exceptions.  The complete exception
    chain is therefore inspected so an HTTP-400 media moderation/decoding rejection
    can be isolated to the media item while account/auth/billing failures remain a
    hard product-stage boundary.  Transient transport errors are retryable and are
    not mistaken for bad media merely because the request happened to contain an
    image.
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


__all__ = [
    "FailureIssue",
    "FailureScope",
    "FailureSeverity",
    "classify_provider_failure",
    "is_provider_media_rejection",
]
