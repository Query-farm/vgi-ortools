"""Vehicle routing (TSP / VRP) on the OR-Tools routing library.

Builds a ``RoutingIndexManager`` + ``RoutingModel`` from a distance matrix and
the routing parameter surface (capacity dimensions, time windows, service /
travel time, pickup-delivery, dropping + penalty, first-solution /
metaheuristic strategies, span-cost balancing), solves with a bounded search,
and returns the per-vehicle stop sequence.

Advanced shapes that cannot be expressed by the scalar arguments -- per-vehicle
capacities, per-dimension capacities, per-vehicle start/end depots -- are passed
through the ``settings_json`` escape hatch (a JSON object documented in the
README); the scalar arguments cover the common single-dimension, shared-capacity
case shown in the spec examples.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from ..limits import DEFAULT_TIME_LIMIT_S, Limits
from ..status import SOLUTION_STATUSES, routing_status
from ..validate import ModelValidationError, ensure_int, require

# A generous per-dimension cumulative ceiling (distance / time / load horizon).
_CUMUL_CEIL = 1 << 40


@dataclass(slots=True)
class TspResult:
    """The single-vehicle tour in visit order, plus a status."""

    seq: list[int] = field(default_factory=list)
    node: list[int] = field(default_factory=list)
    arrival: list[int] = field(default_factory=list)
    status: str = "OPTIMAL"
    objective: int | None = None


@dataclass(slots=True)
class VrpResult:
    """Per-vehicle stop rows + dropped-node rows, plus a status.

    ``loads`` holds one cumulative-load list per capacity dimension, parallel to
    the other per-row lists; the function layer turns them into ``load_0`` ..
    ``load_k`` columns.
    """

    route_id: list[int | None] = field(default_factory=list)
    seq: list[int | None] = field(default_factory=list)
    node: list[int] = field(default_factory=list)
    arrival: list[int | None] = field(default_factory=list)
    departure: list[int | None] = field(default_factory=list)
    loads: list[list[int | None]] = field(default_factory=list)
    is_dropped: list[bool] = field(default_factory=list)
    status: str = "OPTIMAL"
    objective: int | None = None
    n_dims: int = 0


def _strategy(name: str | None, enum: Any, default: str, *, where: str) -> int:
    """Resolve a routing strategy name (string) to its protobuf enum int."""
    value = (name or default).strip().upper()
    member = getattr(enum, value, None)
    if not isinstance(member, int):
        raise ModelValidationError(f"{where} {value!r} is not a recognized OR-Tools strategy")
    return int(member)


def _search_params(
    *,
    first_solution_strategy: str | None,
    local_search_metaheuristic: str | None,
    time_limit_s: float,
    lns_time_limit_s: float | None,
    solution_limit: int | None,
) -> Any:
    """Build ``RoutingSearchParameters`` from the strategy / limit arguments."""
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = _strategy(
        first_solution_strategy,
        routing_enums_pb2.FirstSolutionStrategy,
        "PATH_CHEAPEST_ARC",
        where="first_solution_strategy",
    )
    params.local_search_metaheuristic = _strategy(
        local_search_metaheuristic,
        routing_enums_pb2.LocalSearchMetaheuristic,
        "GUIDED_LOCAL_SEARCH",
        where="local_search_metaheuristic",
    )
    params.time_limit.FromMilliseconds(int(time_limit_s * 1000))
    if lns_time_limit_s:
        params.lns_time_limit.FromMilliseconds(int(lns_time_limit_s * 1000))
    if solution_limit:
        params.solution_limit = int(solution_limit)
    params.log_search = False
    return params


def solve_tsp(
    matrix: Sequence[Sequence[int]],
    n: int,
    *,
    start: int = 0,
    time_limit_s: float = 5.0,
    first_solution_strategy: str | None = None,
    local_search_metaheuristic: str | None = None,
    random_seed: int = 1,
    limits: Limits | None = None,
) -> TspResult:
    """Solve a single-vehicle TSP over a dense distance matrix.

    Args:
        matrix: Dense ``n x n`` distance matrix (already integer-validated).
        n: Number of nodes.
        start: The start (and return) node of the tour.
        time_limit_s: Wall-clock cap (clamped to the worker hard cap).
        first_solution_strategy: OR-Tools first-solution strategy name.
        local_search_metaheuristic: OR-Tools metaheuristic name.
        random_seed: Reserved (routing determinism is governed by the fixed
            strategy); accepted for signature parity.
        limits: Resolved size / time limits.

    Returns:
        A :class:`TspResult` with the tour in visit order.
    """
    from ortools.constraint_solver import pywrapcp

    limits = limits or Limits()
    limits.guard_nodes(n)
    require(0 <= start < n, f"start node {start} is out of range [0, {n})")

    manager = pywrapcp.RoutingIndexManager(n, 1, start)
    routing = pywrapcp.RoutingModel(manager)

    def dist_cb(from_index: int, to_index: int) -> int:
        return int(matrix[manager.IndexToNode(from_index)][manager.IndexToNode(to_index)])

    transit = routing.RegisterTransitCallback(dist_cb)
    routing.SetArcCostEvaluatorOfAllVehicles(transit)
    routing.AddDimension(transit, 0, _CUMUL_CEIL, True, "Distance")
    dist_dim = routing.GetDimensionOrDie("Distance")

    params = _search_params(
        first_solution_strategy=first_solution_strategy,
        local_search_metaheuristic=local_search_metaheuristic,
        time_limit_s=limits.clamp_time(time_limit_s, default=5.0),
        lns_time_limit_s=None,
        solution_limit=None,
    )
    solution = routing.SolveWithParameters(params)
    status = routing_status(routing.status())

    result = TspResult(status=status)
    if solution is None or status not in SOLUTION_STATUSES:
        if status in SOLUTION_STATUSES:
            result.status = "UNKNOWN"
        return result

    index = routing.Start(0)
    seq = 0
    while True:
        node = manager.IndexToNode(index)
        result.seq.append(seq)
        result.node.append(node)
        result.arrival.append(int(solution.Value(dist_dim.CumulVar(index))))
        if routing.IsEnd(index):
            break
        index = solution.Value(routing.NextVar(index))
        seq += 1
    result.objective = int(solution.ObjectiveValue())
    return result


def solve_vrp(
    matrix: Sequence[Sequence[int]],
    n: int,
    *,
    demand: dict[int, list[int]] | None = None,
    n_dims: int = 0,
    num_vehicles: int = 1,
    vehicle_capacity: int | None = None,
    depot: int = 0,
    time_windows: dict[int, tuple[int, int]] | None = None,
    service_time: dict[int, int] | None = None,
    travel_matrix: Sequence[Sequence[int]] | None = None,
    pickup_delivery: Sequence[tuple[int, int]] | None = None,
    allow_dropping: bool = False,
    drop_penalty: dict[int, int] | int | None = None,
    first_solution_strategy: str | None = None,
    local_search_metaheuristic: str | None = None,
    span_cost_coefficient: int = 0,
    time_limit_s: float = DEFAULT_TIME_LIMIT_S,
    lns_time_limit_s: float | None = None,
    solution_limit: int | None = None,
    random_seed: int = 1,
    settings: dict[str, Any] | None = None,
    limits: Limits | None = None,
) -> VrpResult:
    """Solve a (capacitated, time-windowed) vehicle routing problem.

    Args:
        matrix: Dense ``n x n`` distance matrix (integer-validated).
        n: Number of nodes.
        demand: ``node -> [d0, d1, ...]`` per-dimension demand (default all 0).
        n_dims: Number of capacity dimensions (width of a demand row minus 1).
        num_vehicles: Fleet size.
        vehicle_capacity: Shared capacity for dimension 0 (advanced per-vehicle /
            per-dimension capacities go via ``settings``).
        depot: Shared start/end depot node.
        time_windows: ``node -> (open, close)`` arrival windows (needs travel /
            service time to be meaningful).
        service_time: ``node -> dwell`` added at each node.
        travel_matrix: Dense ``n x n`` travel-time matrix; defaults to ``matrix``.
        pickup_delivery: ``[(pickup, delivery), ...]`` same-vehicle + precedence.
        allow_dropping: Add per-node disjunctions so infeasible stops may be skipped.
        drop_penalty: Scalar or ``node -> penalty`` (required if ``allow_dropping``).
        first_solution_strategy: OR-Tools first-solution strategy name.
        local_search_metaheuristic: OR-Tools metaheuristic name.
        span_cost_coefficient: Per-vehicle route-span penalty (balances routes).
        time_limit_s: Wall-clock cap (clamped to the worker hard cap).
        lns_time_limit_s: LNS sub-solve cap.
        solution_limit: Stop after N improving solutions.
        random_seed: Reserved (see :func:`solve_tsp`).
        settings: Parsed ``settings_json`` escape (per-vehicle / per-dimension
            capacities, per-vehicle ``[start, end]`` depots).
        limits: Resolved size / time limits.

    Returns:
        A :class:`VrpResult` with one row per stop (and one per dropped node).

    Raises:
        ModelValidationError: If the parameters are inconsistent.
    """
    from ortools.constraint_solver import pywrapcp

    limits = limits or Limits()
    limits.guard_nodes(n)
    settings = settings or {}
    demand = demand or {}
    service_time = service_time or {}

    starts, ends = _depots(settings, depot, num_vehicles, n)
    if starts is not None:
        manager = pywrapcp.RoutingIndexManager(n, num_vehicles, starts, ends)
    else:
        manager = pywrapcp.RoutingIndexManager(n, num_vehicles, depot)
    routing = pywrapcp.RoutingModel(manager)

    def dist_cb(from_index: int, to_index: int) -> int:
        return int(matrix[manager.IndexToNode(from_index)][manager.IndexToNode(to_index)])

    transit = routing.RegisterTransitCallback(dist_cb)
    routing.SetArcCostEvaluatorOfAllVehicles(transit)
    routing.AddDimension(transit, 0, _CUMUL_CEIL, True, "Distance")
    dist_dim = routing.GetDimensionOrDie("Distance")
    if span_cost_coefficient:
        dist_dim.SetSpanCostCoefficientForAllVehicles(int(span_cost_coefficient))

    cap_dims = _add_capacity(routing, manager, demand, n_dims, vehicle_capacity, num_vehicles, settings)
    time_dim = _add_time(routing, manager, travel_matrix or matrix, service_time, time_windows, num_vehicles)
    _add_pickup_delivery(routing, manager, pickup_delivery, dist_dim)
    _add_dropping(routing, manager, allow_dropping, drop_penalty, depot, starts, ends, n)

    params = _search_params(
        first_solution_strategy=first_solution_strategy,
        local_search_metaheuristic=local_search_metaheuristic,
        time_limit_s=limits.clamp_time(time_limit_s),
        lns_time_limit_s=lns_time_limit_s,
        solution_limit=solution_limit,
    )
    solution = routing.SolveWithParameters(params)
    status = routing_status(routing.status())

    result = VrpResult(status=status, n_dims=n_dims)
    if solution is None or status not in SOLUTION_STATUSES:
        if status in SOLUTION_STATUSES:
            result.status = "UNKNOWN"
        return result

    arrival_dim = time_dim if time_dim is not None else dist_dim
    result.loads = [[] for _ in range(n_dims)]
    for vehicle in range(num_vehicles):
        index = routing.Start(vehicle)
        seq = 0
        while True:
            node = manager.IndexToNode(index)
            arrival = int(solution.Value(arrival_dim.CumulVar(index)))
            result.route_id.append(vehicle)
            result.seq.append(seq)
            result.node.append(node)
            result.arrival.append(arrival)
            result.departure.append(arrival + service_time.get(node, 0))
            for d in range(n_dims):
                result.loads[d].append(int(solution.Value(cap_dims[d].CumulVar(index))))
            result.is_dropped.append(False)
            if routing.IsEnd(index):
                break
            index = solution.Value(routing.NextVar(index))
            seq += 1

    # Dropped nodes: one row each, route_id NULL, is_dropped True.
    for node in range(n):
        idx = manager.NodeToIndex(node)
        if idx < 0:
            continue
        if routing.IsStart(idx) or routing.IsEnd(idx):
            continue
        if solution.Value(routing.NextVar(idx)) == idx:
            result.route_id.append(None)
            result.seq.append(None)
            result.node.append(node)
            result.arrival.append(None)
            result.departure.append(None)
            for d in range(n_dims):
                result.loads[d].append(None)
            result.is_dropped.append(True)

    result.objective = int(solution.ObjectiveValue())
    return result


def _depots(
    settings: dict[str, Any], depot: int, num_vehicles: int, n: int
) -> tuple[list[int] | None, list[int] | None]:
    """Resolve per-vehicle start/end depots from ``settings`` (or the scalar depot)."""
    raw = settings.get("depots")
    if raw is None:
        return None, None
    require(isinstance(raw, list) and len(raw) == num_vehicles, "settings.depots must be one [start, end] per vehicle")
    starts, ends = [], []
    for k, pair in enumerate(raw):
        require(isinstance(pair, list) and len(pair) == 2, f"settings.depots[{k}] must be [start, end]")
        s = ensure_int(pair[0], where=f"settings.depots[{k}][0]")
        e = ensure_int(pair[1], where=f"settings.depots[{k}][1]")
        require(0 <= s < n and 0 <= e < n, f"settings.depots[{k}] node out of range [0, {n})")
        starts.append(s)
        ends.append(e)
    return starts, ends


def _add_capacity(
    routing: Any,
    manager: Any,
    demand: dict[int, list[int]],
    n_dims: int,
    vehicle_capacity: int | None,
    num_vehicles: int,
    settings: dict[str, Any],
) -> list[Any]:
    """Add one capacity dimension per demand column; return the dimension objects."""
    dims: list[Any] = []
    caps_setting = settings.get("vehicle_capacities")
    for d in range(n_dims):

        def demand_cb(from_index: int, d: int = d) -> int:
            node = manager.IndexToNode(from_index)
            row = demand.get(node)
            return row[d] if row is not None else 0

        cb = routing.RegisterUnaryTransitCallback(demand_cb)
        caps = _capacities_for_dim(caps_setting, d, vehicle_capacity, num_vehicles)
        routing.AddDimensionWithVehicleCapacity(cb, 0, caps, True, f"Capacity{d}")
        dims.append(routing.GetDimensionOrDie(f"Capacity{d}"))
    return dims


def _capacities_for_dim(caps_setting: Any, dim: int, vehicle_capacity: int | None, num_vehicles: int) -> list[int]:
    """Resolve the per-vehicle capacity vector for one dimension."""
    if caps_setting is not None:
        # Either per-vehicle list (single dim) or per-dimension list-of-lists.
        if caps_setting and isinstance(caps_setting[0], list):
            require(dim < len(caps_setting), "settings.vehicle_capacities has fewer dimensions than demand columns")
            per_v = caps_setting[dim]
        else:
            per_v = caps_setting
        require(len(per_v) == num_vehicles, "settings.vehicle_capacities must have one capacity per vehicle")
        return [int(c) for c in per_v]
    if vehicle_capacity is None:
        raise ModelValidationError(
            "demand was given but no vehicle_capacity; pass vehicle_capacity (scalar) "
            "or settings_json.vehicle_capacities for per-vehicle / per-dimension capacities"
        )
    return [int(vehicle_capacity)] * num_vehicles


def _add_time(
    routing: Any,
    manager: Any,
    travel: Sequence[Sequence[int]],
    service_time: dict[int, int],
    time_windows: dict[int, tuple[int, int]] | None,
    num_vehicles: int,
) -> Any:
    """Add a Time dimension + window ranges when time windows are present; else None."""
    if not time_windows:
        return None

    def time_cb(from_index: int, to_index: int) -> int:
        f = manager.IndexToNode(from_index)
        t = manager.IndexToNode(to_index)
        return int(travel[f][t] + service_time.get(f, 0))

    cb = routing.RegisterTransitCallback(time_cb)
    routing.AddDimension(cb, _CUMUL_CEIL, _CUMUL_CEIL, False, "Time")
    time_dim = routing.GetDimensionOrDie("Time")
    for node, (open_, close_) in time_windows.items():
        idx = manager.NodeToIndex(node)
        if idx >= 0:
            time_dim.CumulVar(idx).SetRange(int(open_), int(close_))
    # Let the solver choose start times: minimize each vehicle's start/end cumul.
    for v in range(num_vehicles):
        routing.AddVariableMinimizedByFinalizer(time_dim.CumulVar(routing.Start(v)))
        routing.AddVariableMinimizedByFinalizer(time_dim.CumulVar(routing.End(v)))
    return time_dim


def _add_pickup_delivery(
    routing: Any, manager: Any, pickup_delivery: Sequence[tuple[int, int]] | None, dist_dim: Any
) -> None:
    """Add same-vehicle + precedence constraints for each pickup-delivery pair."""
    if not pickup_delivery:
        return
    solver = routing.solver()
    for p, d in pickup_delivery:
        pi = manager.NodeToIndex(p)
        di = manager.NodeToIndex(d)
        routing.AddPickupAndDelivery(pi, di)
        solver.Add(routing.VehicleVar(pi) == routing.VehicleVar(di))
        solver.Add(dist_dim.CumulVar(pi) <= dist_dim.CumulVar(di))


def _add_dropping(
    routing: Any,
    manager: Any,
    allow_dropping: bool,
    drop_penalty: dict[int, int] | int | None,
    depot: int,
    starts: list[int] | None,
    ends: list[int] | None,
    n: int,
) -> None:
    """Add a per-node disjunction with a drop penalty for every non-depot node."""
    if not allow_dropping:
        return
    if drop_penalty is None:
        raise ModelValidationError("allow_dropping requires drop_penalty (a scalar or [[node, penalty], ...])")
    depot_nodes = set()
    if starts is not None and ends is not None:
        depot_nodes.update(starts)
        depot_nodes.update(ends)
    else:
        depot_nodes.add(depot)
    for node in range(n):
        if node in depot_nodes:
            continue
        idx = manager.NodeToIndex(node)
        if idx < 0:
            continue
        penalty = drop_penalty.get(node, 0) if isinstance(drop_penalty, dict) else int(drop_penalty)
        routing.AddDisjunction([idx], int(penalty))


def parse_settings(settings_json: str | None) -> dict[str, Any]:
    """Parse the optional ``settings_json`` VRP escape hatch into a dict.

    Args:
        settings_json: The JSON object string, or ``None`` / empty.

    Returns:
        The parsed dict (empty when no settings were given).

    Raises:
        ModelValidationError: If the JSON is invalid or not an object.
    """
    if not settings_json or not settings_json.strip():
        return {}
    try:
        obj = json.loads(settings_json)
    except json.JSONDecodeError as exc:
        raise ModelValidationError(f"settings_json is not valid JSON: {exc}") from exc
    require(isinstance(obj, dict), "settings_json must be a JSON object")
    assert isinstance(obj, dict)
    return obj
