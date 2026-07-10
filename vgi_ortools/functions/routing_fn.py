"""Routing functions: ``solve_tsp`` (fixed schema) and ``solve_vrp`` (dynamic load columns)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, ClassVar

import pyarrow as pa
from vgi.arguments import Arg
from vgi.invocation import BindResponse
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
from ..arrow_io import as_distance_matrix, emit_one, null_row, rows_of
from ..limits import Limits
from ..models import routing
from ..schema_utils import field as sfield
from ._meta import LIST_LIST_INT, function_tags

_TSP_URL = "https://developers.google.com/optimization/routing/tsp"
_VRP_URL = "https://developers.google.com/optimization/routing/cvrp"

_TSP_SCHEMA = pa.schema(
    [
        sfield("seq", pa.int32(), "0-based position in the tour.", nullable=False),
        sfield("node", pa.int64(), "Node id visited at this position."),
        sfield("arrival", pa.int64(), "Cumulative distance at arrival."),
        sfield("status", pa.string(), "OPTIMAL / INFEASIBLE / UNKNOWN / MODEL_INVALID.", nullable=False),
    ]
)

_TSP_EXAMPLES = [
    {
        "description": "5-node TSP from a dense distance matrix; shortest tour length 17.",
        "sql": (
            "SELECT seq, node, arrival FROM ortools.main.solve_tsp("
            "distance := [[0,2,9,10,1],[1,0,6,4,8],[15,7,0,8,3],[6,3,12,0,5],[9,8,4,7,0]]::BIGINT[][], "
            "start := 0, time_limit_s := 1.0) ORDER BY seq"
        ),
    },
    {
        "description": (
            "Same problem from the sparse `[from, to, meters]` edge-list form (build it with "
            "`array_agg([f, t, m])`) instead of a dense matrix -- here a complete 4-node graph."
        ),
        "sql": (
            "SELECT seq, node, arrival FROM ortools.main.solve_tsp("
            "distance := [[0,1,1],[0,2,4],[0,3,2],[1,0,1],[1,2,1],[1,3,5],"
            "[2,0,4],[2,1,1],[2,3,1],[3,0,2],[3,1,5],[3,2,1]]::BIGINT[][], "
            "start := 0, time_limit_s := 1.0) ORDER BY seq"
        ),
    },
]
_VRP_EXAMPLES = [
    {
        "description": "Capacitated VRP: 1 depot, 3 stops of demand 1, 2 vehicles of capacity 2 (optimal distance 6).",
        "sql": (
            "SELECT route_id, seq, node, arrival, load_0 FROM ortools.main.solve_vrp("
            "distance := [[0,1,1],[0,2,1],[0,3,2],[1,0,1],[1,2,1],[1,3,2],[2,0,1],[2,1,1],[2,3,1],"
            "[3,0,2],[3,1,2],[3,2,1]]::BIGINT[][], "
            "demand := [[1,1],[2,1],[3,1]]::BIGINT[][], num_vehicles := 2, vehicle_capacity := 2, depot := 0, "
            "time_limit_s := 1.0) ORDER BY route_id, seq"
        ),
    }
]


@dataclass(slots=True, frozen=True)
class SolveTspArgs:
    """Arguments for ``solve_tsp``."""

    distance: Annotated[
        list[Any] | None,
        Arg(
            "distance",
            default=None,
            arrow_type=LIST_LIST_INT,
            doc="Distance matrix: sparse [from, to, meters] triples, or a dense n x n matrix.",
        ),
    ]
    start: Annotated[int, Arg("start", default=0, doc="Start (and return) node of the tour.")]
    time_limit_s: Annotated[float, Arg("time_limit_s", default=5.0, doc="Wall-clock cap in seconds.")]
    first_solution_strategy: Annotated[
        str, Arg("first_solution_strategy", default="", doc="OR-Tools first-solution strategy name.")
    ]
    local_search_metaheuristic: Annotated[
        str, Arg("local_search_metaheuristic", default="", doc="OR-Tools local-search metaheuristic name.")
    ]
    random_seed: Annotated[
        int, Arg("random_seed", default=1, doc="Reserved (routing determinism is the fixed strategy).")
    ]


@init_single_worker
@bind_fixed_schema
class SolveTsp(TableFunctionGenerator[SolveTspArgs]):
    """Single-vehicle travelling-salesperson tour."""

    FunctionArguments: ClassVar[type] = SolveTspArgs
    FIXED_SCHEMA: ClassVar[pa.Schema] = _TSP_SCHEMA

    class Meta:
        """SQL-facing metadata for ``solve_tsp``."""

        name = "solve_tsp"
        description = (
            "Travelling salesperson: shortest single-vehicle tour visiting every node once and "
            "returning to the start, over a distance matrix. OR-Tools routing library."
        )
        categories = ["optimization", "routing", "tsp"]
        keywords = [
            "tsp",
            "travelling salesman",
            "traveling salesperson",
            "shortest tour",
            "routing",
            "route optimization",
            "or-tools",
            "optimization",
        ]
        tags = function_tags(
            title="Travelling Salesperson (Shortest Tour)",
            category="Routing",
            doc_llm=(
                "## `solve_tsp`\n\n"
                "Find the **shortest single-vehicle tour** that visits every node once and returns to "
                "`start`, over a distance matrix.\n\n"
                "**Inputs.** `distance` is either sparse `[from, to, meters]` integer triples (build "
                "with `array_agg([f, t, m])`) or a dense `n x n` matrix; `start` is the depot node. "
                "**Outputs.** The tour in visit order: `seq`, `node`, `arrival` (cumulative distance), "
                "`status`. The tour rotation / direction may vary but the length is optimal."
            ),
            doc_md=(
                "# solve_tsp\n\n"
                "Shortest single-vehicle tour over a distance matrix.\n\n"
                "```sql\n"
                "SELECT seq, node, arrival FROM ortools.main.solve_tsp(\n"
                "  distance := (SELECT array_agg([f, t, m]) FROM dist_edges), start := 0) ORDER BY seq;\n"
                "```\n"
            ),
            keywords=keywords,
            result_schema=_TSP_SCHEMA,
            source_url=_TSP_URL,
            executable_examples=_TSP_EXAMPLES,
        )
        examples = [FunctionExample(sql=_TSP_EXAMPLES[0]["sql"], description="5-node TSP, tour length 17.")]

    @classmethod
    def cardinality(cls, params: BindParams[SolveTspArgs]) -> TableCardinality:
        """Estimate the output cardinality (one row per node + return)."""
        return TableCardinality(estimate=16, max=1_000_000)

    @classmethod
    def process(cls, params: ProcessParams[SolveTspArgs], state: None, out: OutputCollector) -> None:
        """Solve the TSP and emit the tour rows (or one status row)."""
        a = params.args
        limits = Limits.from_settings(getattr(params, "settings", None))
        matrix, n = as_distance_matrix(a.distance, where="distance")
        result = routing.solve_tsp(
            matrix,
            n,
            start=int(a.start),
            time_limit_s=a.time_limit_s,
            first_solution_strategy=a.first_solution_strategy or None,
            local_search_metaheuristic=a.local_search_metaheuristic or None,
            random_seed=a.random_seed,
            limits=limits,
        )
        stats.record(status=result.status, objective=float(result.objective) if result.objective is not None else None)
        if not result.node:
            emit_one(out, null_row(_TSP_SCHEMA, status=result.status), _TSP_SCHEMA)
        else:
            m = len(result.node)
            emit_one(
                out,
                {"seq": result.seq, "node": result.node, "arrival": result.arrival, "status": [result.status] * m},
                _TSP_SCHEMA,
            )
        out.finish()


@dataclass(slots=True, frozen=True)
class SolveVrpArgs:
    """Arguments for ``solve_vrp`` (full routing surface)."""

    distance: Annotated[
        list[Any] | None,
        Arg(
            "distance",
            default=None,
            arrow_type=LIST_LIST_INT,
            doc="Distance matrix: sparse [from, to, meters] or dense n x n.",
        ),
    ]
    demand: Annotated[
        list[Any] | None,
        Arg(
            "demand",
            default=None,
            arrow_type=LIST_LIST_INT,
            doc="[node, d0, d1, ...] one demand column per capacity dimension.",
        ),
    ]
    num_vehicles: Annotated[int, Arg("num_vehicles", default=1, doc="Fleet size.")]
    vehicle_capacity: Annotated[
        int, Arg("vehicle_capacity", default=-1, doc="Shared capacity for dimension 0 (-1 = unset).")
    ]
    depot: Annotated[int, Arg("depot", default=0, doc="Shared start/end depot node.")]
    time_windows: Annotated[
        list[Any] | None,
        Arg("time_windows", default=None, arrow_type=LIST_LIST_INT, doc="[node, open, close] arrival windows."),
    ]
    service_time: Annotated[
        list[Any] | None,
        Arg("service_time", default=None, arrow_type=LIST_LIST_INT, doc="[node, secs] dwell time per node."),
    ]
    travel_time: Annotated[
        list[Any] | None,
        Arg(
            "travel_time",
            default=None,
            arrow_type=LIST_LIST_INT,
            doc="[from, to, secs] travel time; defaults to distance.",
        ),
    ]
    pickup_delivery: Annotated[
        list[Any] | None,
        Arg(
            "pickup_delivery",
            default=None,
            arrow_type=LIST_LIST_INT,
            doc="[pickup_node, delivery_node] same-vehicle pairs.",
        ),
    ]
    allow_dropping: Annotated[
        bool, Arg("allow_dropping", default=False, doc="Allow skipping infeasible stops (with a penalty).")
    ]
    drop_penalty: Annotated[
        int, Arg("drop_penalty", default=0, doc="Uniform per-node drop penalty (required if allow_dropping).")
    ]
    first_solution_strategy: Annotated[
        str, Arg("first_solution_strategy", default="", doc="OR-Tools first-solution strategy name.")
    ]
    local_search_metaheuristic: Annotated[
        str, Arg("local_search_metaheuristic", default="", doc="OR-Tools local-search metaheuristic name.")
    ]
    span_cost_coefficient: Annotated[
        int, Arg("span_cost_coefficient", default=0, doc="Per-vehicle route-span penalty (balances routes).")
    ]
    time_limit_s: Annotated[float, Arg("time_limit_s", default=10.0, doc="Wall-clock cap in seconds.")]
    lns_time_limit_s: Annotated[float, Arg("lns_time_limit_s", default=0.0, doc="LNS sub-solve cap (0 = unset).")]
    solution_limit: Annotated[
        int, Arg("solution_limit", default=0, doc="Stop after N improving solutions (0 = unset).")
    ]
    random_seed: Annotated[
        int, Arg("random_seed", default=1, doc="Reserved (routing determinism is the fixed strategy).")
    ]
    settings_json: Annotated[
        str,
        Arg(
            "settings_json",
            default="",
            doc="JSON escape for advanced config: per-vehicle/per-dimension capacities, per-vehicle depots.",
        ),
    ]


def _vrp_dims(demand: list[Any] | None) -> int:
    """Number of capacity dimensions implied by the demand argument (width - 1)."""
    if not demand:
        return 0
    first = demand[0]
    if not isinstance(first, (list, tuple)) or len(first) < 1:
        return 0
    return max(0, len(first) - 1)


def _vrp_schema(n_dims: int) -> pa.Schema:
    """Build the VRP output schema with one ``load_d`` column per capacity dimension."""
    fields = [
        sfield("route_id", pa.int32(), "Vehicle index (NULL on a dropped-node row).", nullable=True),
        sfield("seq", pa.int32(), "0-based stop order on the vehicle (NULL on a dropped-node row)."),
        sfield("node", pa.int64(), "Node id (depot at seq 0 and last).", nullable=False),
        sfield("arrival", pa.int64(), "Cumulative time/distance at arrival."),
        sfield("departure", pa.int64(), "Arrival + service time."),
    ]
    for d in range(n_dims):
        fields.append(sfield(f"load_{d}", pa.int64(), f"Cumulative load for capacity dimension {d}."))
    fields.append(sfield("is_dropped", pa.bool_(), "True on the row emitted for a dropped node.", nullable=False))
    fields.append(
        sfield("status", pa.string(), "OPTIMAL / FEASIBLE / INFEASIBLE / UNKNOWN / MODEL_INVALID.", nullable=False)
    )
    return pa.schema(fields)


@init_single_worker
class SolveVrp(TableFunctionGenerator[SolveVrpArgs]):
    """Capacitated, time-windowed vehicle routing with the full parameter surface."""

    FunctionArguments: ClassVar[type] = SolveVrpArgs

    class Meta:
        """SQL-facing metadata for ``solve_vrp``."""

        name = "solve_vrp"
        description = (
            "Vehicle routing problem: route a fleet from a depot to serve stops at minimum total "
            "distance, with optional capacity dimensions, time windows, service/travel time, "
            "pickup-delivery pairs, droppable stops, and route balancing. OR-Tools routing library."
        )
        categories = ["optimization", "routing", "vrp"]
        keywords = [
            "vrp",
            "vehicle routing",
            "cvrp",
            "capacitated routing",
            "time windows",
            "pickup delivery",
            "fleet routing",
            "last mile",
            "logistics",
            "or-tools",
            "optimization",
        ]
        tags = function_tags(
            title="Vehicle Routing (Capacitated / Time-Windowed)",
            category="Routing",
            doc_llm=(
                "## `solve_vrp`\n\n"
                "Route a **fleet of vehicles** from a depot to serve a set of stops at **minimum total "
                "distance**, with the full routing surface: multi-dimension **capacity** (`demand` + "
                "`vehicle_capacity`), **time windows** (`time_windows` + `service_time` / "
                "`travel_time`), **pickup-delivery** pairs (same vehicle + precedence), droppable "
                "stops (`allow_dropping` + `drop_penalty`), route balancing (`span_cost_coefficient`), "
                "and the OR-Tools `first_solution_strategy` / `local_search_metaheuristic`.\n\n"
                "**Inputs.** `distance` (sparse `[from, to, meters]` or dense `n x n`); `demand` = "
                "`[node, d0, d1, ...]` (the number of demand columns sets the number of capacity "
                "dimensions and therefore the `load_0` .. `load_k` output columns); `num_vehicles`; "
                "`vehicle_capacity`; `depot`. Advanced shapes -- per-vehicle / per-dimension "
                "capacities, per-vehicle `[start, end]` depots -- go through `settings_json`.\n\n"
                "**Outputs.** One row per stop: `route_id` (vehicle), `seq`, `node`, `arrival`, "
                "`departure`, `load_0` .. `load_k`, `is_dropped`, `status`. A dropped node appears as a "
                "single row with `is_dropped = true` and NULL `route_id`. An unsolved instance returns "
                "one status row.\n\n"
                "**Determinism.** Governed by the fixed `first_solution_strategy` / "
                "`local_search_metaheuristic`. The output `load_*` column count is fixed at bind time "
                "from the `demand` argument, so pass `demand` as a literal or a session VARIABLE that "
                "is set before the query binds."
            ),
            doc_md=(
                "# solve_vrp\n\n"
                "Capacitated, time-windowed vehicle routing.\n\n"
                "```sql\n"
                "SELECT route_id, seq, node, arrival, load_0 AS load FROM ortools.main.solve_vrp("
                "distance := (SELECT array_agg([f, t, m]) FROM dist_edges), "
                "demand := (SELECT array_agg([node, units]) FROM stop_demand), "
                "num_vehicles := 4, vehicle_capacity := 15, depot := 0, "
                "first_solution_strategy := 'PATH_CHEAPEST_ARC', "
                "local_search_metaheuristic := 'GUIDED_LOCAL_SEARCH', time_limit_s := 10.0) "
                "ORDER BY route_id, seq;\n"
                "```\n"
            ),
            keywords=keywords,
            result_dynamic_columns_md=(
                "The number of `load_d` columns is fixed at bind time from the `demand` argument "
                "(one per capacity dimension), so the output schema varies by call. For `k+1` capacity "
                "dimensions the columns are:\n\n"
                "| Name | Type | Description |\n"
                "| --- | --- | --- |\n"
                "| route_id | INTEGER | Vehicle index (NULL on a dropped-node row). |\n"
                "| seq | INTEGER | 0-based stop order on the vehicle. |\n"
                "| node | BIGINT | Node id (depot at seq 0 and last). |\n"
                "| arrival | BIGINT | Cumulative time/distance at arrival. |\n"
                "| departure | BIGINT | Arrival + service time. |\n"
                "| load_0 | BIGINT | Cumulative load for capacity dimension 0. |\n"
                "| load_k | BIGINT | Cumulative load for capacity dimension k (one load_d column per dimension). |\n"
                "| is_dropped | BOOLEAN | True on the row emitted for a dropped node. |\n"
                "| status | VARCHAR | OPTIMAL / FEASIBLE / INFEASIBLE / UNKNOWN / MODEL_INVALID. |"
            ),
            source_url=_VRP_URL,
            executable_examples=_VRP_EXAMPLES,
        )
        examples = [FunctionExample(sql=_VRP_EXAMPLES[0]["sql"], description="Capacitated VRP, optimal distance 6.")]

    @classmethod
    def on_bind(cls, params: BindParams[SolveVrpArgs]) -> BindResponse:
        """Build the output schema with one ``load_d`` column per demand dimension."""
        n_dims = _vrp_dims(params.args.demand)
        return BindResponse(output_schema=_vrp_schema(n_dims))

    @classmethod
    def cardinality(cls, params: BindParams[SolveVrpArgs]) -> TableCardinality:
        """Estimate the output cardinality (stops + depots + drops)."""
        return TableCardinality(estimate=32, max=1_000_000)

    @classmethod
    def process(cls, params: ProcessParams[SolveVrpArgs], state: None, out: OutputCollector) -> None:
        """Solve the VRP and emit per-stop rows (+ dropped rows), or one status row."""
        a = params.args
        schema = params.output_schema
        limits = Limits.from_settings(getattr(params, "settings", None))

        matrix, n = as_distance_matrix(a.distance, where="distance")
        demand_rows = rows_of(a.demand, where="demand")
        n_dims = _vrp_dims(a.demand)
        demand = {r[0]: list(r[1:]) for r in demand_rows}
        travel_matrix = None
        if a.travel_time:
            travel_matrix, _ = as_distance_matrix(a.travel_time, where="travel_time")
        time_windows = {r[0]: (r[1], r[2]) for r in rows_of(a.time_windows, where="time_windows")} or None
        service_time = {r[0]: r[1] for r in rows_of(a.service_time, where="service_time")}
        pickup_delivery = [(r[0], r[1]) for r in rows_of(a.pickup_delivery, where="pickup_delivery")] or None

        result = routing.solve_vrp(
            matrix,
            n,
            demand=demand,
            n_dims=n_dims,
            num_vehicles=int(a.num_vehicles),
            vehicle_capacity=None if a.vehicle_capacity < 0 else int(a.vehicle_capacity),
            depot=int(a.depot),
            time_windows=time_windows,
            service_time=service_time,
            travel_matrix=travel_matrix,
            pickup_delivery=pickup_delivery,
            allow_dropping=bool(a.allow_dropping),
            drop_penalty=int(a.drop_penalty) if a.drop_penalty else None,
            first_solution_strategy=a.first_solution_strategy or None,
            local_search_metaheuristic=a.local_search_metaheuristic or None,
            span_cost_coefficient=int(a.span_cost_coefficient),
            time_limit_s=a.time_limit_s,
            lns_time_limit_s=a.lns_time_limit_s or None,
            solution_limit=a.solution_limit or None,
            random_seed=a.random_seed,
            settings=routing.parse_settings(a.settings_json),
            limits=limits,
        )
        stats.record(status=result.status, objective=float(result.objective) if result.objective is not None else None)

        if not result.node:
            emit_one(out, null_row(schema, status=result.status), schema)
            out.finish()
            return

        m = len(result.node)
        cols: dict[str, list[Any]] = {
            "route_id": result.route_id,
            "seq": result.seq,
            "node": result.node,
            "arrival": result.arrival,
            "departure": result.departure,
        }
        for d in range(n_dims):
            cols[f"load_{d}"] = result.loads[d]
        cols["is_dropped"] = result.is_dropped
        cols["status"] = [result.status] * m
        emit_one(out, cols, schema)
        out.finish()
