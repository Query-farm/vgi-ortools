"""Job-shop and resource-constrained project scheduling (RCPSP) on CP-SAT.

Both build an interval per task, a no-overlap (job-shop, per machine) or
cumulative (RCPSP, per resource) constraint, precedence constraints, and
minimize the makespan. These are exactly the kinds of model the ``solve_cpsat``
JSON dialect (README section A) can express; the dedicated builders save the
caller from hand-writing the JSON.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from ..limits import Limits
from ..status import SOLUTION_STATUSES, cpsat_status
from ..validate import ModelValidationError, require
from .cpsat import default_num_workers


@dataclass(slots=True)
class JobShopResult:
    """Per-task scheduled rows for a job-shop, plus makespan + status."""

    job: list[int] = field(default_factory=list)
    task: list[int] = field(default_factory=list)
    machine: list[int] = field(default_factory=list)
    start: list[int] = field(default_factory=list)
    end: list[int] = field(default_factory=list)
    makespan: int | None = None
    status: str = "OPTIMAL"


@dataclass(slots=True)
class RcpspResult:
    """Per-task scheduled rows for an RCPSP, plus makespan + status."""

    task: list[int] = field(default_factory=list)
    start: list[int] = field(default_factory=list)
    end: list[int] = field(default_factory=list)
    makespan: int | None = None
    status: str = "OPTIMAL"


def solve_jobshop(
    tasks: Sequence[Sequence[int]],
    *,
    horizon: int | None = None,
    time_limit_s: float = 30.0,
    random_seed: int = 1,
    limits: Limits | None = None,
) -> JobShopResult:
    """Solve a job-shop scheduling problem minimizing makespan.

    Args:
        tasks: ``[[job, task, machine, duration], ...]``. Tasks of a job run in
            ``task`` order; one task per machine at a time.
        horizon: Optional scheduling horizon (upper bound on time); defaults to
            the sum of all durations.
        time_limit_s: Wall-clock cap (clamped to the worker hard cap).
        random_seed: Fixed seed for a deterministic makespan.
        limits: Resolved size / time limits.

    Returns:
        A :class:`JobShopResult` ordered by (job, task).

    Raises:
        ModelValidationError: If a task row is malformed.
    """
    from ortools.sat.python import cp_model

    limits = limits or Limits()
    require(len(tasks) > 0, "tasks is required and must be non-empty ([[job, task, machine, duration], ...])")
    rows: list[tuple[int, int, int, int]] = []
    for k, t in enumerate(tasks):
        require(len(t) == 4, f"tasks[{k}] must be [job, task, machine, duration]")
        job, tk, machine, dur = int(t[0]), int(t[1]), int(t[2]), int(t[3])
        require(dur >= 0, f"tasks[{k}] duration must be >= 0")
        rows.append((job, tk, machine, dur))

    if horizon is None:
        horizon = sum(d for *_, d in rows) or 1

    model = cp_model.CpModel()
    starts: dict[tuple[int, int], Any] = {}
    ends: dict[tuple[int, int], Any] = {}
    machine_intervals: dict[int, list[Any]] = {}
    for job, tk, machine, dur in rows:
        s = model.new_int_var(0, horizon, f"s_{job}_{tk}")
        e = model.new_int_var(0, horizon, f"e_{job}_{tk}")
        iv = model.new_interval_var(s, dur, e, f"i_{job}_{tk}")
        starts[(job, tk)] = s
        ends[(job, tk)] = e
        machine_intervals.setdefault(machine, []).append(iv)
    for ivs in machine_intervals.values():
        model.add_no_overlap(ivs)
    # Precedence within a job: task t must finish before task t+1 starts.
    by_job: dict[int, list[int]] = {}
    for job, tk, *_ in rows:
        by_job.setdefault(job, []).append(tk)
    for job, tks in by_job.items():
        ordered = sorted(set(tks))
        for a, b in zip(ordered, ordered[1:], strict=False):
            model.add(starts[(job, b)] >= ends[(job, a)])

    makespan = model.new_int_var(0, horizon, "makespan")
    model.add_max_equality(makespan, list(ends.values()))
    model.minimize(makespan)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = limits.clamp_time(time_limit_s, default=30.0)
    solver.parameters.random_seed = int(random_seed)
    solver.parameters.num_workers = default_num_workers()
    status = cpsat_status(int(solver.solve(model)))

    result = JobShopResult(status=status)
    if status not in SOLUTION_STATUSES:
        return result
    result.makespan = int(solver.value(makespan))
    for job, tk, machine, _dur in rows:
        result.job.append(job)
        result.task.append(tk)
        result.machine.append(machine)
        result.start.append(int(solver.value(starts[(job, tk)])))
        result.end.append(int(solver.value(ends[(job, tk)])))
    return result


def solve_rcpsp(
    tasks: Sequence[Sequence[int]],
    precedences: Sequence[Sequence[int]],
    resources: Sequence[Sequence[int]],
    *,
    time_limit_s: float = 30.0,
    random_seed: int = 1,
    limits: Limits | None = None,
) -> RcpspResult:
    """Solve a resource-constrained project scheduling problem (RCPSP).

    Args:
        tasks: ``[[task, duration, r0_demand, r1_demand, ...], ...]`` (one demand
            column per resource, in the order of ``resources``).
        precedences: ``[[pred, succ], ...]`` finish-to-start precedences.
        resources: ``[[resource, capacity], ...]``.
        time_limit_s: Wall-clock cap (clamped to the worker hard cap).
        random_seed: Fixed seed for a deterministic makespan.
        limits: Resolved size / time limits.

    Returns:
        A :class:`RcpspResult` ordered by task.

    Raises:
        ModelValidationError: If the inputs are malformed / inconsistent.
    """
    from ortools.sat.python import cp_model

    limits = limits or Limits()
    require(len(tasks) > 0, "tasks is required and must be non-empty")
    require(len(resources) > 0, "resources is required and must be non-empty")
    n_res = len(resources)
    capacities = {int(r[0]): int(r[1]) for r in resources}
    res_order = [int(r[0]) for r in resources]

    durations: dict[int, int] = {}
    demands: dict[int, list[int]] = {}
    for k, t in enumerate(tasks):
        require(
            len(t) == 2 + n_res, f"tasks[{k}] must be [task, duration, demand-per-resource...] ({2 + n_res} values)"
        )
        tid, dur = int(t[0]), int(t[1])
        require(dur >= 0, f"tasks[{k}] duration must be >= 0")
        durations[tid] = dur
        demands[tid] = [int(v) for v in t[2:]]

    horizon = sum(durations.values()) or 1
    model = cp_model.CpModel()
    starts: dict[int, Any] = {}
    ends: dict[int, Any] = {}
    intervals: dict[int, Any] = {}
    for tid, dur in durations.items():
        s = model.new_int_var(0, horizon, f"s_{tid}")
        e = model.new_int_var(0, horizon, f"e_{tid}")
        intervals[tid] = model.new_interval_var(s, dur, e, f"i_{tid}")
        starts[tid] = s
        ends[tid] = e

    for ridx, res in enumerate(res_order):
        cap = capacities[res]
        ivs = [intervals[tid] for tid in durations]
        dem = [demands[tid][ridx] for tid in durations]
        if any(d > 0 for d in dem):
            model.add_cumulative(ivs, dem, cap)

    for k, p in enumerate(precedences):
        require(len(p) == 2, f"precedences[{k}] must be [pred, succ]")
        pred, succ = int(p[0]), int(p[1])
        if pred not in starts or succ not in starts:
            raise ModelValidationError(f"precedences[{k}] references unknown task ({pred} -> {succ})")
        model.add(starts[succ] >= ends[pred])

    makespan = model.new_int_var(0, horizon, "makespan")
    model.add_max_equality(makespan, list(ends.values()))
    model.minimize(makespan)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = limits.clamp_time(time_limit_s, default=30.0)
    solver.parameters.random_seed = int(random_seed)
    solver.parameters.num_workers = default_num_workers()
    status = cpsat_status(int(solver.solve(model)))

    result = RcpspResult(status=status)
    if status not in SOLUTION_STATUSES:
        return result
    result.makespan = int(solver.value(makespan))
    for tid in sorted(durations):
        result.task.append(tid)
        result.start.append(int(solver.value(starts[tid])))
        result.end.append(int(solver.value(ends[tid])))
    return result
