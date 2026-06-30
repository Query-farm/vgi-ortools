"""Marshal the ``LIST<LIST<BIGINT>>`` matrices that arrive as scalar arguments.

Matrices reach the worker as a single Arrow ``list<list<int64>>`` scalar,
already converted by the SDK to a Python ``list[list[int]]`` (built in SQL with
``array_agg([from, to, w])``). This module reads those into the structured
forms the solvers want -- sparse ``(i, j) -> w`` dicts and dense ``n x n``
matrices -- applying the integer-scaling rule on the way in, and provides the
output side: a single-batch emitter and the canonical all-NULL "no solution"
status row so a solve is *never* an empty result set.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pyarrow as pa

from .validate import ModelValidationError, ensure_int, ensure_int_list, require


def rows_of(value: Any, *, where: str) -> list[list[int]]:
    """Read a ``LIST<LIST<BIGINT>>`` scalar argument into a list of int rows.

    Args:
        value: The Python value the SDK produced for the LIST argument (a list of
            lists, or ``None``).
        where: A human label for the argument (used in error messages).

    Returns:
        A list of integer rows (empty list when ``value`` is ``None``).

    Raises:
        ModelValidationError: If the value is not a list of lists of integers.
    """
    if value is None:
        return []
    require(
        isinstance(value, (list, tuple)),
        f"{where} must be a LIST (e.g. array_agg([...])), got {type(value).__name__}",
    )
    out: list[list[int]] = []
    for i, row in enumerate(value):
        require(
            isinstance(row, (list, tuple)),
            f"{where}[{i}] must be a LIST of integers (a [..] tuple), got {type(row).__name__}",
        )
        out.append(ensure_int_list(row, where=f"{where}[{i}]"))
    return out


def flat_int_list(value: Any, *, where: str) -> list[int]:
    """Read a ``LIST<BIGINT>`` scalar argument into a flat list of ints.

    Args:
        value: The Python value for the LIST argument (a list of ints or ``None``).
        where: A human label for the argument.

    Returns:
        A flat list of ints (empty when ``value`` is ``None``).

    Raises:
        ModelValidationError: If any element is not an integer.
    """
    if value is None:
        return []
    require(isinstance(value, (list, tuple)), f"{where} must be a LIST of integers")
    return ensure_int_list(value, where=where)


def sparse_matrix(rows: Sequence[Sequence[int]], *, where: str) -> tuple[dict[tuple[int, int], int], int]:
    """Read sparse ``[[from, to, w], ...]`` triples into a ``(i, j) -> w`` dict.

    Args:
        rows: The integer triples.
        where: A human label for the argument.

    Returns:
        A tuple ``(edges, n)`` where ``edges`` maps ``(from, to)`` to weight and
        ``n`` is one past the largest node id seen (the dense node count).

    Raises:
        ModelValidationError: If a triple is malformed or a node id is negative.
    """
    edges: dict[tuple[int, int], int] = {}
    n = 0
    for k, row in enumerate(rows):
        require(len(row) == 3, f"{where}[{k}] must be a [from, to, weight] triple, got {len(row)} value(s)")
        i, j, w = int(row[0]), int(row[1]), int(row[2])
        require(i >= 0 and j >= 0, f"{where}[{k}] has a negative node id ({i}, {j})")
        edges[(i, j)] = w
        n = max(n, i + 1, j + 1)
    return edges, n


def as_distance_matrix(value: Any, *, where: str = "distance") -> tuple[list[list[int]], int]:
    """Read a distance argument (sparse triples or dense ``n x n``) into a dense matrix.

    The shape is inferred: rows of width 3 are sparse ``[from, to, w]`` triples;
    any other (square) shape is read as a dense row-major ``n x n`` matrix.
    Missing sparse arcs become a large sentinel so the router treats them as
    (effectively) disallowed.

    Args:
        value: The LIST argument value.
        where: A human label for the argument.

    Returns:
        A tuple ``(matrix, n)``: a dense ``n x n`` list-of-lists of ints.

    Raises:
        ModelValidationError: If the argument is empty or malformed.
    """
    rows = rows_of(value, where=where)
    require(len(rows) > 0, f"{where} is required and must be non-empty")

    widths = {len(r) for r in rows}
    is_sparse = widths == {3}

    if is_sparse:
        edges, n = sparse_matrix(rows, where=where)
        require(n > 0, f"{where} has no nodes")
        big = _sentinel(edges)
        matrix = [[0 if i == j else big for j in range(n)] for i in range(n)]
        for (i, j), w in edges.items():
            matrix[i][j] = w
        return matrix, n

    # Dense form: a square list-of-lists.
    n = len(rows)
    require(all(len(r) == n for r in rows), f"{where} dense matrix must be square (n x n); got rows of widths {widths}")
    return [list(r) for r in rows], n


def _sentinel(edges: dict[tuple[int, int], int]) -> int:
    """A large but safe 'no edge' distance for a sparse matrix.

    Args:
        edges: The known edge weights.

    Returns:
        A value comfortably larger than the sum of all known edges, so a route
        forced across a missing arc is always worse than any real route, without
        overflowing the routing solver's int64 accumulation.
    """
    total = sum(abs(w) for w in edges.values())
    return max(1, total) * 1000 + 1_000_000


def emit_one(out: Any, columns: dict[str, list[Any]], schema: pa.Schema) -> None:
    """Emit a single record batch built from column lists, then finish.

    Args:
        out: The VGI output collector.
        columns: Column-name -> list-of-values (every list the same length).
        schema: The output Arrow schema (explicit per-column types).
    """
    out.emit(pa.RecordBatch.from_pydict(columns, schema=schema))


def null_row(schema: pa.Schema, *, status: str, **fixed: Any) -> dict[str, list[Any]]:
    """Build the canonical one-row 'no solution' batch payload for a schema.

    Every column is a single NULL except ``status`` (set to ``status``) and any
    column named in ``fixed`` (set to the given scalar). This realizes the
    empty-vs-infeasible contract: a solve with no solution still yields exactly
    one row carrying the status, never an empty result set.

    Args:
        schema: The output schema.
        status: The status string for the single row.
        **fixed: Optional overrides for specific columns.

    Returns:
        A column-name -> single-element-list dict suitable for :func:`emit_one`.
    """
    cols: dict[str, list[Any]] = {}
    for f in schema:
        if f.name == "status":
            cols[f.name] = [status]
        elif f.name in fixed:
            cols[f.name] = [fixed[f.name]]
        else:
            cols[f.name] = [None]
    return cols


__all__ = [
    "ModelValidationError",
    "as_distance_matrix",
    "emit_one",
    "ensure_int",
    "flat_int_list",
    "null_row",
    "rows_of",
    "sparse_matrix",
]
