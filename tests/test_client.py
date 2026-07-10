"""End-to-end tests driving the worker over the real VGI client/RPC stack.

Spawns ``ortools_worker.py`` as a subprocess via ``vgi.client.Client`` and
exercises the wire protocol exactly as DuckDB would after ATTACH -- in
particular that the ``LIST<LIST<BIGINT>>`` matrix arguments and the JSON model
argument marshal correctly across Arrow, and that the empty-vs-infeasible
contract and dynamic VRP schema hold over the wire.
"""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pytest
from vgi import Arguments
from vgi.client import Client

_WORKER = str(Path(__file__).resolve().parent.parent / "ortools_worker.py")

LIST_INT = pa.list_(pa.int64())
LIST_LIST_INT = pa.list_(pa.list_(pa.int64()))


@pytest.fixture(scope="module")
def client() -> Client:
    with Client(f"uv run --no-sync {_WORKER}") as c:
        yield c


def _ll(value: list) -> pa.Scalar:
    return pa.scalar(value, type=LIST_LIST_INT)


def _l(value: list) -> pa.Scalar:
    return pa.scalar(value, type=LIST_INT)


def _table(client: Client, name: str, named: dict) -> pa.Table:
    batches = list(client.table_function(function_name=name, arguments=Arguments(positional=(), named=named)))
    return pa.Table.from_batches(batches)


def test_solver_info(client: Client) -> None:
    # ``solver_info`` is a base table backed by the ``solver_info_scan`` generator
    # (VGI311); over the raw RPC we exercise the backing scan directly.
    t = _table(client, "solver_info_scan", {})
    comps = set(t.column("component").to_pylist())
    assert {"ortools", "worker", "dialect"} <= comps


def test_assignment_over_rpc(client: Client) -> None:
    cost = [[0, 0, 90], [0, 1, 76], [0, 2, 75], [1, 0, 35], [1, 1, 85], [1, 2, 55], [2, 0, 125], [2, 1, 95], [2, 2, 90]]
    t = _table(client, "solve_assignment", {"cost": _ll(cost), "maximize": pa.scalar(False)})
    assert set(t.column("status").to_pylist()) == {"OPTIMAL"}
    pairs = dict(zip(t.column("worker").to_pylist(), t.column("task").to_pylist(), strict=True))
    assert pairs == {0: 1, 1: 0, 2: 2}
    assert sum(t.column("cost").to_pylist()) == 201


def test_assignment_infeasible_returns_one_row(client: Client) -> None:
    t = _table(client, "solve_assignment", {"cost": _ll([[0, 0, 1], [0, 1, 1]]), "maximize": pa.scalar(False)})
    assert t.num_rows == 1
    assert t.column("status").to_pylist() == ["INFEASIBLE"]
    assert t.column("task").to_pylist() == [None]


def test_cpsat_worked_example_over_rpc(client: Client) -> None:
    doc = {
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
            {
                "type": "max_equality",
                "target": {"var": "makespan"},
                "exprs": [{"var": "e0"}, {"var": "e1"}, {"var": "e2"}],
            },
        ],
        "objective": {"sense": "minimize", "terms": [{"coef": 1, "var": "makespan"}]},
    }
    t = _table(client, "solve_cpsat", {"model": pa.scalar(json.dumps(doc)), "time_limit_s": pa.scalar(5.0)})
    assert set(t.column("status").to_pylist()) == {"OPTIMAL"}
    assert t.column("objective").to_pylist()[0] == 10.0
    vals = dict(zip(t.column("var").to_pylist(), t.column("value").to_pylist(), strict=True))
    assert vals["makespan"] == 10


def test_cpsat_infeasible_one_row_over_rpc(client: Client) -> None:
    doc = {
        "version": 1,
        "variables": [{"kind": "int", "name": "x", "lb": 0, "ub": 5}],
        "constraints": [{"type": "linear", "expr": {"var": "x"}, "op": ">=", "rhs": 10}],
    }
    t = _table(client, "solve_cpsat", {"model": pa.scalar(json.dumps(doc))})
    assert t.num_rows == 1
    assert t.column("status").to_pylist() == ["INFEASIBLE"]
    assert t.column("var").to_pylist() == [None]


def test_cpsat_model_invalid_raises_over_rpc(client: Client) -> None:
    doc = {"version": 1, "variables": [{"kind": "int", "name": "x", "lb": 0, "ub": 1.5}]}
    with pytest.raises(Exception, match="not an integer"):
        _table(client, "solve_cpsat", {"model": pa.scalar(json.dumps(doc))})


def test_tsp_over_rpc(client: Client) -> None:
    dist = [[0, 2, 9, 10, 1], [1, 0, 6, 4, 8], [15, 7, 0, 8, 3], [6, 3, 12, 0, 5], [9, 8, 4, 7, 0]]
    t = _table(
        client,
        "solve_tsp",
        {"distance": _ll(dist), "start": pa.scalar(0, type=pa.int64()), "time_limit_s": pa.scalar(3.0)},
    )
    assert set(t.column("status").to_pylist()) == {"OPTIMAL"}
    assert max(t.column("arrival").to_pylist()) == 17
    assert sorted(t.column("node").to_pylist()[:-1]) == [0, 1, 2, 3, 4]


def test_vrp_dynamic_load_column_over_rpc(client: Client) -> None:
    dist = [
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
    t = _table(
        client,
        "solve_vrp",
        {
            "distance": _ll(dist),
            "demand": _ll([[1, 1], [2, 1], [3, 1]]),
            "num_vehicles": pa.scalar(2, type=pa.int32()),
            "vehicle_capacity": pa.scalar(2, type=pa.int64()),
            "depot": pa.scalar(0, type=pa.int32()),
            "time_limit_s": pa.scalar(3.0),
        },
    )
    assert "load_0" in t.column_names
    assert set(t.column("status").to_pylist()) == {"OPTIMAL"}
    loads = [v for v in t.column("load_0").to_pylist() if v is not None]
    assert all(v <= 2 for v in loads)


def test_max_flow_value_scalar_over_rpc(client: Client) -> None:
    arcs = [[0, 1, 20], [0, 2, 30], [1, 2, 10], [1, 3, 10], [2, 3, 20]]
    batch = pa.RecordBatch.from_pydict({"arcs": pa.array([arcs], type=LIST_LIST_INT)})
    out = list(
        client.scalar_function(
            function_name="max_flow_value",
            input=iter([batch]),
            arguments=Arguments(positional=(pa.scalar(0, type=pa.int64()), pa.scalar(3, type=pa.int64())), named={}),
        )
    )
    table = pa.Table.from_batches(out)
    assert table.num_rows == 1
    assert table.column(0).to_pylist() == [30]


def test_knapsack_over_rpc(client: Client) -> None:
    t = _table(
        client,
        "solve_knapsack",
        {
            "item_values": _l([10, 20, 30, 40, 50]),
            "weights": _ll([[5], [10], [15], [20], [25]]),
            "capacities": _l([40]),
        },
    )
    chosen = [i for i, c in zip(t.column("item").to_pylist(), t.column("chosen").to_pylist(), strict=True) if c]
    assert chosen == [0, 2, 3]


def test_last_solve_stats_after_a_solve(client: Client) -> None:
    # ``last_solve_stats`` is a base table backed by the ``last_solve_stats_scan``
    # generator (VGI311); over the raw RPC we exercise the backing scan directly.
    _table(client, "solve_assignment", {"cost": _ll([[0, 0, 1], [1, 1, 1]]), "maximize": pa.scalar(False)})
    t = _table(client, "last_solve_stats_scan", {})
    metrics = dict(zip(t.column("metric").to_pylist(), t.column("value").to_pylist(), strict=True))
    assert metrics["status"] == "OPTIMAL"
