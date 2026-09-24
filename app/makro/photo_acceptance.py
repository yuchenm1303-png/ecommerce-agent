from __future__ import annotations

from typing import Any, Callable


def _positive_int(value: object, *, default: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return int(default)
    return parsed if parsed >= 0 else int(default)


def photo_requirement_satisfied(photo_report: dict[str, Any] | None) -> bool:
    """Return the canonical Makro Product Photos acceptance state.

    Observed final gallery count wins whenever it is present. That prevents a stale
    or contradictory boolean from turning a real 0/N gallery into success. Older
    reports may not contain final_count, so compatibility falls back to the lower
    uploader flag and then persisted/initial counts. "No upload was requested" is
    never treated as success by itself.
    """

    if not photo_report:
        return False

    persistence = photo_report.get("persistence") or {}
    if not isinstance(persistence, dict):
        persistence = {}
    required_min = 1
    for raw_required in (photo_report.get("required_min"), persistence.get("required_min")):
        parsed_required = _positive_int(raw_required)
        if parsed_required >= 1:
            required_min = parsed_required
            break

    for owner in (photo_report, persistence):
        if "final_count" in owner:
            return _positive_int(owner.get("final_count")) >= required_min

    explicit = photo_report.get("listing_photo_requirement_satisfied")
    if isinstance(explicit, bool):
        return explicit

    counts = (
        photo_report.get("persisted"),
        persistence.get("persisted"),
        photo_report.get("initial_count"),
        persistence.get("initial_count"),
    )
    return any(_positive_int(raw) >= required_min for raw in counts)


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
