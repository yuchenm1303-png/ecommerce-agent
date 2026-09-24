from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


_ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")
_ALIAS_RE = re.compile(r"^[a-z][a-z0-9._/-]{0,127}$")


class CredentialSource(str, Enum):
    """Where a model credential is resolved at runtime.

    The AI platform stores only references. Secret values stay in the existing
    environment/OS credential/runtime secret mechanism and never enter a model
    profile or repository configuration.
    """

    ENVIRONMENT = "environment"
    OS_KEYCHAIN = "os_keychain"
    RUNTIME = "runtime"


@dataclass(frozen=True, slots=True)
class CredentialRef:
    source: CredentialSource
    name: str

    def __post_init__(self) -> None:
        source = CredentialSource(self.source)
        name = str(self.name or "").strip()
        if source is CredentialSource.ENVIRONMENT:
            if not _ENV_NAME_RE.fullmatch(name):
                raise ValueError(f"invalid environment credential reference: {name!r}")
        elif not _ALIAS_RE.fullmatch(name):
            raise ValueError(f"invalid credential alias: {name!r}")
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "name", name)

    @classmethod
    def environment(cls, variable_name: str) -> "CredentialRef":
        return cls(CredentialSource.ENVIRONMENT, variable_name)

    @classmethod
    def os_keychain(cls, alias: str) -> "CredentialRef":
        return cls(CredentialSource.OS_KEYCHAIN, alias)

    @classmethod
    def runtime(cls, alias: str) -> "CredentialRef":
        return cls(CredentialSource.RUNTIME, alias)

    def as_safe_dict(self) -> dict[str, str]:
        return {"source": self.source.value, "name": self.name}


__all__ = ["CredentialRef", "CredentialSource"]
