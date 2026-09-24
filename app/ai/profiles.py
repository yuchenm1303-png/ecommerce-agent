from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from .capabilities import ModelCapability
from .credentials import CredentialRef


_PROFILE_ID_RE = re.compile(r"^[a-z][a-z0-9._-]{0,127}$")


@dataclass(frozen=True, slots=True)
class ModelProfile:
    """One provider/model assignment for a stable application AI role.

    A profile contains only non-secret model metadata plus an optional
    ``CredentialRef``. The reference names where a secret can be resolved; the
    secret value itself never belongs in this object.
    """

    profile_id: str
    provider: str
    model: str
    capabilities: frozenset[ModelCapability]
    allow_fallback: bool = False
    credential_ref: CredentialRef | None = None

    def __post_init__(self) -> None:
        profile_id = str(self.profile_id or "").strip().casefold()
        provider = str(self.provider or "").strip().casefold()
        model = str(self.model or "").strip()
        if not _PROFILE_ID_RE.fullmatch(profile_id):
            raise ValueError(f"invalid model profile id: {self.profile_id!r}")
        if not provider:
            raise ValueError("model profile provider must not be empty")
        if not model:
            raise ValueError("model profile model must not be empty")
        capabilities = frozenset(ModelCapability(value) for value in self.capabilities)
        if not capabilities:
            raise ValueError("model profile must declare at least one capability")
        credential_ref = self.credential_ref
        if credential_ref is not None and not isinstance(credential_ref, CredentialRef):
            raise TypeError("credential_ref must be CredentialRef or None")
        object.__setattr__(self, "profile_id", profile_id)
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "capabilities", capabilities)

    def supports(self, *required: ModelCapability) -> bool:
        return all(ModelCapability(value) in self.capabilities for value in required)

    def as_safe_dict(self) -> dict[str, object]:
        return {
            "profile_id": self.profile_id,
            "provider": self.provider,
            "model": self.model,
            "capabilities": sorted(value.value for value in self.capabilities),
            "allow_fallback": self.allow_fallback,
            "credential_ref": (
                self.credential_ref.as_safe_dict() if self.credential_ref is not None else None
            ),
        }


class ModelRegistry:
    """Explicit profile registry; duplicate role ownership fails closed."""

    def __init__(self) -> None:
        self._profiles: dict[str, ModelProfile] = {}

    def register(self, profile: ModelProfile) -> None:
        profile_id = profile.profile_id
        if profile_id in self._profiles:
            raise ValueError(f"model profile already registered: {profile_id}")
        self._profiles[profile_id] = profile

    def get(self, profile_id: str) -> ModelProfile:
        key = str(profile_id or "").strip().casefold()
        try:
            return self._profiles[key]
        except KeyError as exc:
            raise KeyError(f"unknown model profile: {key or profile_id!r}") from exc

    def require(
        self,
        profile_id: str,
        capabilities: Iterable[ModelCapability] = (),
    ) -> ModelProfile:
        profile = self.get(profile_id)
        required = tuple(ModelCapability(value) for value in capabilities)
        missing = tuple(value.value for value in required if value not in profile.capabilities)
        if missing:
            raise ValueError(
                f"model profile {profile.profile_id!r} lacks required capabilities: "
                + ", ".join(missing)
            )
        return profile

    def all(self) -> tuple[ModelProfile, ...]:
        return tuple(self._profiles[key] for key in sorted(self._profiles))


__all__ = ["ModelProfile", "ModelRegistry"]
