"""Min-cost flow and max-flow via ``SimpleMinCostFlow`` / ``SimpleMaxFlow``."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from ..status import INFEASIBLE, OPTIMAL, UNKNOWN
from ..validate import require


@dataclass(slots=True)
class MinCostFlowResult:
    """Per-arc flow rows (from, to, flow, cost) plus a status."""

    src: list[int] = field(default_factory=list)
    dst: list[int] = field(default_factory=list)
    flow: list[int] = field(default_factory=list)
    cost: list[int] = field(default_factory=list)
    status: str = OPTIMAL


@dataclass(slots=True)
class MaxFlowResult:
    """Per-arc flow rows (from, to, flow), the value, and a status."""

    src: list[int] = field(default_factory=list)
    dst: list[int] = field(default_factory=list)
    flow: list[int] = field(default_factory=list)
    value: int = 0
    status: str = OPTIMAL


def min_cost_flow(arcs: Sequence[Sequence[int]], supplies: Sequence[Sequence[int]]) -> MinCostFlowResult:
    """Solve a minimum-cost flow.

    Args:
        arcs: ``[[from, to, capacity, unit_cost], ...]``.
        supplies: ``[[node, supply], ...]`` (positive = source, negative = sink;
            the supplies must sum to zero).

    Returns:
        A :class:`MinCostFlowResult` with one row per arc carrying positive flow.

    Raises:
        ModelValidationError: If an arc / supply row is malformed.
    """
    from ortools.graph.python import min_cost_flow as mcf

    require(len(arcs) > 0, "arcs is required and must be non-empty")
    solver = mcf.SimpleMinCostFlow()
    n = 0
    for k, a in enumerate(arcs):
        require(len(a) == 4, f"arcs[{k}] must be [from, to, capacity, unit_cost]")
        i, j, cap, ucost = int(a[0]), int(a[1]), int(a[2]), int(a[3])
        solver.add_arc_with_capacity_and_unit_cost(i, j, cap, ucost)
        n = max(n, i + 1, j + 1)
    for k, s in enumerate(supplies):
        require(len(s) == 2, f"supplies[{k}] must be [node, supply]")
        solver.set_node_supply(int(s[0]), int(s[1]))

    status = solver.solve()
    result = MinCostFlowResult()
    if status != solver.OPTIMAL:
        result.status = INFEASIBLE if status == solver.INFEASIBLE else UNKNOWN
        return result
    for arc in range(solver.num_arcs()):
        f = solver.flow(arc)
        if f <= 0:
            continue
        result.src.append(solver.tail(arc))
        result.dst.append(solver.head(arc))
        result.flow.append(f)
        result.cost.append(f * solver.unit_cost(arc))
    return result


def max_flow(arcs: Sequence[Sequence[int]], *, source: int, sink: int) -> MaxFlowResult:
    """Solve a maximum flow from ``source`` to ``sink``.

    Args:
        arcs: ``[[from, to, capacity], ...]``.
        source: The source node id.
        sink: The sink node id.

    Returns:
        A :class:`MaxFlowResult` with one row per arc carrying positive flow and
        the total ``value``.

    Raises:
        ModelValidationError: If an arc row is malformed.
    """
    from ortools.graph.python import max_flow as mf

    require(len(arcs) > 0, "arcs is required and must be non-empty")
    solver = mf.SimpleMaxFlow()
    for k, a in enumerate(arcs):
        require(len(a) == 3, f"arcs[{k}] must be [from, to, capacity]")
        solver.add_arc_with_capacity(int(a[0]), int(a[1]), int(a[2]))

    status = solver.solve(int(source), int(sink))
    result = MaxFlowResult()
    if status != solver.OPTIMAL:
        result.status = INFEASIBLE if status == solver.INFEASIBLE else UNKNOWN
        return result
    result.value = int(solver.optimal_flow())
    for arc in range(solver.num_arcs()):
        f = solver.flow(arc)
        if f <= 0:
            continue
        result.src.append(solver.tail(arc))
        result.dst.append(solver.head(arc))
        result.flow.append(f)
    return result
