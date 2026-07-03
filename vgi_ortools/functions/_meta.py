"""Shared metadata helpers + common argument plumbing for the SQL functions.

Centralizes the per-function tag block the vgi-lint metadata gate requires
(``vgi.title`` / ``vgi.doc_llm`` / ``vgi.doc_md`` / ``vgi.keywords`` /
``vgi.result_columns_md`` / ``vgi.source_url`` / ``vgi.executable_examples``) so
each function module only supplies its prose, and the Arrow LIST types reused
across the solvers.
"""

from __future__ import annotations

import json
from typing import Any

import pyarrow as pa

# Arrow types for the scalar LIST / matrix arguments.
LIST_INT = pa.list_(pa.int64())
LIST_LIST_INT = pa.list_(pa.list_(pa.int64()))
LIST_STR = pa.list_(pa.string())


def function_tags(
    *,
    title: str,
    category: str,
    doc_llm: str,
    doc_md: str,
    keywords: list[str],
    result_columns_md: str,
    source_url: str,
    executable_examples: list[dict[str, Any]],
) -> dict[str, str]:
    """Build the per-function tag block the vgi-lint gate checks.

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
        doc_md: Human-facing Markdown narrative (VGI113).
        keywords: Synonym keywords (VGI126/138).
        result_columns_md: A Markdown table documenting the result columns.
        source_url: A stable OR-Tools docs deep link, appended to ``doc_md``.
        executable_examples: Self-contained, catalog-qualified runnable examples
            (VGI509); each is ``{"description", "sql"}`` where ``sql`` is a string
            or a list of statements run in order.

    Returns:
        The tag dictionary for the function ``Meta.tags``.
    """
    return {
        "vgi.title": title,
        "vgi.category": category,
        "vgi.doc_llm": doc_llm,
        "vgi.doc_md": f"{doc_md}\n\n**Reference:** [OR-Tools documentation]({source_url})",
        "vgi.keywords": json.dumps(keywords),
        "vgi.result_columns_md": result_columns_md,
        "vgi.executable_examples": json.dumps(executable_examples),
    }
