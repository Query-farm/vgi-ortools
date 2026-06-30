"""Linear sum assignment via ``SimpleLinearSumAssignment``.

Matches each worker to at most one task minimizing (or maximizing) total cost
over a sparse set of allowed ``[worker, task, cost]`` arcs.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from ..status import INFEASIBLE, OPTIMAL
from ..validate import require


@dataclass(slots=True)
class AssignmentResult:
    """Per-match rows (worker, task, cost) plus a status."""

    worker: list[int] = field(default_factory=list)
    task: list[int] = field(default_factory=list)
    cost: list[int] = field(default_factory=list)
    status: str = OPTIMAL


def solve(cost: Sequence[Sequence[int]], *, maximize: bool = False) -> AssignmentResult:
    """Solve a linear assignment over sparse ``[worker, task, cost]`` triples.

    Args:
        cost: Sparse arcs ``[[worker, task, cost], ...]``; a missing pair is a
            disallowed assignment.
        maximize: If true, maximize total cost (costs are negated internally).

    Returns:
        An :class:`AssignmentResult`; ``status='INFEASIBLE'`` when no perfect
        matching of all workers exists.

    Raises:
        ModelValidationError: If a triple is malformed.
    """
    from ortools.graph.python import linear_sum_assignment

    require(len(cost) > 0, "cost is required and must be non-empty ([[worker, task, cost], ...])")

    solver = linear_sum_assignment.SimpleLinearSumAssignment()
    for k, row in enumerate(cost):
        require(len(row) == 3, f"cost[{k}] must be a [worker, task, cost] triple")
        w, t, c = int(row[0]), int(row[1]), int(row[2])
        require(w >= 0 and t >= 0, f"cost[{k}] has a negative worker/task id")
        solver.add_arc_with_cost(w, t, -c if maximize else c)

    status = solver.solve()
    result = AssignmentResult()
    if status != solver.OPTIMAL:
        result.status = INFEASIBLE
        return result

    for i in range(solver.num_nodes()):
        result.worker.append(i)
        result.task.append(solver.right_mate(i))
        c = solver.assignment_cost(i)
        result.cost.append(-c if maximize else c)
    return result
