"""Best-effort 'most recent solve' statistics for ``last_solve_stats()``.

This worker is otherwise stateless -- each table-function call is a self-contained
solve. ``last_solve_stats()`` is a convenience mirror of the metadata columns
already present on each solver's rows: it records the stats of the most recent
solve handled by this worker *process* in a module global, and returns them as
``(metric, value)`` rows.

Honest limitation: the value is per-*process*, not strictly per-DuckDB-connection
(a shared worker process serves multiple connections), so under concurrency it
reflects whichever solve most recently finished. The metadata columns on each
solver's own result rows are the authoritative, race-free source; this is the
documented convenience.
"""

from __future__ import annotations

import threading
from typing import Any

_LOCK = threading.Lock()
_LAST: dict[str, Any] = {
    "status": None,
    "objective": None,
    "best_bound": None,
    "wall_time": None,
    "num_branches": None,
    "num_conflicts": None,
    "num_booleans": None,
    "deterministic_time": None,
}


def record(**stats: Any) -> None:
    """Record the stats of a completed solve (only the keys provided are updated).

    Args:
        **stats: Any subset of the known metric keys.
    """
    with _LOCK:
        for key in _LAST:
            if key in stats:
                _LAST[key] = stats[key]


def snapshot() -> dict[str, Any]:
    """Return a copy of the most recent solve's stats.

    Returns:
        A dict mapping each metric name to its last recorded value (or ``None``).
    """
    with _LOCK:
        return dict(_LAST)
