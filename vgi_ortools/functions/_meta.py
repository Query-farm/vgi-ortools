"""Shared metadata helpers + common argument plumbing for the SQL functions.

Centralizes the per-function tag block the vgi-lint metadata gate requires
(``vgi.title`` / ``vgi.doc_llm`` / ``vgi.doc_md`` / ``vgi.keywords`` /
``vgi.result_columns_schema`` (or ``vgi.result_dynamic_columns_md``) /
``vgi.executable_examples``) so each function module only supplies its prose,
and the Arrow LIST types reused across the solvers.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pyarrow as pa

# Arrow types for the scalar LIST / matrix arguments.
LIST_INT = pa.list_(pa.int64())
LIST_LIST_INT = pa.list_(pa.list_(pa.int64()))
LIST_STR = pa.list_(pa.string())

# ```sql fenced blocks embedded in vgi.doc_md are flagged by VGI179 (a complete
# runnable query belongs in vgi.example_queries / vgi.executable_examples, not in
# prose). The executable_examples already carry every runnable query, so we strip
# fenced SQL from the human doc_md centrally.
_SQL_FENCE_RE = re.compile(r"```sql\b.*?```", re.DOTALL | re.IGNORECASE)
_BLANK_RUN_RE = re.compile(r"\n{3,}")

# Arrow -> DuckDB SQL type names for the result_columns_schema (VGI307/VGI414).
_ARROW_SQL_TYPE = {
    "int64": "BIGINT",
    "int32": "INTEGER",
    "int16": "SMALLINT",
    "int8": "TINYINT",
    "double": "DOUBLE",
    "float": "REAL",
    "string": "VARCHAR",
    "large_string": "VARCHAR",
    "bool": "BOOLEAN",
}


def _sql_type(dt: pa.DataType) -> str:
    """Map an Arrow data type to its DuckDB SQL type name for the column schema."""
    return _ARROW_SQL_TYPE.get(str(dt), str(dt).upper())


def result_columns_schema(schema: pa.Schema) -> list[dict[str, str]]:
    """Derive the ``vgi.result_columns_schema`` array from an Arrow output schema.

    Each entry is ``{"name", "type", "description"}`` -- the description is read
    from the field's ``comment`` metadata (set by ``schema_utils.field``), so the
    machine-readable result schema can never drift from the columns the worker
    actually emits.

    Args:
        schema: The Arrow schema the table function emits.

    Returns:
        A list of ``{name, type, description}`` dicts, one per column.
    """
    cols: list[dict[str, str]] = []
    for f in schema:
        meta = f.metadata or {}
        desc = meta.get(b"comment", b"").decode("utf-8")
        cols.append({"name": f.name, "type": _sql_type(f.type), "description": desc})
    return cols


def _clean_doc_md(doc_md: str, source_url: str) -> str:
    """Strip runnable ```sql fences (VGI179) and append the OR-Tools reference link."""
    stripped = _BLANK_RUN_RE.sub("\n\n", _SQL_FENCE_RE.sub("", doc_md)).strip()
    return f"{stripped}\n\n**Reference:** [OR-Tools documentation]({source_url})"


def function_tags(
    *,
    title: str,
    category: str,
    doc_llm: str,
    doc_md: str,
    keywords: list[str],
    source_url: str,
    executable_examples: list[dict[str, Any]],
    result_schema: pa.Schema | None = None,
    result_dynamic_columns_md: str | None = None,
) -> dict[str, str]:
    """Build the per-function tag block the vgi-lint metadata gate checks.

    The OR-Tools docs deep link (``source_url``) is appended to ``vgi.doc_md`` as
    a *Reference* link rather than set as ``vgi.source_url``: the metadata linter
    (VGI139) treats a per-object ``vgi.source_url`` as redundant with the
    catalog's, so the canonical ``source_url`` lives only on the catalog while
    each function still surfaces its OR-Tools reference inline.

    Args:
        title: Human display title (must differ from the machine name).
        category: The ``vgi.category`` this function belongs to; must name one of
            the schema's ``vgi.categories`` registry entries (VGI411).
        doc_llm: Agent-facing Markdown narrative (VGI112).
        doc_md: Human-facing Markdown narrative (VGI113). Any ```sql fence is
            stripped (VGI179); the runnable form lives in ``executable_examples``.
        keywords: Synonym keywords (VGI126/138).
        source_url: A stable OR-Tools docs deep link, appended to ``doc_md``.
        executable_examples: Self-contained, catalog-qualified runnable examples
            (VGI509); each is ``{"description", "sql"}`` where ``sql`` is a string
            or a list of statements run in order.
        result_schema: The Arrow output schema of a static-schema table function;
            emitted as the structured ``vgi.result_columns_schema`` (VGI307/414).
        result_dynamic_columns_md: For a table function whose columns vary by
            argument (e.g. ``solve_vrp``), a Markdown note describing the dynamic
            columns; emitted as ``vgi.result_dynamic_columns_md`` (VGI307). Exactly
            one of ``result_schema`` / ``result_dynamic_columns_md`` is set for a
            table function; a scalar function passes neither.

    Returns:
        The tag dictionary for the function ``Meta.tags``.
    """
    tags = {
        "vgi.title": title,
        "vgi.category": category,
        "vgi.doc_llm": doc_llm,
        "vgi.doc_md": _clean_doc_md(doc_md, source_url),
        "vgi.keywords": json.dumps(keywords),
        "vgi.executable_examples": json.dumps(executable_examples),
    }
    if result_schema is not None:
        tags["vgi.result_columns_schema"] = json.dumps(result_columns_schema(result_schema))
    if result_dynamic_columns_md is not None:
        tags["vgi.result_dynamic_columns_md"] = result_dynamic_columns_md
    return tags
