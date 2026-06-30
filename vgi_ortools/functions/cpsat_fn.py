"""``solve_cpsat`` -- the general CP-SAT JSON-model escape hatch (README section A)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, ClassVar

import pyarrow as pa
from vgi.arguments import Arg
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

from .. import stats
from ..arrow_io import emit_one
from ..limits import Limits
from ..models import cpsat
from ..schema_utils import field as sfield
from ._meta import function_tags

_SOURCE_URL = "https://developers.google.com/optimization/cp/cp_solver"

_SCHEMA = pa.schema(
    [
        sfield("var", pa.string(), "Variable name; intervals expand to <name>.start/.size/.end/.presence."),
        sfield("value", pa.int64(), "Assigned integer value (NULL on a non-solution status row)."),
        sfield("status", pa.string(), "OPTIMAL/FEASIBLE/INFEASIBLE/MODEL_INVALID/UNKNOWN.", nullable=False),
        sfield("objective", pa.float64(), "Objective value (NULL if no objective / no solution)."),
        sfield("best_bound", pa.float64(), "Best objective bound (the other end of the gap)."),
        sfield("wall_time", pa.float64(), "Solver wall-clock time in seconds.", nullable=False),
        sfield("num_branches", pa.int64(), "Number of search branches explored.", nullable=False),
    ]
)

_KNAPSACK_MODEL = (
    '{"version":1,'
    '"variables":[{"kind":"bool","name":"a"},{"kind":"bool","name":"b"},{"kind":"bool","name":"c"}],'
    '"constraints":[{"type":"linear","expr":{"sum":[{"coef":5,"var":"a"},{"coef":10,"var":"b"},'
    '{"coef":15,"var":"c"}]},"op":"<=","rhs":20}],'
    '"objective":{"sense":"maximize","terms":[{"coef":10,"var":"a"},{"coef":20,"var":"b"},'
    '{"coef":30,"var":"c"}]}}'
)
_MAKESPAN_MODEL = (
    '{"version":1,"variables":['
    '{"kind":"int","name":"s0","lb":0,"ub":20},{"kind":"int","name":"e0","lb":0,"ub":20},'
    '{"kind":"int","name":"s1","lb":0,"ub":20},{"kind":"int","name":"e1","lb":0,"ub":20},'
    '{"kind":"int","name":"s2","lb":0,"ub":20},{"kind":"int","name":"e2","lb":0,"ub":20},'
    '{"kind":"interval","name":"i0","start":{"var":"s0"},"size":{"const":3},"end":{"var":"e0"}},'
    '{"kind":"interval","name":"i1","start":{"var":"s1"},"size":{"const":5},"end":{"var":"e1"}},'
    '{"kind":"interval","name":"i2","start":{"var":"s2"},"size":{"const":2},"end":{"var":"e2"}},'
    '{"kind":"int","name":"makespan","lb":0,"ub":20}],'
    '"constraints":[{"type":"no_overlap","intervals":["i0","i1","i2"]},'
    '{"type":"max_equality","target":{"var":"makespan"},"exprs":[{"var":"e0"},{"var":"e1"},{"var":"e2"}]}],'
    '"objective":{"sense":"minimize","terms":[{"coef":1,"var":"makespan"}]}}'
)

_EXAMPLES = [
    {
        "description": "0/1 knapsack as a CP-SAT model: maximize value within a weight budget (optimum 50).",
        "sql": (
            f"SELECT var, value, status, objective FROM ortools.solve_cpsat(model := '{_KNAPSACK_MODEL}') ORDER BY var"
        ),
    },
    {
        "description": "Single-machine no-overlap schedule minimizing makespan (optimum 10).",
        "sql": (
            f"SELECT objective, status FROM ortools.solve_cpsat(model := '{_MAKESPAN_MODEL}', time_limit_s := 5.0) "
            "LIMIT 1"
        ),
    },
]


@dataclass(slots=True, frozen=True)
class SolveCpsatArgs:
    """Arguments for ``solve_cpsat``."""

    model: Annotated[
        str,
        Arg("model", default="", doc="The CP-SAT model as a JSON document (the versioned dialect, README section A)."),
    ]
    time_limit_s: Annotated[
        float, Arg("time_limit_s", default=10.0, doc="Wall-clock cap in seconds (clamped to the worker hard cap).")
    ]
    num_workers: Annotated[
        int, Arg("num_workers", default=0, doc="Parallel CP-SAT search workers (0 = single-threaded deterministic).")
    ]
    random_seed: Annotated[int, Arg("random_seed", default=1, doc="Random seed for a deterministic objective value.")]
    enumerate_all: Annotated[
        bool,
        Arg(
            "enumerate",
            default=False,
            doc="Reserved: enumerate feasible solutions (solution-pool streaming is roadmap).",
        ),
    ]
    project: Annotated[
        str,
        Arg("project", default="", doc="Optional comma-separated subset of variable names to emit (others dropped)."),
    ]


@init_single_worker
@bind_fixed_schema
class SolveCpsat(TableFunctionGenerator[SolveCpsatArgs]):
    """Solve a hand-built CP-SAT model expressed in the JSON dialect."""

    FunctionArguments: ClassVar[type] = SolveCpsatArgs
    FIXED_SCHEMA: ClassVar[pa.Schema] = _SCHEMA

    class Meta:
        """SQL-facing metadata for ``solve_cpsat``."""

        name = "solve_cpsat"
        description = (
            "Solve an arbitrary CP-SAT constraint-programming model expressed as a versioned JSON "
            "document (variables, constraints, objective). Returns one row per variable plus solve "
            "metadata. The general escape hatch for constraints highs/LP cannot express."
        )
        categories = ["optimization", "constraint-programming", "cp-sat"]
        keywords = [
            "cp-sat",
            "constraint programming",
            "constraint solver",
            "satisfiability",
            "integer programming",
            "scheduling",
            "all_different",
            "no_overlap",
            "cumulative",
            "circuit",
            "or-tools",
            "optimization",
        ]
        tags = function_tags(
            title="CP-SAT Constraint Solver (JSON Model)",
            doc_llm=(
                "## `solve_cpsat`\n\n"
                "Solve an **arbitrary CP-SAT constraint-programming model** handed in as a **versioned "
                "JSON document** -- the general escape hatch for everything the per-domain builders and "
                "the `highs` LP/MIP extension cannot express (global constraints like `all_different`, "
                "`no_overlap`, `cumulative`, `circuit`, reification, intervals).\n\n"
                "**The model.** A JSON object with `version` (must be 1), `variables` (each `int` / "
                "`bool` / `interval` with a unique `name`), `constraints`, and an optional `objective`. "
                "All numeric domains are **integers** -- a fractional bound / coefficient / constant is "
                "rejected with a clear error (scale floats to integers first). The full grammar is in "
                "the README (section A).\n\n"
                "**Outputs.** One row per variable: `var`, `value`, plus the solve metadata (`status`, "
                "`objective`, `best_bound`, `wall_time`, `num_branches`) repeated on every row so a "
                "single SELECT is self-describing. Interval variables expand to `<name>.start` / "
                "`.size` / `.end` (and `.presence` for optional intervals).\n\n"
                "**The empty-vs-infeasible contract.** A model with no solution still returns **exactly "
                "one row** carrying `status` in {`INFEASIBLE`, `MODEL_INVALID`, `UNKNOWN`} with NULL "
                "`var`/`value`; a solve is never an empty result set. Test `WHERE status='INFEASIBLE'`, "
                "never `count(*)=0`.\n\n"
                "**Determinism.** Single-threaded by default (`num_workers := 0`) with a fixed "
                "`random_seed`, so the objective and assignment are reproducible."
            ),
            doc_md=(
                "# solve_cpsat\n\n"
                "Solve a hand-built **CP-SAT** model expressed as a versioned JSON document.\n\n"
                "```sql\n"
                "SELECT var, value, status, objective\n"
                "FROM ortools.solve_cpsat(model := '<json>', time_limit_s := 5.0);\n"
                "```\n\n"
                "See the README (section A) for the full JSON dialect: `variables` (int / bool / "
                "interval), `constraints` (linear, all_different, element, table, no_overlap, "
                "cumulative, circuit, reservoir, mult/div/abs/min/max equality, bool_or/and/xor, "
                "implication, with optional `enforce` reification), and an optional `objective`.\n\n"
                "All numeric domains are integers. A no-solution model returns a single status row."
            ),
            keywords=keywords,
            result_columns_md=(
                "| column | type | description |\n"
                "| --- | --- | --- |\n"
                "| `var` | `VARCHAR` | Variable name; intervals expand to `<name>.start/.size/.end/.presence`. |\n"
                "| `value` | `BIGINT` | Assigned value (NULL on a non-solution status row). |\n"
                "| `status` | `VARCHAR` | `OPTIMAL` / `FEASIBLE` / `INFEASIBLE` / `MODEL_INVALID` / `UNKNOWN`. |\n"
                "| `objective` | `DOUBLE` | Objective value (NULL if no objective / no solution). |\n"
                "| `best_bound` | `DOUBLE` | Best objective bound (the other end of the optimality gap). |\n"
                "| `wall_time` | `DOUBLE` | Solver wall-clock seconds. |\n"
                "| `num_branches` | `BIGINT` | Search branches explored. |"
            ),
            source_url=_SOURCE_URL,
            executable_examples=_EXAMPLES,
        )
        examples = [
            FunctionExample(
                sql=_EXAMPLES[0]["sql"],
                description="0/1 knapsack as a CP-SAT model (optimum 50).",
            )
        ]

    @classmethod
    def cardinality(cls, params: BindParams[SolveCpsatArgs]) -> TableCardinality:
        """Estimate the output cardinality (one row per variable)."""
        return TableCardinality(estimate=16, max=1_000_000)

    @classmethod
    def process(cls, params: ProcessParams[SolveCpsatArgs], state: None, out: OutputCollector) -> None:
        """Build, solve, and emit the per-variable rows + solve metadata."""
        a = params.args
        limits = cls._limits(params)
        project = [p.strip() for p in a.project.split(",") if p.strip()] or None
        result = cpsat.solve(
            a.model,
            time_limit_s=a.time_limit_s,
            num_workers=a.num_workers or None,
            random_seed=a.random_seed,
            enumerate_all=bool(a.enumerate_all),
            project=project,
            limits=limits,
        )
        stats.record(**result.stats())
        n = len(result.var)
        emit_one(
            out,
            {
                "var": result.var,
                "value": result.value,
                "status": [result.status] * n,
                "objective": [result.objective] * n,
                "best_bound": [result.best_bound] * n,
                "wall_time": [result.wall_time] * n,
                "num_branches": [result.num_branches] * n,
            },
            _SCHEMA,
        )
        out.finish()

    @classmethod
    def _limits(cls, params: ProcessParams[SolveCpsatArgs]) -> Limits:
        return Limits.from_settings(getattr(params, "settings", None))
