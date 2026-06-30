"""Arrow-schema helpers for the ortools worker.

Keeps column-comment plumbing in one place so every solver exposes consistent,
documented schemas to DuckDB. Mirrors the sibling vgi-explain / vgi-match
helper.
"""

from __future__ import annotations

import pyarrow as pa


def field(
    name: str,
    type: pa.DataType,
    comment: str,
    *,
    nullable: bool = True,
) -> pa.Field:
    """Build a ``pa.Field`` carrying a column comment in its metadata.

    The ``comment`` metadata key is the framework's transport for column
    comments -- DuckDB surfaces it via ``duckdb_columns()`` and ``DESCRIBE``.

    Args:
        name: Column name.
        type: Arrow data type for the column.
        comment: Human-facing column comment.
        nullable: Whether the column may contain NULLs.

    Returns:
        A ``pa.Field`` with the comment attached as Arrow field metadata.
    """
    return pa.field(
        name,
        type,
        nullable=nullable,
        metadata={b"comment": comment.encode("utf-8")},
    )
