"""Scan backings for the ``solver_info`` and ``last_solve_stats`` tables.

These two generators are **not** registered as callable table functions -- they
back browsable base *tables* (``ortools.main.solver_info`` /
``ortools.main.last_solve_stats``), declared in :mod:`vgi_ortools.catalog` via
``Table(function=...)``. Exposing them as tables (rather than parameterless table
functions) is the idiomatic VGI shape (VGI311/VGI146): an agent can scan them
directly to discover the solver versions or the last solve's metrics. The rich
per-object metadata (title / docs / examples) therefore lives on the ``Table``
descriptors in the catalog, not here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

import pyarrow as pa
from vgi.table_function import (
    BindParams,
    ProcessParams,
    TableCardinality,
    TableFunctionGenerator,
    bind_fixed_schema,
    init_single_worker,
)
from vgi_rpc.rpc import OutputCollector

from .. import DIALECT_VERSION, __version__, stats
from ..arrow_io import emit_one
from ..schema_utils import field as sfield

INFO_SCHEMA = pa.schema(
    [
        sfield("component", pa.string(), "Component name (ortools, cp_sat, worker, dialect, ...).", nullable=False),
        sfield("version", pa.string(), "Component version string.", nullable=False),
    ]
)
STATS_SCHEMA = pa.schema(
    [
        sfield("metric", pa.string(), "Metric name from the most recent solve.", nullable=False),
        sfield("value", pa.string(), "Metric value as a string (NULL string when not yet set)."),
    ]
)


@dataclass(slots=True, frozen=True)
class NoArgs:
    """No arguments."""


@init_single_worker
@bind_fixed_schema
class SolverInfo(TableFunctionGenerator[NoArgs]):
    """Scan backing for the ``solver_info`` table: OR-Tools / worker / dialect versions."""

    FunctionArguments: ClassVar[type] = NoArgs
    FIXED_SCHEMA: ClassVar[pa.Schema] = INFO_SCHEMA

    class Meta:
        """Backing-scan metadata for the ``solver_info`` table."""

        name = "solver_info_scan"
        description = "Scan backing for the solver_info table (OR-Tools / worker / dialect versions)."

    @classmethod
    def cardinality(cls, params: BindParams[NoArgs]) -> TableCardinality:
        """Estimate the output cardinality (a handful of rows)."""
        return TableCardinality(estimate=6, max=64)

    @classmethod
    def process(cls, params: ProcessParams[NoArgs], state: None, out: OutputCollector) -> None:
        """Emit the version rows."""
        import ortools

        ort = getattr(ortools, "__version__", "unknown")
        components = ["ortools", "cp_sat", "glop", "pdlp", "worker", "dialect"]
        versions = [ort, ort, ort, ort, __version__, str(DIALECT_VERSION)]
        emit_one(out, {"component": components, "version": versions}, INFO_SCHEMA)
        out.finish()


@init_single_worker
@bind_fixed_schema
class LastSolveStats(TableFunctionGenerator[NoArgs]):
    """Scan backing for the ``last_solve_stats`` table: the most recent solve's metrics."""

    FunctionArguments: ClassVar[type] = NoArgs
    FIXED_SCHEMA: ClassVar[pa.Schema] = STATS_SCHEMA

    class Meta:
        """Backing-scan metadata for the ``last_solve_stats`` table."""

        name = "last_solve_stats_scan"
        description = "Scan backing for the last_solve_stats table (the most recent solve's metrics)."

    @classmethod
    def cardinality(cls, params: BindParams[NoArgs]) -> TableCardinality:
        """Estimate the output cardinality (a handful of rows)."""
        return TableCardinality(estimate=8, max=64)

    @classmethod
    def process(cls, params: ProcessParams[NoArgs], state: None, out: OutputCollector) -> None:
        """Emit the most-recent solve's stats as (metric, value) rows."""
        snap = stats.snapshot()
        metrics = list(snap.keys())
        values = [None if v is None else str(v) for v in snap.values()]
        emit_one(out, {"metric": metrics, "value": values}, STATS_SCHEMA)
        out.finish()
