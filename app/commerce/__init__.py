"""Commerce domain foundation.

This package is intentionally detached from the current listing runtime.  It
contains business identities and transaction contracts only; production flows
must opt in explicitly in a later integration phase.
"""

from .scope import CommerceScope

__all__ = ["CommerceScope"]
