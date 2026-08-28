from __future__ import annotations

# Deprecated module retained only as an import tombstone for source trees that
# update files incrementally. Batch no longer creates secondary Edge processes;
# see gui.batch_browser_session for the single-browser session owner.

raise ImportError(
    "gui.batch_browser_pool was removed: Batch parallelism now uses one Makro Edge "
    "with targetId-owned tabs via gui.batch_browser_session"
)
