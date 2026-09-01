from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CommerceScope:
    """Stable ownership boundary for commerce data.

    The value is deliberately opaque.  Domain code must never infer ownership
    from a browser profile, marketplace account, filesystem path, or display
    label.  Infrastructure may map an authenticated application account or
    workspace to this identifier later.
    """

    workspace_id: str

    def __post_init__(self) -> None:
        normalized = str(self.workspace_id or "").strip()
        if not normalized:
            raise ValueError("commerce workspace_id must not be empty")
        if len(normalized) > 200:
            raise ValueError("commerce workspace_id is too long")
        object.__setattr__(self, "workspace_id", normalized)

    @classmethod
    def from_application_scope(cls, scope_id: str) -> "CommerceScope":
        return cls(workspace_id=scope_id)


__all__ = ["CommerceScope"]
