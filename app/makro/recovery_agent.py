"""Recovery AI advisor.

Phase 1 is deliberately advisory only: this module converts a bounded page
observation into a multimodal JSON task and validates the returned proposal.
It does not execute browser actions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from .page_observation import PageObservation
from .runtime_contract import (
    FORBIDDEN_RECOVERY_ACTION_NAMES,
    InterruptionKind,
    RecoveryAction,
    RecoveryProposal,
)


class RecoveryJSONProvider(Protocol):
    name: str

    def extract_json(self, request_payload: dict[str, Any]) -> dict[str, Any]:
        ...


def build_recovery_request(
    observation: PageObservation,
    *,
    last_successful_action: str = "",
    job_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    allowed_actions = [action.value for action in RecoveryAction]
    context = {
        "observation": observation.as_ai_context(),
        "last_successful_action": str(last_successful_action or ""),
        "job_context": dict(job_context or {}),
        "allowed_actions": allowed_actions,
        "forbidden_business_actions": sorted(FORBIDDEN_RECOVERY_ACTION_NAMES),
    }
    sources: list[dict[str, Any]] = [
        {
            "source_id": "runtime-page-state",
            "source_type": "makro_runtime_observation",
            "kind": "text",
            "origin": observation.url,
            "content": json.dumps(context, ensure_ascii=False, separators=(",", ":")),
        }
    ]
    if observation.screenshot_path and Path(observation.screenshot_path).is_file():
        sources.append(
            {
                "source_id": "runtime-screenshot",
                "source_type": "makro_runtime_screenshot",
                "kind": "image",
                "image_path": observation.screenshot_path,
            }
        )

    return {
        "task": "advise_makro_runtime_recovery",
        "system_instruction": (
            "You are a recovery supervisor for a deterministic marketplace listing workflow. "
            "Understand the current browser page and recommend one bounded recovery action. "
            "You are not the browser executor and cannot change product business data. JSON only."
        ),
        "prompt_instruction": (
            "Use the runtime observation and screenshot to identify what interrupted the expected "
            "workflow. Prefer recognizing that the portal legitimately advanced to another known "
            "stage over forcing it back. Recommend exactly one allowed action. Do not propose "
            "Send to QC, deleting a listing, changing Vertical/Brand, creating a new listing, "
            "submitting unknown forms, or bypassing human verification."
        ),
        "context": context,
        "grounded_sources": sources,
        "rules": [
            "action must be copied exactly from allowed_actions.",
            "target_element_id must be empty unless it refers to one exact interactive element_id from observation.",
            "CAPTCHA/human verification must use ASK_HUMAN_VERIFICATION.",
            "Login/authentication must use ASK_HUMAN_LOGIN.",
            "Never claim recovery succeeded; deterministic code verifies the resulting state.",
            "If confidence is low or the page purpose is unclear, return NONE or ABORT rather than guessing.",
        ],
        "json_contract": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "observed_state": {"type": "string"},
                "interruption": {
                    "type": "string",
                    "enum": [item.value for item in InterruptionKind],
                },
                "action": {
                    "type": "string",
                    "enum": allowed_actions,
                },
                "target_element_id": {"type": "string"},
                "explanation": {"type": "string"},
                "expected_after": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "requires_user": {"type": "boolean"},
            },
            "required": [
                "observed_state",
                "interruption",
                "action",
                "target_element_id",
                "explanation",
                "expected_after",
                "confidence",
                "requires_user",
            ],
        },
        "strict_json_schema": True,
    }


def parse_recovery_response(raw: Any, observation: PageObservation) -> RecoveryProposal:
    if not isinstance(raw, dict):
        raise ValueError("Recovery AI response must be a JSON object")

    action_text = str(raw.get("action") or "").strip().upper()
    if action_text in FORBIDDEN_RECOVERY_ACTION_NAMES:
        raise ValueError(f"Recovery AI proposed forbidden business action: {action_text}")
    try:
        action = RecoveryAction(action_text)
    except ValueError as exc:
        raise ValueError(f"Recovery AI returned unknown action: {action_text!r}") from exc

    interruption_text = str(raw.get("interruption") or "UNKNOWN").strip().upper()
    try:
        interruption = InterruptionKind(interruption_text)
    except ValueError as exc:
        raise ValueError(f"Recovery AI returned unknown interruption: {interruption_text!r}") from exc

    target = str(raw.get("target_element_id") or "").strip()
    if target and target not in observation.element_ids():
        raise ValueError(
            f"Recovery AI referenced an element outside the current observation: {target!r}"
        )

    if observation.human_verification and action is not RecoveryAction.ASK_HUMAN_VERIFICATION:
        raise ValueError("Human verification can only be routed to ASK_HUMAN_VERIFICATION")
    if observation.login_required and action not in {
        RecoveryAction.ASK_HUMAN_LOGIN,
        RecoveryAction.ASK_HUMAN_VERIFICATION,
    }:
        raise ValueError("Login-required observation cannot be auto-recovered by AI")

    return RecoveryProposal(
        observed_state=str(raw.get("observed_state") or "").strip(),
        interruption=interruption,
        action=action,
        target_element_id=target,
        explanation=str(raw.get("explanation") or "").strip(),
        expected_after=str(raw.get("expected_after") or "").strip(),
        confidence=float(raw.get("confidence") or 0.0),
        requires_user=bool(raw.get("requires_user", False)),
    )


class RecoveryAgent:
    """Advisory-only Recovery AI facade used by the future runtime supervisor."""

    def __init__(self, provider: RecoveryJSONProvider) -> None:
        self.provider = provider

    def analyze(
        self,
        observation: PageObservation,
        *,
        last_successful_action: str = "",
        job_context: dict[str, Any] | None = None,
    ) -> RecoveryProposal:
        request = build_recovery_request(
            observation,
            last_successful_action=last_successful_action,
            job_context=job_context,
        )
        raw = self.provider.extract_json(request)
        return parse_recovery_response(raw, observation)


__all__ = [
    "RecoveryAgent",
    "RecoveryJSONProvider",
    "build_recovery_request",
    "parse_recovery_response",
]
