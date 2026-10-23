"""The ``ortools`` catalog: schema, function registry, and catalog/schema metadata.

Declares the single-schema (`main`) ``ortools`` catalog assembled from every
solver / introspection function. The catalog metadata states explicitly that
this worker needs **no secret provider** and exposes **no externalized
scan-state** (each table-function call is a self-contained, offline solve), so
the proxy's capability check is satisfied without prompting for a credential or
trying to serialize a cursor.
"""

from __future__ import annotations

import json

import pyarrow as pa
from vgi.catalog import Catalog, Schema, Table

from . import DIALECT_VERSION, __version__
from .functions import ALL_FUNCTIONS
from .functions.info_fn import INFO_SCHEMA, STATS_SCHEMA, LastSolveStats, SolverInfo

SOURCE_URL = "https://github.com/Query-farm/vgi-ortools"


def _column_comments(schema: pa.Schema) -> dict[str, str]:
    """Extract ``{column: comment}`` from an Arrow schema's field metadata."""
    out: dict[str, str] = {}
    for f in schema:
        meta = f.metadata or {}
        if b"comment" in meta:
            out[f.name] = meta[b"comment"].decode("utf-8")
    return out


_CATALOG_DESCRIPTION_LLM = (
    "Prescriptive optimization and constraint solving inside DuckDB, powered by Google OR-Tools. This is "
    "the decision-making complement to the predictive ML workers (predict / xgboost / lightgbm): instead "
    "of forecasting what will happen, it computes what you should *do* -- the lowest-cost assignment, the "
    "shortest set of routes, a feasible schedule, the maximum throughput of a network, or the most "
    "valuable way to pack a container.\n\n"
    "Reach for this catalog when a question is a decision or feasibility problem rather than an "
    "aggregation: matching supply to demand at least cost, sequencing jobs on machines under precedence "
    "and resource limits, routing a fleet from a depot under capacity and time-window constraints, or "
    "any hand-built constraint model that mixes global constraints (all-different, no-overlap, "
    "cumulative, circuit) that LP/MIP solvers such as the community `highs` extension cannot express.\n\n"
    "Key concepts. Problems are passed entirely as scalar arguments -- integer matrices and vectors "
    "built by `array_agg` (OR-Tools is an integer engine, so scale fractional data first), or, for the "
    "general constraint solver, a versioned JSON model. Build the cost, distance, or demand matrices "
    "using ordinary SQL joins, solve next to the data, and join the solution rows straight back into "
    "your warehouse -- no separate optimization microservice, data copy, or ETL round-trip. Every solver "
    "reports a `status`; a problem with no solution returns a single status row (test for the string "
    "INFEASIBLE, never an empty result), never an empty set.\n\n"
    "The worker makes no outbound network calls -- pure local CPU, no API keys, no data egress. Your "
    "problem instances, which encode your customers and depots, never leave the box."
)

_CATALOG_DESCRIPTION_MD = (
    "# Optimization & Constraint Solving in SQL (Google OR-Tools)\n\n"
    "**Prescriptive compute inside DuckDB: constraint programming, vehicle routing, scheduling, "
    "assignment, network flow, and packing as plain SQL functions.**\n\n"
    "This catalog turns [Google OR-Tools](https://developers.google.com/optimization) into DuckDB table "
    "and scalar functions, so an operations, logistics, or planning team that already lives in DuckDB "
    "can solve optimization problems next to their data instead of standing up a separate Python "
    "optimization microservice with its own deploy, data copy, and ETL round-trip.\n\n"
    "## When to reach for it\n\n"
    "Use it when a question is a *decision* problem rather than an aggregation -- matching supply to "
    "demand at least cost, sequencing work under precedence and resource limits, routing a fleet under "
    "capacity and time windows, maximizing the throughput of a network, or any hand-built constraint "
    "model mixing the global constraints (all-different, no-overlap, cumulative, circuit) that LP/MIP "
    "solvers such as the community `highs` extension stop short of. It is the decision-making complement "
    "to the predictive ML workers.\n\n"
    "## How it works\n\n"
    "Build the cost, distance, demand, or capacity matrices using ordinary SQL joins and `array_agg`, "
    "hand them to a solver as integer list arguments (OR-Tools is an integer engine -- scale fractional "
    "data first), and get the optimal or best-found solution back as rows you can join straight back "
    "into the warehouse. Every solver returns a `status` column, and a problem with no solution returns "
    "exactly one status row rather than an empty result.\n\n"
    "## Data residency\n\n"
    "The worker makes **no outbound network calls** -- pure local CPU, no API keys, no rate limits, no "
    "data egress. Your problem instances never leave the box. OR-Tools is Apache-2.0; the worker is MIT."
)

_CATALOG_KEYWORDS = [
    "optimization",
    "constraint programming",
    "cp-sat",
    "or-tools",
    "vehicle routing",
    "vrp",
    "tsp",
    "scheduling",
    "job shop",
    "assignment",
    "linear assignment",
    "min cost flow",
    "max flow",
    "knapsack",
    "bin packing",
    "operations research",
    "prescriptive analytics",
    "duckdb",
]

_SCHEMA_EXAMPLE_QUERIES = json.dumps(
    [
        {
            "description": (
                "Least-cost assignment of three workers to three tasks (returns each "
                "worker's chosen task and its cost)."
            ),
            "sql": (
                "SELECT worker, task, cost FROM ortools.main.solve_assignment("
                "cost := [[0,0,90],[0,1,76],[0,2,75],[1,0,35],[1,1,85],[1,2,55],"
                "[2,0,125],[2,1,95],[2,2,90]]::BIGINT[][], "
                "maximize := false) ORDER BY worker"
            ),
        },
        {
            "description": "Optimal travelling-salesperson tour over a 5-node distance matrix, in visit order.",
            "sql": (
                "SELECT seq, node, arrival FROM ortools.main.solve_tsp("
                "distance := [[0,2,9,10,1],[1,0,6,4,8],[15,7,0,8,3],[6,3,12,0,5],[9,8,4,7,0]]::BIGINT[][], "
                "start := 0) ORDER BY seq"
            ),
        },
        {
            "description": "List the bundled OR-Tools / solver component versions.",
            "sql": "SELECT component, version FROM ortools.main.solver_info ORDER BY component",
        },
    ]
)

# Fixed analyst-suitability suite (`vgi-lint simulate` / VGI152 / VGI920). Each
# task has a single unambiguous numeric answer reachable through one solver call
# plus a simple aggregate, so strict reference grading is sound. Only each task's
# `name` and `prompt` are published here (VGI416); the grader-only `reference_sql`
# (deterministic: single-threaded solves with a fixed seed) and the
# `ignore_column_names` option live in `vgi-agent-tests.yaml` at the repo root,
# merged by task name.
_AGENT_TEST_TASKS = [
    {
        "name": "assignment_min_cost",
        "prompt": (
            "Three workers must each be assigned to a distinct task. The cost of a (worker, task) pair "
            "is given by these [worker, task, cost] arcs: "
            "[[0,0,90],[0,1,76],[0,2,75],[1,0,35],[1,1,85],[1,2,55],[2,0,125],[2,1,95],[2,2,90]]. "
            "Assign every worker to exactly one task so the total cost is as small as possible, and "
            "return that minimum total cost as a single number."
        ),
    },
    {
        "name": "max_flow_source_to_sink",
        "prompt": (
            "A directed network has [from, to, capacity] arcs "
            "[[0,1,20],[0,2,30],[1,2,10],[1,3,10],[2,3,20]]. What is the maximum flow that can be routed "
            "from node 0 to node 3? Return a single number."
        ),
    },
    {
        "name": "min_cost_flow_total",
        "prompt": (
            "Route 20 units from node 0 to node 4 over a network whose [from, to, capacity, unit_cost] "
            "arcs are [[0,1,15,4],[0,2,8,4],[1,2,20,2],[1,3,4,2],[2,3,15,1],[2,4,4,3],[3,4,20,2],"
            "[4,2,5,3]], with node supplies [[0,20],[4,-20]], minimizing total cost. Return the minimum "
            "total cost as a single number."
        ),
    },
    {
        "name": "jobshop_makespan",
        "prompt": (
            "Schedule a job shop whose [job, task, machine, duration] operations are "
            "[[0,0,0,3],[0,1,1,2],[1,0,1,2],[1,1,0,4]], minimizing the makespan (the finish time of the "
            "last operation). Return the optimal makespan as a single number."
        ),
    },
    {
        "name": "bin_packing_fewest_bins",
        "prompt": (
            "Pack items of sizes [3,5,2,7,1,4] into bins that each hold at most 10. What is the fewest "
            "number of bins needed? Return a single number."
        ),
    },
    {
        "name": "tsp_tour_nodes",
        "prompt": (
            "A salesperson must visit every location exactly once and return to location 0, over the "
            "distance matrix [[0,2,9,10,1],[1,0,6,4,8],[15,7,0,8,3],[6,3,12,0,5],[9,8,4,7,0]]. How many "
            "distinct locations does the computed optimal tour visit? Return a single number."
        ),
    },
    {
        "name": "cpsat_items_selected",
        "prompt": (
            "Solve this CP-SAT model (booleans a, b, c) that maximizes 10*a + 20*b + 30*c subject to "
            "5*a + 10*b + 15*c <= 20: "
            '\'{"version":1,"variables":[{"kind":"bool","name":"a"},{"kind":"bool","name":"b"},'
            '{"kind":"bool","name":"c"}],"constraints":[{"type":"linear","expr":{"sum":[{"coef":5,'
            '"var":"a"},{"coef":10,"var":"b"},{"coef":15,"var":"c"}]},"op":"<=","rhs":20}],'
            '"objective":{"sense":"maximize","terms":[{"coef":10,"var":"a"},{"coef":20,"var":"b"},'
            '{"coef":30,"var":"c"}]}}\'. In the optimal solution, how many of the three booleans are '
            "set to 1? Return a single number."
        ),
    },
    {
        "name": "vrp_vehicles_used",
        "prompt": (
            "A depot (node 0) must serve three stops (nodes 1, 2, 3), each with demand 1, using up to 2 "
            "vehicles of capacity 2. The [from, to, distance] arcs are "
            "[[0,1,1],[0,2,1],[0,3,2],[1,0,1],[1,2,1],[1,3,2],[2,0,1],[2,1,1],[2,3,1],[3,0,2],[3,1,2],[3,2,1]] "
            "and the [node, demand] pairs are [[1,1],[2,1],[3,1]]. In the optimal routing, how many "
            "vehicles are actually used? Return a single number."
        ),
    },
    {
        "name": "rcpsp_makespan",
        "prompt": (
            "Schedule a project of four tasks given as [task, duration, demand] rows "
            "[[0,3,2],[1,2,1],[2,4,2],[3,2,1]], with finish-to-start precedences [[0,2],[1,3]] and a "
            "single renewable resource of capacity 2 ([[0,2]]), minimizing the makespan. Return the "
            "optimal makespan as a single number."
        ),
    },
    {
        "name": "max_flow_arc_rows",
        "prompt": (
            "A directed network has [from, to, capacity] arcs "
            "[[0,1,20],[0,2,30],[1,2,10],[1,3,10],[2,3,20]]. Using the per-arc maximum-flow solver, "
            "compute the total flow that leaves the source node 0 when routing as much as possible from "
            "node 0 to node 3. Return a single number."
        ),
    },
    {
        "name": "knapsack_items_chosen",
        "prompt": (
            "You have five items with values [10,20,30,40,50] and single-dimension weights "
            "[[5],[10],[15],[20],[25]], and a knapsack of capacity [40]. Choosing items to maximize "
            "total value without exceeding capacity, how many items are packed? Return a single number."
        ),
    },
    {
        "name": "bin_packing_bins_rollup",
        "prompt": (
            "Pack items of sizes [3,5,2,7,1,4] into bins of capacity 10. Using the per-bin rollup, how "
            "many bins end up holding at least one item? Return a single number."
        ),
    },
    {
        "name": "solver_info_ortools_version",
        "prompt": (
            "Which version of Google OR-Tools does this worker bundle? Find the version string it "
            "reports for its OR-Tools engine."
        ),
    },
    {
        "name": "last_solve_stats_reports_status",
        "prompt": (
            "Run any solve (for example a small assignment), then inspect the statistics of the most "
            "recent solve this worker handled and report that solve's status."
        ),
    },
]

_CATALOG_TAGS = {
    "vgi.title": "Optimization & Constraint Solving (OR-Tools)",
    "vgi.keywords": json.dumps(_CATALOG_KEYWORDS),
    "vgi.doc_llm": _CATALOG_DESCRIPTION_LLM,
    "vgi.doc_md": _CATALOG_DESCRIPTION_MD,
    "vgi.agent_test_tasks": json.dumps(_AGENT_TEST_TASKS),
    "vgi.source_url": SOURCE_URL,
    "vgi.author": "Query.Farm",
    "vgi.copyright": "Copyright 2026 Query Farm LLC - https://query.farm",
    "vgi.license": "MIT",
    "vgi.support_contact": "https://github.com/Query-farm/vgi-ortools/issues",
    "vgi.support_policy_url": "https://github.com/Query-farm/vgi-ortools/blob/main/README.md",
}

_SCHEMA_CATEGORIES = [
    {
        "name": "Constraint Programming",
        "description": "The general CP-SAT constraint solver, driven by a versioned JSON model.",
    },
    {
        "name": "Routing",
        "description": "Shortest tours and capacitated, time-windowed vehicle routing.",
    },
    {
        "name": "Assignment",
        "description": "Least-cost matching of workers to tasks (linear sum assignment).",
    },
    {
        "name": "Scheduling",
        "description": "Job-shop and resource-constrained project scheduling that minimize makespan.",
    },
    {
        "name": "Network Flow",
        "description": "Minimum-cost flow and maximum flow over capacitated networks.",
    },
    {
        "name": "Packing",
        "description": "Knapsack value maximization and bin packing into the fewest bins.",
    },
    {
        "name": "Introspection",
        "description": "Solver / worker versions and the statistics of the most recent solve.",
    },
]

_SCHEMA_TAGS = {
    "vgi.title": "OR-Tools Solvers",
    "vgi.keywords": json.dumps(_CATALOG_KEYWORDS),
    "vgi.categories": json.dumps(_SCHEMA_CATEGORIES),
    "vgi.doc_llm": (
        "Optimization and constraint-solving table and scalar functions backed by Google OR-Tools, "
        "organized into constraint programming, routing, assignment, scheduling, network flow, packing, "
        "and introspection categories.\n\n"
        "Problems are passed as scalar integer list arguments built by `array_agg`, or -- for the "
        "general constraint solver -- as a versioned JSON model. Every solver returns a `status` column, "
        "and an unsolved problem yields exactly one status row rather than an empty result."
    ),
    "vgi.doc_md": (
        "## OR-Tools Solvers\n\n"
        "Prescriptive optimization and constraint-solving functions backed by Google OR-Tools, "
        "covering:\n\n"
        "- **Constraint programming** -- a general CP-SAT model expressed as versioned JSON\n"
        "- **Routing** -- shortest tours and capacitated, time-windowed vehicle routing\n"
        "- **Assignment** -- least-cost matching of workers to tasks\n"
        "- **Scheduling** -- job-shop and resource-constrained project scheduling\n"
        "- **Network flow** -- minimum-cost and maximum flow\n"
        "- **Packing** -- knapsack and bin packing\n"
        "- **Introspection** -- solver versions and last-solve statistics\n\n"
        "Every solver takes its problem as scalar integer list arguments (or a JSON model) and returns "
        "rows carrying a `status` column; a problem with no solution yields a single status row, never "
        "an empty result."
    ),
    # source_url lives only on the catalog object (VGI139); not duplicated here.
    "vgi.example_queries": _SCHEMA_EXAMPLE_QUERIES,
    # VGI123 classifying tags use BARE keys (NOT vgi.-namespaced).
    "domain": "operations-research",
    "category": "optimization",
    "topic": "constraint-solving-and-routing",
}

# ---------------------------------------------------------------------------
# Introspection base TABLES (browsable, credential-free scans).
#
# `solver_info` and `last_solve_stats` are exposed as base tables backed by a
# zero-argument scan generator (VGI311: a parameterless table function should be
# a table; VGI146: a worker needs at least one browsable relation). An agent can
# `SELECT * FROM ortools.main.solver_info` to discover the bundled solver
# versions, or scan `last_solve_stats` for the metrics of the most recent solve
# on this worker process -- both scan with no arguments, no network, no secret.
# ---------------------------------------------------------------------------
_SOLVER_INFO_TABLE_TAGS = {
    "vgi.title": "Solver / Worker Versions",
    "vgi.category": "Introspection",
    "vgi.keywords": json.dumps(["solver info", "version", "ortools version", "diagnostics", "introspection", "cp-sat"]),
    "vgi.doc_llm": (
        "The bundled solver and worker versions as `(component, version)` rows: `ortools`, `cp_sat`, "
        "`glop`, `pdlp`, the `worker` build version, and the `dialect` version of the `solve_cpsat` JSON "
        "model. Scan it directly (no arguments) for diagnostics or compatibility checks -- e.g. to "
        "confirm which OR-Tools release backs a result before relying on version-specific behaviour."
    ),
    "vgi.doc_md": (
        "## solver_info\n\n"
        "A browsable table of the bundled component versions, one row per component: `ortools`, "
        "`cp_sat`, `glop`, `pdlp`, the `worker` build version, and the `dialect` version of the "
        "`solve_cpsat` JSON model. It takes no arguments -- scan it and filter with `WHERE`.\n\n"
        "**Reference:** [OR-Tools documentation](https://developers.google.com/optimization)"
    ),
    "vgi.executable_examples": json.dumps(
        [
            {
                "description": "List the bundled solver and worker versions.",
                "sql": "SELECT component, version FROM ortools.main.solver_info ORDER BY component",
            },
            {
                "description": "Look up just the OR-Tools engine version.",
                "sql": "SELECT version FROM ortools.main.solver_info WHERE component = 'ortools'",
            },
        ]
    ),
    "vgi.example_queries": json.dumps(
        [
            {
                "description": "List every bundled component and its version.",
                "sql": "SELECT component, version FROM ortools.main.solver_info ORDER BY component",
            },
            {
                "description": "Fetch just the OR-Tools engine version.",
                "sql": "SELECT version FROM ortools.main.solver_info WHERE component = 'ortools'",
            },
        ]
    ),
    "domain": "operations-research",
}

_LAST_SOLVE_STATS_TABLE_TAGS = {
    "vgi.title": "Last Solve Statistics",
    "vgi.category": "Introspection",
    "vgi.keywords": json.dumps(
        ["last solve", "solve stats", "diagnostics", "wall time", "num branches", "introspection"]
    ),
    "vgi.doc_llm": (
        "The statistics of the **most recent solve** handled by this worker process, as `(metric, value)` "
        "rows: `status`, `objective`, `best_bound`, `wall_time`, `num_branches`, `num_conflicts`, "
        "`num_booleans`, `deterministic_time`. A convenience mirror of the solve metadata; every `value` "
        "is NULL until the first solve runs. This is per-process, not strictly per-connection -- the "
        "authoritative, race-free source is the metadata columns on each solver's own result rows."
    ),
    "vgi.doc_md": (
        "## last_solve_stats\n\n"
        "A browsable table of the most recent solve's metrics, one row per metric: `status`, "
        "`objective`, `best_bound`, `wall_time`, `num_branches`, `num_conflicts`, `num_booleans`, "
        "`deterministic_time`. Scan it after a solve; every `value` is NULL until the first solve runs. "
        "It reflects the last solve on this worker *process*, so for a race-free per-query value prefer "
        "the metadata columns on the solver's own result rows.\n\n"
        "**Reference:** [OR-Tools documentation](https://developers.google.com/optimization/cp/cp_solver)"
    ),
    "vgi.executable_examples": json.dumps(
        [
            {
                "description": "Run a solve, then read back the statistics of that solve.",
                "sql": [
                    "SELECT count(*) FROM ortools.main.solve_assignment("
                    "cost := [[0,0,1],[1,1,1]]::BIGINT[][], maximize := false)",
                    "SELECT metric, value FROM ortools.main.last_solve_stats ORDER BY metric",
                ],
            }
        ]
    ),
    "vgi.example_queries": json.dumps(
        [
            {
                "description": "Read every metric of the most recent solve on this worker.",
                "sql": "SELECT metric, value FROM ortools.main.last_solve_stats ORDER BY metric",
            },
            {
                "description": "Fetch just the status of the most recent solve.",
                "sql": "SELECT value FROM ortools.main.last_solve_stats WHERE metric = 'status'",
            },
        ]
    ),
    "domain": "operations-research",
}

ORTOOLS_CATALOG = Catalog(
    name="ortools",
    default_schema="main",
    comment="Google OR-Tools optimization / constraint solving as SQL functions.",
    tags=_CATALOG_TAGS,
    schemas=[
        Schema(
            path=["main"],
            comment="OR-Tools solvers: CP-SAT, routing, assignment, scheduling, flow, packing.",
            tags=_SCHEMA_TAGS,
            functions=list(ALL_FUNCTIONS),
            tables=[
                Table(
                    name="solver_info",
                    function=SolverInfo,
                    comment="Bundled OR-Tools / worker / dialect versions, one row per component (no arguments).",
                    column_comments=_column_comments(INFO_SCHEMA),
                    # One row per component name; both columns are always populated.
                    not_null=("component", "version"),
                    primary_key=(("component",),),
                    cardinality_estimate=6,
                    cardinality_max=64,
                    tags=_SOLVER_INFO_TABLE_TAGS,
                ),
                Table(
                    name="last_solve_stats",
                    function=LastSolveStats,
                    comment=(
                        "Statistics of the most recent solve on this worker process, one row per metric "
                        "(NULL values until the first solve)."
                    ),
                    column_comments=_column_comments(STATS_SCHEMA),
                    # One row per metric name (the value may be NULL until the first solve).
                    not_null=("metric",),
                    primary_key=(("metric",),),
                    cardinality_estimate=8,
                    cardinality_max=64,
                    tags=_LAST_SOLVE_STATS_TABLE_TAGS,
                ),
            ],
        ),
    ],
)

# Surfaced on ATTACH so clients can express compatibility with the data contract
# (the result-row / JSON-dialect shape) served by this catalog.
DATA_VERSION = __version__
DATA_VERSION_SPEC = ">=0.1.0,<0.2.0"
DIALECT = DIALECT_VERSION
