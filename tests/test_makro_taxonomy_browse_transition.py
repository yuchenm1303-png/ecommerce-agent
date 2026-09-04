from __future__ import annotations

from app.makro.taxonomy_resilient import (
    _browse_transition_ready,
    _root_surface_signature,
)


def test_browse_transition_rejects_stale_query_surface_even_after_input_rows_disappear() -> None:
    prior = ("query-a", "query-b")
    stale_root = ("group-1", "owner-1", prior)

    assert not _browse_transition_ready(prior, prior, stale_root)
    assert not _browse_transition_ready(prior, (), stale_root)


def test_browse_transition_accepts_repainted_or_replaced_structural_root() -> None:
    prior = ("query-a", "query-b")
    browse_root = ("group-1", "owner-1", ("browse-a", "browse-b"))

    assert _browse_transition_ready(prior, (), browse_root)
    assert _browse_transition_ready(prior, ("browse-a", "browse-b"), browse_root)


def test_browse_transition_with_no_query_rows_requires_only_a_real_root() -> None:
    browse_root = ("group-2", "owner-2", ("browse-a",))

    assert _browse_transition_ready((), (), browse_root)
    assert not _browse_transition_ready((), (), None)


def test_root_surface_signature_requires_one_structural_root() -> None:
    descriptors = [
        {
            "group_id": "root",
            "owner_id": "owner",
            "items": ["A", "B"],
            "is_root": True,
        },
        {
            "group_id": "child",
            "owner_id": "child-owner",
            "items": ["C"],
            "is_root": False,
        },
    ]

    assert _root_surface_signature(descriptors) == ("root", "owner", ("a", "b"))
    assert _root_surface_signature([{**descriptors[0], "is_root": False}]) is None
