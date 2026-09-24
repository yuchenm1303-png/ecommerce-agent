from __future__ import annotations

from typing import Any, Callable


def photo_requirement_satisfied(photo_report: dict[str, Any] | None) -> bool:
    """Return the canonical Makro Product Photos acceptance state.

    The upload transaction itself already records the authoritative
    ``listing_photo_requirement_satisfied`` flag. Older reports may not contain
    that field, so the compatibility fallback uses observed persisted gallery
    counts. Crucially, "no upload was requested" is never treated as success.
    """

    if not photo_report:
        return False

    explicit = photo_report.get("listing_photo_requirement_satisfied")
    if isinstance(explicit, bool):
        return explicit

    persistence = photo_report.get("persistence") or {}
    counts = (
        photo_report.get("final_count"),
        photo_report.get("persisted"),
        persistence.get("final_count") if isinstance(persistence, dict) else None,
        photo_report.get("initial_count"),
    )
    for raw in counts:
        try:
            if int(raw or 0) >= 1:
                return True
        except (TypeError, ValueError):
            continue
    return False


def harden_completion_result(
    result: dict[str, Any],
    photo_report: dict[str, Any] | None,
) -> dict[str, Any]:
    """Make mandatory photo persistence part of full-draft acceptance."""

    hardened = dict(result)
    photo_ok = photo_requirement_satisfied(photo_report)
    hardened["photo_requirement_satisfied"] = photo_ok
    if not photo_ok:
        hardened["draft_persisted_complete"] = False
        hardened["autofill_safe_complete"] = False
        hardened["photo_acceptance_reason"] = "mandatory_product_photo_missing"
    else:
        hardened["photo_acceptance_reason"] = "persisted_product_photo_present"
    return hardened


def harden_execution_outcome(
    result: dict[str, Any],
    photo_report: dict[str, Any] | None,
) -> dict[str, Any]:
    """Prevent a zero-photo full Step 3 run from being classified as success."""

    hardened = dict(result)
    photo_ok = photo_requirement_satisfied(photo_report)
    hardened["photo_requirement_satisfied"] = photo_ok
    if photo_ok:
        return hardened

    hardened["strict_acceptance_complete"] = False
    if hardened.get("status") == "success":
        hardened["status"] = "partial_success"
        hardened["reason"] = "mandatory_product_photo_missing"
        hardened["process_success"] = True
    elif hardened.get("reason") == "isolated_incomplete_items":
        hardened["reason"] = "mandatory_product_photo_missing"
    return hardened


def install_executor_photo_guards(executor_module: Any) -> None:
    """Install the photo gate on the canonical GUI executor host exactly once.

    ``makro_execute_listing`` still imports a few compatibility helpers from the
    historical preview module. Keeping this guard at the canonical GUI host lets
    us enforce the submission boundary without duplicating the Step 3 browser
    implementation. The wrapper is idempotent and intentionally tiny.
    """

    if bool(getattr(executor_module, "_photo_acceptance_guards_installed", False)):
        return

    original_completion: Callable[..., dict[str, Any]] = executor_module._completion_summary
    original_outcome: Callable[..., dict[str, Any]] = executor_module._execution_outcome

    def guarded_completion(section_reports, photo_report, plan_summary):
        return harden_completion_result(
            original_completion(section_reports, photo_report, plan_summary),
            photo_report,
        )

    def guarded_outcome(section_reports, photo_report, completion):
        return harden_execution_outcome(
            original_outcome(section_reports, photo_report, completion),
            photo_report,
        )

    executor_module._completion_summary = guarded_completion
    executor_module._execution_outcome = guarded_outcome
    executor_module._photo_acceptance_guards_installed = True


__all__ = [
    "harden_completion_result",
    "harden_execution_outcome",
    "install_executor_photo_guards",
    "photo_requirement_satisfied",
]
