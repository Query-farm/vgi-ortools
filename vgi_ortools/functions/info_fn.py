"""Introspection functions: ``solver_info`` and ``last_solve_stats``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

import pyarrow as pa
from vgi.metadata import FunctionExample
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
from ._meta import function_tags

_INFO_URL = "https://developers.google.com/optimization"
_STATS_URL = "https://developers.google.com/optimization/cp/cp_solver"

_INFO_SCHEMA = pa.schema(
    [
        sfield("component", pa.string(), "Component name (ortools, cp_sat, worker, dialect, ...).", nullable=False),
        sfield("version", pa.string(), "Component version string.", nullable=False),
    ]
)
_STATS_SCHEMA = pa.schema(
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
    """Report OR-Tools / worker / dialect versions, one row per component."""

    FunctionArguments: ClassVar[type] = NoArgs
    FIXED_SCHEMA: ClassVar[pa.Schema] = _INFO_SCHEMA

    class Meta:
        """SQL-facing metadata for ``solver_info``."""

        name = "solver_info"
        description = (
            "Report the OR-Tools version, the worker version, and the solve_cpsat JSON dialect version "
            "as (component, version) rows. Useful for diagnostics and compatibility checks."
        )
        categories = ["introspection", "version"]
        keywords = ["solver info", "version", "ortools version", "diagnostics", "introspection", "cp-sat"]
        tags = function_tags(
            title="Solver / Worker Versions",
            doc_llm=(
                "## `solver_info`\n\n"
                "Return the versions of the bundled solver and the worker as `(component, version)` "
                "rows: `ortools`, `cp_sat`, `glop`, `pdlp`, the `worker` version, and the `dialect` "
                "version of the `solve_cpsat` JSON model. No arguments."
            ),
            doc_md=(
                "# solver_info\n\n"
                "OR-Tools / worker / dialect versions.\n\n"
                "```sql\n"
                "SELECT component, version FROM ortools.solver_info() ORDER BY component;\n"
                "```\n"
            ),
            keywords=keywords,
            result_columns_md=(
                "| column | type | description |\n"
                "| --- | --- | --- |\n"
                "| `component` | `VARCHAR` | Component name. |\n"
                "| `version` | `VARCHAR` | Component version. |"
            ),
            source_url=_INFO_URL,
            executable_examples=[
                {
                    "description": "List the bundled solver and worker versions.",
                    "sql": "SELECT component, version FROM ortools.solver_info() ORDER BY component",
                }
            ],
        )
        examples = [
            FunctionExample(
                sql="SELECT component, version FROM ortools.solver_info() ORDER BY component",
                description="Solver and worker versions.",
            )
        ]

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
        emit_one(out, {"component": components, "version": versions}, _INFO_SCHEMA)
        out.finish()


@init_single_worker
@bind_fixed_schema
class LastSolveStats(TableFunctionGenerator[NoArgs]):
    """Return the statistics of the most recent solve handled by this worker."""

    FunctionArguments: ClassVar[type] = NoArgs
    FIXED_SCHEMA: ClassVar[pa.Schema] = _STATS_SCHEMA

    class Meta:
        """SQL-facing metadata for ``last_solve_stats``."""

        name = "last_solve_stats"
        description = (
            "Return the statistics of the most recent solve handled by this worker process "
            "(status, objective, best_bound, wall_time, num_branches, ...) as (metric, value) rows. "
            "A convenience mirror of the metadata columns already on each solver's result rows."
        )
        categories = ["introspection", "diagnostics"]
        keywords = ["last solve", "solve stats", "diagnostics", "wall time", "num branches", "introspection"]
        tags = function_tags(
            title="Last Solve Statistics",
            doc_llm=(
                "## `last_solve_stats`\n\n"
                "Return the statistics of the **most recent solve** handled by this worker process as "
                "`(metric, value)` rows: `status`, `objective`, `best_bound`, `wall_time`, "
                "`num_branches`, `num_conflicts`, `num_booleans`, `deterministic_time`. A convenience "
                "mirror of the metadata columns already present on each solver's result rows. No "
                "arguments. Note: this is per-process, not strictly per-connection -- the authoritative, "
                "race-free source is the metadata columns on the solver's own rows."
            ),
            doc_md=(
                "# last_solve_stats\n\n"
                "Statistics of the most recent solve.\n\n"
                "```sql\n"
                "SELECT metric, value FROM ortools.last_solve_stats();\n"
                "```\n"
            ),
            keywords=keywords,
            result_columns_md=(
                "| column | type | description |\n"
                "| --- | --- | --- |\n"
                "| `metric` | `VARCHAR` | Metric name. |\n"
                "| `value` | `VARCHAR` | Metric value (NULL until a solve has run). |"
            ),
            source_url=_STATS_URL,
            executable_examples=[
                {
                    "description": "Inspect the stats of the previous solve on this connection.",
                    "sql": [
                        "SELECT count(*) FROM ortools.solve_assignment("
                        "cost := [[0,0,1],[1,1,1]]::BIGINT[][], maximize := false)",
                        "SELECT metric, value FROM ortools.last_solve_stats() ORDER BY metric",
                    ],
                }
            ],
        )
        examples = [
            FunctionExample(
                sql="SELECT metric, value FROM ortools.last_solve_stats() ORDER BY metric",
                description="Most-recent solve statistics.",
            )
        ]

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
        emit_one(out, {"metric": metrics, "value": values}, _STATS_SCHEMA)
        out.finish()
