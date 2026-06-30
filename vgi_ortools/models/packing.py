"""Knapsack (multi-dimension branch-and-bound) and CP-SAT bin packing."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from ..limits import DEFAULT_TIME_LIMIT_S, Limits
from ..status import INFEASIBLE, SOLUTION_STATUSES, cpsat_status
from ..validate import require


@dataclass(slots=True)
class KnapsackResult:
    """Per-item chosen rows plus a status and total packed value."""

    item: list[int] = field(default_factory=list)
    chosen: list[bool] = field(default_factory=list)
    status: str = "OPTIMAL"
    value: int = 0


@dataclass(slots=True)
class BinPackingResult:
    """Per-item bin assignment, the per-bin rollup, plus a status."""

    item: list[int] = field(default_factory=list)
    bin: list[int] = field(default_factory=list)
    status: str = "OPTIMAL"
    # bin -> count used (only bins that hold >=1 item), for the rollup table fn.
    bin_used: dict[int, int] = field(default_factory=dict)


def solve_knapsack(
    values: Sequence[int],
    weights: Sequence[Sequence[int]],
    capacities: Sequence[int],
) -> KnapsackResult:
    """Solve a multi-dimensional 0/1 knapsack.

    Args:
        values: Per-item values ``[v0, v1, ...]``.
        weights: Per-item, per-dimension weights ``[[w0_d0, w0_d1, ...], ...]``.
        capacities: Per-dimension capacities ``[c_d0, c_d1, ...]``.

    Returns:
        A :class:`KnapsackResult`, one row per item with ``chosen``.

    Raises:
        ModelValidationError: If the shapes are inconsistent.
    """
    from ortools.algorithms.python import knapsack_solver as ks

    require(len(values) > 0, "values is required and must be non-empty")
    require(len(weights) == len(values), "weights must have one row per item")
    n_dims = len(capacities)
    require(n_dims > 0, "capacities is required and must be non-empty")
    # KnapsackSolver wants weights as [dimension][item].
    by_dim: list[list[int]] = [[0] * len(values) for _ in range(n_dims)]
    for i, row in enumerate(weights):
        require(len(row) == n_dims, f"weights[{i}] must have one weight per capacity dimension ({n_dims})")
        for d in range(n_dims):
            by_dim[d][i] = int(row[d])

    solver = ks.KnapsackSolver(
        ks.KNAPSACK_MULTIDIMENSION_BRANCH_AND_BOUND_SOLVER,
        "vgi_ortools_knapsack",
    )
    solver.init([int(v) for v in values], by_dim, [int(c) for c in capacities])
    total = solver.solve()
    result = KnapsackResult(value=int(total))
    for i in range(len(values)):
        result.item.append(i)
        result.chosen.append(bool(solver.best_solution_contains(i)))
    return result


def solve_bin_packing(
    sizes: Sequence[int],
    *,
    bin_capacity: int,
    time_limit_s: float | None = DEFAULT_TIME_LIMIT_S,
    random_seed: int = 1,
    limits: Limits | None = None,
) -> BinPackingResult:
    """Pack items into the fewest bins of a fixed capacity (CP-SAT).

    Args:
        sizes: Per-item sizes ``[s0, s1, ...]``.
        bin_capacity: The capacity of each (identical) bin.
        time_limit_s: Wall-clock cap (clamped to the worker hard cap).
        random_seed: Fixed seed for determinism.
        limits: Resolved size / time limits.

    Returns:
        A :class:`BinPackingResult` with the item->bin assignment and per-bin
        rollup; ``status='INFEASIBLE'`` only if an item exceeds ``bin_capacity``.

    Raises:
        ModelValidationError: If the inputs are malformed.
    """
    from ortools.sat.python import cp_model

    limits = limits or Limits()
    require(len(sizes) > 0, "sizes is required and must be non-empty")
    require(bin_capacity > 0, "bin_capacity must be positive")
    sizes = [int(s) for s in sizes]
    require(all(0 <= s <= bin_capacity for s in sizes), "every item size must be in [0, bin_capacity]")

    n = len(sizes)
    max_bins = n  # worst case: one item per bin.
    model = cp_model.CpModel()
    x = {(i, b): model.new_bool_var(f"x_{i}_{b}") for i in range(n) for b in range(max_bins)}
    used = [model.new_bool_var(f"used_{b}") for b in range(max_bins)]

    for i in range(n):
        model.add_exactly_one(x[(i, b)] for b in range(max_bins))
    for b in range(max_bins):
        model.add(sum(sizes[i] * x[(i, b)] for i in range(n)) <= bin_capacity * used[b])
    # Symmetry break: bin b can only be used if bin b-1 is used.
    for b in range(1, max_bins):
        model.add(used[b] <= used[b - 1])
    model.minimize(sum(used))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = limits.clamp_time(time_limit_s)
    solver.parameters.random_seed = int(random_seed)
    solver.parameters.num_workers = 1
    status = cpsat_status(int(solver.solve(model)))

    result = BinPackingResult(status=status)
    if status not in SOLUTION_STATUSES:
        result.status = INFEASIBLE if status == "INFEASIBLE" else status
        return result
    for i in range(n):
        for b in range(max_bins):
            if solver.value(x[(i, b)]):
                result.item.append(i)
                result.bin.append(b)
                result.bin_used[b] = result.bin_used.get(b, 0) + 1
                break
    return result
