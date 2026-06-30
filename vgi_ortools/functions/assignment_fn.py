"""``solve_assignment`` -- linear sum assignment as a table function."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, ClassVar

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
from ..arrow_io import emit_one, null_row, rows_of
from ..models import assignment
from ..schema_utils import field as sfield
from ._meta import LIST_LIST_INT, function_tags

_SOURCE_URL = "https://developers.google.com/optimization/assignment/linear_assignment"

_SCHEMA = pa.schema(
    [
        sfield("worker", pa.int64(), "Worker id from the input arcs.", nullable=False),
        sfield("task", pa.int64(), "Task this worker is assigned to."),
        sfield("cost", pa.int64(), "Arc cost of this match."),
        sfield("status", pa.string(), "OPTIMAL, or INFEASIBLE when no perfect matching exists.", nullable=False),
    ]
)

_EXAMPLES = [
    {
        "description": "Assign 3 workers to 3 tasks minimizing total cost (unique optimum 0->1, 1->0, 2->2).",
        "sql": (
            "SELECT worker, task, cost FROM ortools.solve_assignment("
            "cost := [[0,0,90],[0,1,76],[0,2,75],[1,0,35],[1,1,85],[1,2,55],"
            "[2,0,125],[2,1,95],[2,2,90]]::BIGINT[][], maximize := false) ORDER BY worker"
        ),
    },
    {
        "description": "Maximize total value instead of minimizing cost.",
        "sql": (
            "SELECT worker, task, cost, status FROM ortools.solve_assignment("
            "cost := [[0,0,8],[0,1,7],[1,0,6],[1,1,9]]::BIGINT[][], maximize := true) ORDER BY worker"
        ),
    },
]


@dataclass(slots=True, frozen=True)
class SolveAssignmentArgs:
    """Arguments for ``solve_assignment``."""

    cost: Annotated[
        list[Any] | None,
        Arg(
            "cost",
            default=None,
            arrow_type=LIST_LIST_INT,
            doc="Sparse [worker, task, cost] integer triples (array_agg([w, t, c])); a missing pair is disallowed.",
        ),
    ]
    maximize: Annotated[bool, Arg("maximize", default=False, doc="Maximize total cost instead of minimizing.")]


@init_single_worker
@bind_fixed_schema
class SolveAssignment(TableFunctionGenerator[SolveAssignmentArgs]):
    """Minimum (or maximum) cost matching of workers to tasks."""

    FunctionArguments: ClassVar[type] = SolveAssignmentArgs
    FIXED_SCHEMA: ClassVar[pa.Schema] = _SCHEMA

    class Meta:
        """SQL-facing metadata for ``solve_assignment``."""

        name = "solve_assignment"
        description = (
            "Linear sum assignment: match workers to tasks over sparse [worker, task, cost] arcs, "
            "minimizing (or maximizing) total cost. Backed by OR-Tools SimpleLinearSumAssignment."
        )
        categories = ["optimization", "assignment", "matching"]
        keywords = [
            "assignment problem",
            "linear sum assignment",
            "hungarian algorithm",
            "bipartite matching",
            "worker task",
            "minimum cost matching",
            "or-tools",
            "optimization",
        ]
        tags = function_tags(
            title="Linear Assignment (Min-Cost Matching)",
            doc_llm=(
                "## `solve_assignment`\n\n"
                "Solve a **linear sum assignment**: match each worker to at most one task (and each "
                "task to at most one worker) so the **total arc cost is minimized** (or maximized).\n\n"
                "**Inputs.** `cost` is a sparse `LIST<LIST<BIGINT>>` of `[worker, task, cost]` integer "
                "triples (build it with `array_agg([w, t, c])`); a pair that is absent is a disallowed "
                "assignment. `maximize := true` negates costs internally. All costs must be integers.\n\n"
                "**Outputs.** One row per matched worker: `worker`, `task`, `cost`, `status`. When no "
                "perfect matching of every worker exists the result is **exactly one row** with "
                "`status = 'INFEASIBLE'` and NULL `task`/`cost` -- never an empty set.\n\n"
                "**Edge cases.** A non-integer cost raises a clear error (scale to integers first)."
            ),
            doc_md=(
                "# solve_assignment\n\n"
                "Minimum- (or maximum-) cost matching of workers to tasks over sparse "
                "`[worker, task, cost]` arcs.\n\n"
                "```sql\n"
                "SELECT worker, task, cost FROM ortools.solve_assignment(\n"
                "  cost := (SELECT array_agg([w, t, c]) FROM costs), maximize := false);\n"
                "```\n\n"
                "Returns one row per matched worker; an unmatchable instance returns a single "
                "`status = 'INFEASIBLE'` row."
            ),
            keywords=keywords,
            result_columns_md=(
                "| column | type | description |\n"
                "| --- | --- | --- |\n"
                "| `worker` | `BIGINT` | Worker id. |\n"
                "| `task` | `BIGINT` | Task assigned to the worker (NULL on an INFEASIBLE row). |\n"
                "| `cost` | `BIGINT` | Arc cost of this match (NULL on an INFEASIBLE row). |\n"
                "| `status` | `VARCHAR` | `OPTIMAL` or `INFEASIBLE`. |"
            ),
            source_url=_SOURCE_URL,
            executable_examples=_EXAMPLES,
        )
        examples = [
            FunctionExample(
                sql=_EXAMPLES[0]["sql"],
                description="Assign 3 workers to 3 tasks minimizing total cost.",
            )
        ]

    @classmethod
    def cardinality(cls, params: BindParams[SolveAssignmentArgs]) -> TableCardinality:
        """Estimate the output cardinality (one row per worker)."""
        return TableCardinality(estimate=8, max=1_000_000)

    @classmethod
    def process(cls, params: ProcessParams[SolveAssignmentArgs], state: None, out: OutputCollector) -> None:
        """Solve the assignment and emit the matched rows (or one INFEASIBLE row)."""
        a = params.args
        cost = rows_of(a.cost, where="cost")
        result = assignment.solve(cost, maximize=bool(a.maximize))
        stats.record(status=result.status, objective=float(sum(result.cost)) if result.cost else None)
        if not result.worker:
            emit_one(out, null_row(_SCHEMA, status=result.status), _SCHEMA)
        else:
            emit_one(
                out,
                {
                    "worker": result.worker,
                    "task": result.task,
                    "cost": result.cost,
                    "status": [result.status] * len(result.worker),
                },
                _SCHEMA,
            )
        out.finish()
