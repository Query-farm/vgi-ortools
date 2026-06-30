"""Golden, hand-verified optimization fixtures over the pure solver builders.

These run the Arrow-free ``vgi_ortools.models`` solvers directly (fast, no worker
process) and assert known-optimal objective values / makespans -- the validation
backbone. Determinism is asserted by re-solving with a fixed seed; the
empty-vs-infeasible and MODEL_INVALID contracts are asserted here and again over
the wire in test_client.py.
"""

from __future__ import annotations

import json

import pytest

from vgi_ortools.arrow_io import as_distance_matrix
from vgi_ortools.models import assignment, cpsat, flow, packing, routing, scheduling
from vgi_ortools.status import FEASIBLE, INFEASIBLE, OPTIMAL, SOLUTION_STATUSES
from vgi_ortools.validate import ModelValidationError

# ---- assignment ----------------------------------------------------------

# Cost matrix whose unique optimal assignment is (0->1, 1->0, 2->2), total 76+35+90 = 201.
_ASSIGN = [
    [0, 0, 90],
    [0, 1, 76],
    [0, 2, 75],
    [1, 0, 35],
    [1, 1, 85],
    [1, 2, 55],
    [2, 0, 125],
    [2, 1, 95],
    [2, 2, 90],
]


def test_assignment_known_optimum() -> None:
    r = assignment.solve(_ASSIGN, maximize=False)
    assert r.status == OPTIMAL
    assert dict(zip(r.worker, r.task, strict=True)) == {0: 1, 1: 0, 2: 2}
    assert sum(r.cost) == 201


def test_assignment_maximize() -> None:
    r = assignment.solve([[0, 0, 8], [0, 1, 7], [1, 0, 6], [1, 1, 9]], maximize=True)
    assert r.status == OPTIMAL
    assert sum(r.cost) == 8 + 9  # 0->0, 1->1


def test_assignment_infeasible_returns_status_not_empty() -> None:
    # Worker 1 has no allowed task -> no perfect matching.
    r = assignment.solve([[0, 0, 1], [0, 1, 1]], maximize=False)
    assert r.status == INFEASIBLE
    assert r.worker == []


# ---- TSP -----------------------------------------------------------------

_TSP = [[0, 2, 9, 10, 1], [1, 0, 6, 4, 8], [15, 7, 0, 8, 3], [6, 3, 12, 0, 5], [9, 8, 4, 7, 0]]


def test_tsp_known_length() -> None:
    r = routing.solve_tsp(_TSP, 5, start=0, time_limit_s=3)
    assert r.status == OPTIMAL
    assert r.objective == 17
    # Every node appears exactly once (the tour repeats the start at the end).
    visited = r.node[:-1]
    assert sorted(visited) == [0, 1, 2, 3, 4]
    assert r.node[0] == 0 and r.node[-1] == 0


# ---- CVRP ----------------------------------------------------------------

_CVRP = [
    [0, 1, 1],
    [0, 2, 1],
    [0, 3, 2],
    [1, 0, 1],
    [1, 2, 1],
    [1, 3, 2],
    [2, 0, 1],
    [2, 1, 1],
    [2, 3, 1],
    [3, 0, 2],
    [3, 1, 2],
    [3, 2, 1],
]


def test_cvrp_known_optimum_and_capacity_respected() -> None:
    matrix, n = as_distance_matrix(_CVRP)
    r = routing.solve_vrp(
        matrix,
        n,
        demand={1: [1], 2: [1], 3: [1]},
        n_dims=1,
        num_vehicles=2,
        vehicle_capacity=2,
        depot=0,
        time_limit_s=3,
    )
    assert r.status == OPTIMAL
    assert r.objective == 6
    # Per-route final load never exceeds capacity.
    assert all(v is None or v <= 2 for v in r.loads[0])


def test_cvrp_infeasible_when_demand_exceeds_fleet() -> None:
    matrix, n = as_distance_matrix(_CVRP)
    # One vehicle, capacity 1, total demand 3 and dropping off -> infeasible.
    r = routing.solve_vrp(
        matrix,
        n,
        demand={1: [1], 2: [1], 3: [1]},
        n_dims=1,
        num_vehicles=1,
        vehicle_capacity=1,
        depot=0,
        time_limit_s=3,
    )
    assert r.status not in SOLUTION_STATUSES
    assert r.node == []


def test_vrp_dropping_skips_a_stop() -> None:
    matrix, n = as_distance_matrix(_CVRP)
    r = routing.solve_vrp(
        matrix,
        n,
        demand={1: [1], 2: [1], 3: [1]},
        n_dims=1,
        num_vehicles=1,
        vehicle_capacity=2,
        depot=0,
        allow_dropping=True,
        drop_penalty=100,
        time_limit_s=3,
    )
    assert r.status == OPTIMAL
    assert sum(r.is_dropped) == 1  # exactly one stop dropped (cap 2, demand 3)


# ---- job shop ------------------------------------------------------------

# Classic 3x3 job shop with known optimal makespan 11.
_JOBSHOP = [
    [0, 0, 0, 3],
    [0, 1, 1, 2],
    [0, 2, 2, 2],
    [1, 0, 0, 2],
    [1, 1, 2, 1],
    [1, 2, 1, 4],
    [2, 0, 1, 4],
    [2, 1, 2, 3],
]


def test_jobshop_known_makespan_and_no_overlap() -> None:
    r = scheduling.solve_jobshop(_JOBSHOP, time_limit_s=10)
    assert r.status == OPTIMAL
    assert r.makespan == 11
    # No two tasks on the same machine overlap.
    by_machine: dict[int, list[tuple[int, int]]] = {}
    for m, s, e in zip(r.machine, r.start, r.end, strict=True):
        by_machine.setdefault(m, []).append((s, e))
    for spans in by_machine.values():
        spans.sort()
        for (_, e1), (s2, _) in zip(spans, spans[1:], strict=False):
            assert e1 <= s2


def test_rcpsp_solves() -> None:
    r = scheduling.solve_rcpsp([[0, 3, 2], [1, 2, 1], [2, 4, 2], [3, 2, 1]], [[0, 2], [1, 3]], [[0, 2]], time_limit_s=5)
    assert r.status == OPTIMAL
    assert r.makespan is not None and r.makespan >= 4


# ---- flow ----------------------------------------------------------------


def test_max_flow_known_value() -> None:
    r = flow.max_flow([[0, 1, 20], [0, 2, 30], [1, 2, 10], [1, 3, 10], [2, 3, 20]], source=0, sink=3)
    assert r.status == OPTIMAL
    assert r.value == 30


def test_min_cost_flow_known_cost() -> None:
    # The canonical OR-Tools min-cost-flow example: optimal total cost 150.
    r = flow.min_cost_flow(
        [
            [0, 1, 15, 4],
            [0, 2, 8, 4],
            [1, 2, 20, 2],
            [1, 3, 4, 2],
            [1, 4, 10, 6],
            [2, 3, 15, 1],
            [2, 4, 4, 3],
            [3, 4, 20, 2],
            [4, 2, 5, 3],
        ],
        [[0, 20], [3, -5], [4, -15]],
    )
    assert r.status == OPTIMAL
    assert sum(r.cost) == 150


# ---- packing -------------------------------------------------------------


def test_knapsack_known_value() -> None:
    r = packing.solve_knapsack([10, 20, 30, 40, 50], [[5], [10], [15], [20], [25]], [40])
    assert r.status == OPTIMAL
    assert r.value == 80
    assert [i for i, c in zip(r.item, r.chosen, strict=True) if c] == [0, 2, 3]


def test_bin_packing_minimizes_bins() -> None:
    r = packing.solve_bin_packing([3, 5, 2, 7, 1, 4], bin_capacity=10, time_limit_s=5)
    assert r.status == OPTIMAL
    # Total size 22 > 2*10, so at least 3 bins are required; the optimum is 3.
    assert len(r.bin_used) == 3
    assert sum(r.bin_used.values()) == 6  # every item placed exactly once


# ---- CP-SAT dialect: worked example + contracts --------------------------

_MAKESPAN_DOC = {
    "version": 1,
    "variables": [
        {"kind": "int", "name": "s0", "lb": 0, "ub": 20},
        {"kind": "int", "name": "e0", "lb": 0, "ub": 20},
        {"kind": "int", "name": "s1", "lb": 0, "ub": 20},
        {"kind": "int", "name": "e1", "lb": 0, "ub": 20},
        {"kind": "int", "name": "s2", "lb": 0, "ub": 20},
        {"kind": "int", "name": "e2", "lb": 0, "ub": 20},
        {"kind": "interval", "name": "i0", "start": {"var": "s0"}, "size": {"const": 3}, "end": {"var": "e0"}},
        {"kind": "interval", "name": "i1", "start": {"var": "s1"}, "size": {"const": 5}, "end": {"var": "e1"}},
        {"kind": "interval", "name": "i2", "start": {"var": "s2"}, "size": {"const": 2}, "end": {"var": "e2"}},
        {"kind": "int", "name": "makespan", "lb": 0, "ub": 20},
    ],
    "constraints": [
        {"type": "no_overlap", "intervals": ["i0", "i1", "i2"]},
        {"type": "max_equality", "target": {"var": "makespan"}, "exprs": [{"var": "e0"}, {"var": "e1"}, {"var": "e2"}]},
    ],
    "objective": {"sense": "minimize", "terms": [{"coef": 1, "var": "makespan"}], "offset": 0},
}


def test_cpsat_worked_example_makespan() -> None:
    r = cpsat.solve(json.dumps(_MAKESPAN_DOC), time_limit_s=5)
    assert r.status == OPTIMAL
    assert r.objective == 10.0
    values = dict(zip(r.var, r.value, strict=True))
    assert values["makespan"] == 10
    # Intervals expand to .start/.size/.end.
    assert "i0.start" in values and "i0.end" in values


def test_cpsat_infeasible_returns_single_null_row() -> None:
    doc = {
        "version": 1,
        "variables": [{"kind": "int", "name": "x", "lb": 0, "ub": 5}],
        "constraints": [{"type": "linear", "expr": {"var": "x"}, "op": ">=", "rhs": 10}],
    }
    r = cpsat.solve(json.dumps(doc), time_limit_s=2)
    assert r.status == INFEASIBLE
    assert r.var == [None] and r.value == [None]
    assert r.objective is None


def test_cpsat_model_invalid_fractional_names_value() -> None:
    doc = {"version": 1, "variables": [{"kind": "int", "name": "x", "lb": 0, "ub": 1.5}]}
    with pytest.raises(ModelValidationError, match="not an integer"):
        cpsat.solve(json.dumps(doc))


def test_distance_fractional_rejected_with_guidance() -> None:
    with pytest.raises(ModelValidationError, match="12.4 is not an integer"):
        as_distance_matrix([[0, 1, 12.4], [1, 0, 5]])


def test_cpsat_determinism_same_objective() -> None:
    r1 = cpsat.solve(json.dumps(_MAKESPAN_DOC), time_limit_s=5, random_seed=1)
    r2 = cpsat.solve(json.dumps(_MAKESPAN_DOC), time_limit_s=5, random_seed=1)
    assert r1.objective == r2.objective
    assert dict(zip(r1.var, r1.value, strict=True)) == dict(zip(r2.var, r2.value, strict=True))


def test_cpsat_linear_and_objective() -> None:
    # Maximize 3x + 2y s.t. x + y <= 7, 0 <= x,y <= 10 -> 21 at x=7, y=0.
    doc = {
        "version": 1,
        "variables": [{"kind": "int", "name": "x", "lb": 0, "ub": 10}, {"kind": "int", "name": "y", "lb": 0, "ub": 10}],
        "constraints": [
            {
                "type": "linear",
                "expr": {"sum": [{"coef": 1, "var": "x"}, {"coef": 1, "var": "y"}]},
                "op": "<=",
                "rhs": 7,
            }
        ],
        "objective": {"sense": "maximize", "terms": [{"coef": 3, "var": "x"}, {"coef": 2, "var": "y"}]},
    }
    r = cpsat.solve(json.dumps(doc), time_limit_s=3)
    assert r.status == OPTIMAL
    assert r.objective == 21.0


def test_cpsat_all_different_and_bool() -> None:
    doc = {
        "version": 1,
        "variables": [
            {"kind": "int", "name": "a", "lb": 1, "ub": 3},
            {"kind": "int", "name": "b", "lb": 1, "ub": 3},
            {"kind": "int", "name": "c", "lb": 1, "ub": 3},
        ],
        "constraints": [{"type": "all_different", "vars": [{"var": "a"}, {"var": "b"}, {"var": "c"}]}],
    }
    r = cpsat.solve(json.dumps(doc), time_limit_s=2)
    assert r.status == OPTIMAL
    vals = dict(zip(r.var, r.value, strict=True))
    assert sorted([vals["a"], vals["b"], vals["c"]]) == [1, 2, 3]


def test_cpsat_reification_enforce() -> None:
    # b == 1 implies x >= 5; maximize b then x.
    doc = {
        "version": 1,
        "variables": [{"kind": "bool", "name": "b"}, {"kind": "int", "name": "x", "lb": 0, "ub": 10}],
        "constraints": [
            {"type": "linear", "expr": {"var": "x"}, "op": ">=", "rhs": 5, "enforce": ["b"]},
            {"type": "linear", "expr": {"var": "b"}, "op": "==", "rhs": 1},
        ],
    }
    r = cpsat.solve(json.dumps(doc), time_limit_s=2)
    assert r.status == OPTIMAL
    assert dict(zip(r.var, r.value, strict=True))["x"] >= 5


def test_cpsat_unknown_dialect_version_rejected() -> None:
    with pytest.raises(ModelValidationError, match="dialect version"):
        cpsat.solve(json.dumps({"version": 99, "variables": []}))


def test_cpsat_bad_json_rejected() -> None:
    with pytest.raises(ModelValidationError, match="not valid JSON"):
        cpsat.solve("{not json")


# Fisher-Thompson ft10: a notoriously hard 10x10 job shop (optimum 930). Used as
# the time-limit fixture: a sub-second cap cannot prove its optimum, so the
# solver honestly returns FEASIBLE -- proving the time-limit clamp works.
_FT10 = [
    [(0, 29), (1, 78), (2, 9), (3, 36), (4, 49), (5, 11), (6, 62), (7, 56), (8, 44), (9, 21)],
    [(0, 43), (2, 90), (4, 75), (9, 11), (3, 69), (1, 28), (6, 46), (5, 46), (7, 72), (8, 30)],
    [(1, 91), (0, 85), (3, 39), (2, 74), (8, 90), (5, 10), (7, 12), (6, 89), (9, 45), (4, 33)],
    [(1, 81), (2, 95), (0, 71), (4, 99), (6, 9), (8, 52), (7, 85), (3, 98), (9, 22), (5, 43)],
    [(2, 14), (0, 6), (1, 22), (5, 61), (3, 26), (4, 69), (8, 21), (7, 49), (9, 72), (6, 53)],
    [(2, 84), (1, 2), (5, 52), (3, 95), (8, 48), (9, 72), (0, 47), (6, 65), (4, 6), (7, 25)],
    [(1, 46), (0, 37), (3, 61), (2, 13), (6, 32), (5, 21), (9, 32), (8, 89), (7, 30), (4, 55)],
    [(2, 31), (0, 86), (1, 46), (5, 74), (4, 32), (6, 88), (8, 19), (9, 48), (7, 36), (3, 79)],
    [(0, 76), (1, 69), (3, 76), (5, 51), (2, 85), (9, 11), (6, 40), (7, 89), (4, 26), (8, 74)],
    [(1, 85), (0, 13), (2, 61), (6, 7), (8, 64), (9, 76), (5, 47), (3, 52), (4, 90), (7, 45)],
]


def test_cpsat_time_limit_honored_on_hard_instance() -> None:
    tasks = [[j, t, m, d] for j, seq in enumerate(_FT10) for t, (m, d) in enumerate(seq)]
    r = scheduling.solve_jobshop(tasks, time_limit_s=0.5)
    assert r.status in SOLUTION_STATUSES
    # The clamp worked: ft10 is not proven optimal in 0.5s, so the status is
    # honestly FEASIBLE (a solution found, optimality not proven).
    assert r.status == FEASIBLE
