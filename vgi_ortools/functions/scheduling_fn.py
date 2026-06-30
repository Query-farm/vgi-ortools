"""Scheduling functions: ``solve_jobshop`` and ``solve_rcpsp`` (CP-SAT builders)."""

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
from ..limits import Limits
from ..models import scheduling
from ..schema_utils import field as sfield
from ._meta import LIST_LIST_INT, function_tags

_JOBSHOP_URL = "https://developers.google.com/optimization/scheduling/job_shop"
_RCPSP_URL = "https://developers.google.com/optimization/scheduling/job_shop"

_JOBSHOP_SCHEMA = pa.schema(
    [
        sfield("job", pa.int64(), "Job id.", nullable=False),
        sfield("task", pa.int64(), "Task index within the job."),
        sfield("machine", pa.int64(), "Machine the task runs on."),
        sfield("start", pa.int64(), "Scheduled start time."),
        sfield("end", pa.int64(), "Scheduled end time (start + duration)."),
        sfield("makespan", pa.int64(), "Optimal makespan (repeated on every row)."),
        sfield("status", pa.string(), "OPTIMAL / FEASIBLE / INFEASIBLE / UNKNOWN.", nullable=False),
    ]
)
_RCPSP_SCHEMA = pa.schema(
    [
        sfield("task", pa.int64(), "Task id.", nullable=False),
        sfield("start", pa.int64(), "Scheduled start time."),
        sfield("end", pa.int64(), "Scheduled end time (start + duration)."),
        sfield("makespan", pa.int64(), "Optimal makespan (repeated on every row)."),
        sfield("status", pa.string(), "OPTIMAL / FEASIBLE / INFEASIBLE / UNKNOWN.", nullable=False),
    ]
)

_JOBSHOP_EXAMPLES = [
    {
        "description": "Two-job, two-machine job shop minimizing makespan (optimum 7).",
        "sql": (
            'SELECT job, task, machine, start, "end", makespan FROM ortools.solve_jobshop('
            "tasks := [[0,0,0,3],[0,1,1,2],[1,0,1,2],[1,1,0,4]]::BIGINT[][], time_limit_s := 5.0) "
            "ORDER BY job, task"
        ),
    }
]
_RCPSP_EXAMPLES = [
    {
        "description": "Four-task project, one resource of capacity 2, with two precedences.",
        "sql": (
            'SELECT task, start, "end", makespan, status FROM ortools.solve_rcpsp('
            "tasks := [[0,3,2],[1,2,1],[2,4,2],[3,2,1]]::BIGINT[][], "
            "precedences := [[0,2],[1,3]]::BIGINT[][], resources := [[0,2]]::BIGINT[][], time_limit_s := 5.0) "
            "ORDER BY task"
        ),
    }
]


@dataclass(slots=True, frozen=True)
class SolveJobshopArgs:
    """Arguments for ``solve_jobshop``."""

    tasks: Annotated[
        list[Any] | None,
        Arg("tasks", default=None, arrow_type=LIST_LIST_INT, doc="[job, task, machine, duration] integer rows."),
    ]
    horizon: Annotated[int, Arg("horizon", default=0, doc="Scheduling horizon upper bound (0 = sum of durations).")]
    time_limit_s: Annotated[float, Arg("time_limit_s", default=30.0, doc="Wall-clock cap in seconds.")]
    random_seed: Annotated[int, Arg("random_seed", default=1, doc="Random seed for a deterministic makespan.")]


@init_single_worker
@bind_fixed_schema
class SolveJobshop(TableFunctionGenerator[SolveJobshopArgs]):
    """Job-shop scheduling minimizing makespan."""

    FunctionArguments: ClassVar[type] = SolveJobshopArgs
    FIXED_SCHEMA: ClassVar[pa.Schema] = _JOBSHOP_SCHEMA

    class Meta:
        """SQL-facing metadata for ``solve_jobshop``."""

        name = "solve_jobshop"
        description = (
            "Job-shop scheduling: schedule the tasks of several jobs on shared machines (tasks of a "
            "job run in order, one task per machine at a time) to minimize the makespan. CP-SAT."
        )
        categories = ["optimization", "scheduling", "cp-sat"]
        keywords = [
            "job shop",
            "jobshop scheduling",
            "makespan",
            "machine scheduling",
            "no overlap",
            "production scheduling",
            "or-tools",
            "cp-sat",
        ]
        tags = function_tags(
            title="Job-Shop Scheduling (Min Makespan)",
            doc_llm=(
                "## `solve_jobshop`\n\n"
                "Schedule the tasks of several **jobs** onto shared **machines** to minimize the "
                "**makespan** (the time the last task finishes). Within a job, tasks run in `task` "
                "order; each machine runs one task at a time.\n\n"
                "**Inputs.** `tasks` = `[job, task, machine, duration]` integer rows. Optional "
                "`horizon` (defaults to the sum of durations), `time_limit_s`, `random_seed`.\n\n"
                "**Outputs.** One row per task with its `start` / `end`, the optimal `makespan` "
                "(repeated on every row), and `status`. An infeasible / unsolved instance returns a "
                "single status row."
            ),
            doc_md=(
                "# solve_jobshop\n\n"
                "Job-shop scheduling minimizing makespan.\n\n"
                "```sql\n"
                'SELECT job, task, machine, start, "end"\n'
                "FROM ortools.solve_jobshop(tasks := (SELECT array_agg([job, task, machine, dur]) FROM ops));\n"
                "```\n"
            ),
            keywords=keywords,
            result_columns_md=(
                "| column | type | description |\n"
                "| --- | --- | --- |\n"
                "| `job` | `BIGINT` | Job id. |\n"
                "| `task` | `BIGINT` | Task index within the job. |\n"
                "| `machine` | `BIGINT` | Machine the task runs on. |\n"
                "| `start` | `BIGINT` | Scheduled start time. |\n"
                "| `end` | `BIGINT` | Scheduled end time. |\n"
                "| `makespan` | `BIGINT` | Optimal makespan (repeated on every row). |\n"
                "| `status` | `VARCHAR` | `OPTIMAL` / `FEASIBLE` / `INFEASIBLE` / `UNKNOWN`. |"
            ),
            source_url=_JOBSHOP_URL,
            executable_examples=_JOBSHOP_EXAMPLES,
        )
        examples = [FunctionExample(sql=_JOBSHOP_EXAMPLES[0]["sql"], description="Two-job job shop, makespan 7.")]

    @classmethod
    def cardinality(cls, params: BindParams[SolveJobshopArgs]) -> TableCardinality:
        """Estimate the output cardinality (one row per task)."""
        return TableCardinality(estimate=16, max=1_000_000)

    @classmethod
    def process(cls, params: ProcessParams[SolveJobshopArgs], state: None, out: OutputCollector) -> None:
        """Solve the job shop and emit per-task rows (or one status row)."""
        a = params.args
        limits = Limits.from_settings(getattr(params, "settings", None))
        result = scheduling.solve_jobshop(
            rows_of(a.tasks, where="tasks"),
            horizon=a.horizon or None,
            time_limit_s=a.time_limit_s,
            random_seed=a.random_seed,
            limits=limits,
        )
        stats.record(status=result.status, objective=float(result.makespan) if result.makespan is not None else None)
        if not result.job:
            emit_one(out, null_row(_JOBSHOP_SCHEMA, status=result.status), _JOBSHOP_SCHEMA)
        else:
            n = len(result.job)
            emit_one(
                out,
                {
                    "job": result.job,
                    "task": result.task,
                    "machine": result.machine,
                    "start": result.start,
                    "end": result.end,
                    "makespan": [result.makespan] * n,
                    "status": [result.status] * n,
                },
                _JOBSHOP_SCHEMA,
            )
        out.finish()


@dataclass(slots=True, frozen=True)
class SolveRcpspArgs:
    """Arguments for ``solve_rcpsp``."""

    tasks: Annotated[
        list[Any] | None,
        Arg(
            "tasks",
            default=None,
            arrow_type=LIST_LIST_INT,
            doc="[task, duration, r0_demand, r1_demand, ...] integer rows (one demand col per resource).",
        ),
    ]
    precedences: Annotated[
        list[Any] | None,
        Arg("precedences", default=None, arrow_type=LIST_LIST_INT, doc="[pred, succ] finish-to-start precedences."),
    ]
    resources: Annotated[
        list[Any] | None,
        Arg("resources", default=None, arrow_type=LIST_LIST_INT, doc="[resource, capacity] rows."),
    ]
    time_limit_s: Annotated[float, Arg("time_limit_s", default=30.0, doc="Wall-clock cap in seconds.")]
    random_seed: Annotated[int, Arg("random_seed", default=1, doc="Random seed for a deterministic makespan.")]


@init_single_worker
@bind_fixed_schema
class SolveRcpsp(TableFunctionGenerator[SolveRcpspArgs]):
    """Resource-constrained project scheduling minimizing makespan."""

    FunctionArguments: ClassVar[type] = SolveRcpspArgs
    FIXED_SCHEMA: ClassVar[pa.Schema] = _RCPSP_SCHEMA

    class Meta:
        """SQL-facing metadata for ``solve_rcpsp``."""

        name = "solve_rcpsp"
        description = (
            "Resource-constrained project scheduling (RCPSP): schedule tasks with durations, "
            "precedences, and per-resource capacity (renewable) to minimize makespan. CP-SAT cumulative."
        )
        categories = ["optimization", "scheduling", "cp-sat"]
        keywords = [
            "rcpsp",
            "project scheduling",
            "resource constrained",
            "cumulative",
            "makespan",
            "precedence",
            "or-tools",
            "cp-sat",
        ]
        tags = function_tags(
            title="Resource-Constrained Project Scheduling",
            doc_llm=(
                "## `solve_rcpsp`\n\n"
                "Solve a **resource-constrained project scheduling problem**: schedule tasks (each with "
                "a duration and a per-resource demand) subject to finish-to-start **precedences** and "
                "renewable **resource capacities**, minimizing the makespan.\n\n"
                "**Inputs.** `tasks` = `[task, duration, r0_demand, r1_demand, ...]` (one demand column "
                "per resource, in `resources` order); `precedences` = `[pred, succ]`; `resources` = "
                "`[resource, capacity]`. **Outputs.** One row per task with `start` / `end`, the optimal "
                "`makespan`, and `status`."
            ),
            doc_md=(
                "# solve_rcpsp\n\n"
                "Resource-constrained project scheduling minimizing makespan.\n\n"
                "```sql\n"
                'SELECT task, start, "end", makespan FROM ortools.solve_rcpsp(\n'
                "  tasks := (SELECT array_agg([t, dur, d0]) FROM project_tasks),\n"
                "  precedences := (SELECT array_agg([p, s]) FROM deps),\n"
                "  resources := [[0, 4]]::BIGINT[][]);\n"
                "```\n"
            ),
            keywords=keywords,
            result_columns_md=(
                "| column | type | description |\n"
                "| --- | --- | --- |\n"
                "| `task` | `BIGINT` | Task id. |\n"
                "| `start` | `BIGINT` | Scheduled start time. |\n"
                "| `end` | `BIGINT` | Scheduled end time. |\n"
                "| `makespan` | `BIGINT` | Optimal makespan (repeated on every row). |\n"
                "| `status` | `VARCHAR` | `OPTIMAL` / `FEASIBLE` / `INFEASIBLE` / `UNKNOWN`. |"
            ),
            source_url=_RCPSP_URL,
            executable_examples=_RCPSP_EXAMPLES,
        )
        examples = [FunctionExample(sql=_RCPSP_EXAMPLES[0]["sql"], description="Four-task RCPSP with one resource.")]

    @classmethod
    def cardinality(cls, params: BindParams[SolveRcpspArgs]) -> TableCardinality:
        """Estimate the output cardinality (one row per task)."""
        return TableCardinality(estimate=16, max=1_000_000)

    @classmethod
    def process(cls, params: ProcessParams[SolveRcpspArgs], state: None, out: OutputCollector) -> None:
        """Solve the RCPSP and emit per-task rows (or one status row)."""
        a = params.args
        limits = Limits.from_settings(getattr(params, "settings", None))
        result = scheduling.solve_rcpsp(
            rows_of(a.tasks, where="tasks"),
            rows_of(a.precedences, where="precedences"),
            rows_of(a.resources, where="resources"),
            time_limit_s=a.time_limit_s,
            random_seed=a.random_seed,
            limits=limits,
        )
        stats.record(status=result.status, objective=float(result.makespan) if result.makespan is not None else None)
        if not result.task:
            emit_one(out, null_row(_RCPSP_SCHEMA, status=result.status), _RCPSP_SCHEMA)
        else:
            n = len(result.task)
            emit_one(
                out,
                {
                    "task": result.task,
                    "start": result.start,
                    "end": result.end,
                    "makespan": [result.makespan] * n,
                    "status": [result.status] * n,
                },
                _RCPSP_SCHEMA,
            )
        out.finish()
