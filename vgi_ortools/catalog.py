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

from vgi.catalog import Catalog, Schema

from . import DIALECT_VERSION, __version__
from .functions import ALL_FUNCTIONS

SOURCE_URL = "https://github.com/Query-farm/vgi-ortools"

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

_SCHEMA_EXAMPLE_QUERIES = (
    "SELECT worker, task, cost FROM ortools.solve_assignment("
    "cost := [[0,0,90],[0,1,76],[0,2,75],[1,0,35],[1,1,85],[1,2,55],[2,0,125],[2,1,95],[2,2,90]]::BIGINT[][], "
    "maximize := false) ORDER BY worker;\n"
    "SELECT seq, node, arrival FROM ortools.solve_tsp("
    "distance := [[0,2,9,10,1],[1,0,6,4,8],[15,7,0,8,3],[6,3,12,0,5],[9,8,4,7,0]]::BIGINT[][], start := 0) "
    "ORDER BY seq;\n"
    "SELECT component, version FROM ortools.solver_info() ORDER BY component;"
)

# Fixed analyst-suitability suite (`vgi-lint simulate` / VGI152 / VGI920). Each
# task has a single unambiguous numeric answer reachable through one solver call
# plus a simple aggregate, so strict reference grading is sound; `reference_sql`
# is grader-only (never shown to the analyst) and deterministic (single-threaded
# solves with a fixed seed). Column names are aliased freely by the analyst, so
# every task grades with `ignore_column_names`.
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
        "reference_sql": (
            "SELECT sum(cost) AS total_cost FROM ortools.solve_assignment("
            "cost := [[0,0,90],[0,1,76],[0,2,75],[1,0,35],[1,1,85],[1,2,55],"
            "[2,0,125],[2,1,95],[2,2,90]]::BIGINT[][], maximize := false)"
        ),
        "ignore_column_names": True,
    },
    {
        "name": "max_flow_source_to_sink",
        "prompt": (
            "A directed network has [from, to, capacity] arcs "
            "[[0,1,20],[0,2,30],[1,2,10],[1,3,10],[2,3,20]]. What is the maximum flow that can be routed "
            "from node 0 to node 3? Return a single number."
        ),
        "reference_sql": (
            "SELECT ortools.max_flow_value("
            "[[0,1,20],[0,2,30],[1,2,10],[1,3,10],[2,3,20]]::BIGINT[][], 0, 3) AS max_flow"
        ),
        "ignore_column_names": True,
    },
    {
        "name": "min_cost_flow_total",
        "prompt": (
            "Route 20 units from node 0 to node 4 over a network whose [from, to, capacity, unit_cost] "
            "arcs are [[0,1,15,4],[0,2,8,4],[1,2,20,2],[1,3,4,2],[2,3,15,1],[2,4,4,3],[3,4,20,2],"
            "[4,2,5,3]], with node supplies [[0,20],[4,-20]], minimizing total cost. Return the minimum "
            "total cost as a single number."
        ),
        "reference_sql": (
            "SELECT sum(cost) AS total_cost FROM ortools.min_cost_flow("
            "arcs := [[0,1,15,4],[0,2,8,4],[1,2,20,2],[1,3,4,2],[2,3,15,1],[2,4,4,3],[3,4,20,2],"
            "[4,2,5,3]]::BIGINT[][], supplies := [[0,20],[4,-20]]::BIGINT[][])"
        ),
        "ignore_column_names": True,
    },
    {
        "name": "jobshop_makespan",
        "prompt": (
            "Schedule a job shop whose [job, task, machine, duration] operations are "
            "[[0,0,0,3],[0,1,1,2],[1,0,1,2],[1,1,0,4]], minimizing the makespan (the finish time of the "
            "last operation). Return the optimal makespan as a single number."
        ),
        "reference_sql": (
            'SELECT max("end") AS makespan FROM ortools.solve_jobshop('
            "tasks := [[0,0,0,3],[0,1,1,2],[1,0,1,2],[1,1,0,4]]::BIGINT[][])"
        ),
        "ignore_column_names": True,
    },
    {
        "name": "bin_packing_fewest_bins",
        "prompt": (
            "Pack items of sizes [3,5,2,7,1,4] into bins that each hold at most 10. What is the fewest "
            "number of bins needed? Return a single number."
        ),
        "reference_sql": (
            "SELECT count(DISTINCT bin) AS bins FROM ortools.solve_bin_packing("
            "sizes := [3,5,2,7,1,4]::BIGINT[], bin_capacity := 10)"
        ),
        "ignore_column_names": True,
    },
    {
        "name": "tsp_tour_nodes",
        "prompt": (
            "A salesperson must visit every location exactly once and return to location 0, over the "
            "distance matrix [[0,2,9,10,1],[1,0,6,4,8],[15,7,0,8,3],[6,3,12,0,5],[9,8,4,7,0]]. How many "
            "distinct locations does the computed optimal tour visit? Return a single number."
        ),
        "reference_sql": (
            "SELECT count(DISTINCT node) AS visited FROM ortools.solve_tsp("
            "distance := [[0,2,9,10,1],[1,0,6,4,8],[15,7,0,8,3],[6,3,12,0,5],[9,8,4,7,0]]::BIGINT[][], "
            "start := 0)"
        ),
        "ignore_column_names": True,
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
        "and an unsolved problem yields exactly one status row rather than an empty result. Browse the "
        "categories or list the schema to find the specific solver for a problem."
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

ORTOOLS_CATALOG = Catalog(
    name="ortools",
    default_schema="main",
    comment="Google OR-Tools optimization / constraint solving as SQL functions.",
    tags=_CATALOG_TAGS,
    schemas=[
        Schema(
            name="main",
            comment="OR-Tools solvers: CP-SAT, routing, assignment, scheduling, flow, packing.",
            tags=_SCHEMA_TAGS,
            functions=list(ALL_FUNCTIONS),
        ),
    ],
)

# Surfaced on ATTACH so clients can express compatibility with the data contract
# (the result-row / JSON-dialect shape) served by this catalog.
DATA_VERSION = __version__
DATA_VERSION_SPEC = ">=0.1.0,<0.2.0"
DIALECT = DIALECT_VERSION
