from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from .capabilities import ModelCapability
from .credentials import CredentialRef
from .profiles import ModelProfile


_ROLE_ID_RE = re.compile(r"^[a-z][a-z0-9._-]{0,127}$")


@dataclass(frozen=True, slots=True)
class ModelRole:
    """Stable application AI role with provider-neutral capability requirements."""

    role_id: str
    required_capabilities: frozenset[ModelCapability]
    allow_fallback: bool = False

    def __post_init__(self) -> None:
        role_id = str(self.role_id or "").strip().casefold()
        if not _ROLE_ID_RE.fullmatch(role_id):
            raise ValueError(f"invalid model role id: {self.role_id!r}")
        required = frozenset(ModelCapability(value) for value in self.required_capabilities)
        if not required:
            raise ValueError("model role must require at least one capability")
        object.__setattr__(self, "role_id", role_id)
        object.__setattr__(self, "required_capabilities", required)

    def bind(
        self,
        *,
        provider: str,
        model: str,
        capabilities: Iterable[ModelCapability],
        credential_ref: CredentialRef | None = None,
    ) -> ModelProfile:
        declared = frozenset(ModelCapability(value) for value in capabilities)
        missing = tuple(
            value.value for value in sorted(self.required_capabilities, key=lambda item: item.value)
            if value not in declared
        )
        if missing:
            raise ValueError(
                f"model binding for role {self.role_id!r} lacks required capabilities: "
                + ", ".join(missing)
            )
        return ModelProfile(
            profile_id=self.role_id,
            provider=provider,
            model=model,
            capabilities=declared,
            allow_fallback=self.allow_fallback,
            credential_ref=credential_ref,
        )


__all__ = ["ModelRole"]
