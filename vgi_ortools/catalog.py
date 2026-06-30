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
    "Solve optimization and constraint-satisfaction problems inside DuckDB with Google OR-Tools, as "
    "SQL table/scalar functions in the `ortools` catalog. This is prescriptive compute (decide what to "
    "do) -- the complement to the predictive ML workers (predict / xgboost / lightgbm). It exposes the "
    "CP-SAT constraint solver via a versioned JSON model dialect (`solve_cpsat`), the routing library "
    "(`solve_tsp`, `solve_vrp` with capacity / time-window / pickup-delivery / dropping), linear "
    "assignment (`solve_assignment`), scheduling (`solve_jobshop`, `solve_rcpsp`), min-cost / max-flow "
    "(`min_cost_flow`, `max_flow`, scalar `max_flow_value`), and bin-packing / knapsack "
    "(`solve_bin_packing`, `solve_knapsack`). Build cost / distance / demand matrices with ordinary SQL "
    "joins and `array_agg([...])`, hand them to a solver, and get the optimal (or best-found) solution "
    "back as rows you can join straight back into your warehouse. Every solver returns a `status` "
    "column; a no-solution model returns exactly one status row, never an empty set. All values are "
    "integers (OR-Tools is an integer engine). The worker makes no outbound network calls -- pure local "
    "CPU compute, no API keys, no data egress. It owns the constraint-programming / routing / scheduling "
    "capability the `highs` community extension (LP/MIP) cannot express."
)

_CATALOG_DESCRIPTION_MD = (
    "# Optimization & Constraint Solving in SQL (Google OR-Tools)\n\n"
    "**Prescriptive compute inside DuckDB: CP-SAT constraint programming, vehicle routing, scheduling, "
    "assignment, and network flow as plain SQL functions.**\n\n"
    "The `ortools` catalog turns [Google OR-Tools](https://developers.google.com/optimization) into a "
    "set of DuckDB table and scalar functions, so an ops / logistics / planning team that already lives "
    "in DuckDB can write `SELECT * FROM ortools.solve_vrp(...)` instead of standing up a separate Python "
    "optimization microservice with its own deploy, data copy, and ETL round-trip. Build the "
    "cost / distance / capacity matrices with ordinary SQL joins, solve next to the data, and join the "
    "result straight back into the warehouse.\n\n"
    "It is the complement to the predictive ML workers (`predict` / `xgboost` / `lightgbm`, which say "
    "what will happen) and the in-engine capability the `highs` community extension stops short of: "
    "CP-SAT constraint programming, the routing library (TSP / VRP with capacity, time windows, "
    "pickup-delivery), scheduling (job-shop, RCPSP), and the global constraints (`all_different`, "
    "`no_overlap`, `cumulative`, `circuit`) that LP/MIP cannot express. The general escape hatch is "
    "`solve_cpsat`, a stable versioned JSON dialect that deserializes one-to-one onto an OR-Tools "
    "`CpModel`.\n\n"
    "Data residency: this worker makes **no outbound network calls** -- pure local CPU, no API keys, no "
    "rate limits, no data egress. Your routing problem (which encodes your customers and depots) never "
    "leaves the box. OR-Tools is Apache-2.0; the worker is MIT."
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

_CATALOG_TAGS = {
    "vgi.title": "Optimization & Constraint Solving (OR-Tools)",
    "vgi.keywords": json.dumps(_CATALOG_KEYWORDS),
    "vgi.doc_llm": _CATALOG_DESCRIPTION_LLM,
    "vgi.doc_md": _CATALOG_DESCRIPTION_MD,
    "vgi.source_url": SOURCE_URL,
    "vgi.author": "Query.Farm",
    "vgi.copyright": "Copyright 2026 Query Farm LLC - https://query.farm",
    "vgi.license": "MIT",
    "vgi.support_contact": "https://github.com/Query-farm/vgi-ortools/issues",
    "vgi.support_policy_url": "https://github.com/Query-farm/vgi-ortools/blob/main/README.md",
    # Offline compute: no credentials, no resumable cursor.
    "vgi.requires_secret": "false",
    "vgi.externalized_scan_state": "false",
}

_SCHEMA_TAGS = {
    "vgi.title": "OR-Tools Solvers",
    "vgi.keywords": json.dumps(_CATALOG_KEYWORDS),
    "vgi.doc_llm": (
        "Optimization / constraint-solving table & scalar functions backed by Google OR-Tools: "
        "solve_cpsat (CP-SAT JSON model), solve_assignment, solve_tsp, solve_vrp, solve_jobshop, "
        "solve_rcpsp, min_cost_flow, max_flow, max_flow_value, solve_knapsack, solve_bin_packing, "
        "solver_info, last_solve_stats. Every solver returns a status column."
    ),
    "vgi.doc_md": (
        "OR-Tools optimization functions: CP-SAT (`solve_cpsat`), routing (`solve_tsp`, `solve_vrp`), "
        "assignment, scheduling, flow, and packing. Each returns a `status` column."
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
