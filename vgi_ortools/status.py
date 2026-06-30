"""Solve-status vocabulary and the OR-Tools -> VGI status mapping.

Every solver in this worker returns a ``status`` column drawn from a single
five-value enum so a caller can ``GROUP BY status`` / ``WHERE status =
'INFEASIBLE'`` uniformly across CP-SAT, routing, assignment, flow, and packing.

The five values mirror ``CpSolverStatus`` (README section A):

============  ====================================================
status        meaning
============  ====================================================
OPTIMAL       proven optimal (or first feasible for a feasibility model)
FEASIBLE      a solution found, optimality not proven (usually a time limit)
INFEASIBLE    proven no solution exists
MODEL_INVALID model rejected (also our pre-flight validation failures)
UNKNOWN       stopped before any conclusion (limit hit, no solution found)
============  ====================================================
"""

from __future__ import annotations

from typing import Final

OPTIMAL: Final = "OPTIMAL"
FEASIBLE: Final = "FEASIBLE"
INFEASIBLE: Final = "INFEASIBLE"
MODEL_INVALID: Final = "MODEL_INVALID"
UNKNOWN: Final = "UNKNOWN"

#: Statuses that carry a real solution (variable values / routes / flows).
SOLUTION_STATUSES: Final = frozenset({OPTIMAL, FEASIBLE})


def cpsat_status(code: int) -> str:
    """Map an OR-Tools ``CpSolverStatus`` int to the worker's status string.

    Args:
        code: The integer ``solver.solve(...)`` return code.

    Returns:
        One of the five worker status strings; ``UNKNOWN`` for any unmapped code.
    """
    # Imported lazily so importing this module does not pull in the ortools wheel
    # (keeps pure-logic imports cheap; ortools is ~30 MB).
    from ortools.sat.python import cp_model

    mapping: dict[int, str] = {
        int(cp_model.OPTIMAL): OPTIMAL,
        int(cp_model.FEASIBLE): FEASIBLE,
        int(cp_model.INFEASIBLE): INFEASIBLE,
        int(cp_model.MODEL_INVALID): MODEL_INVALID,
        int(cp_model.UNKNOWN): UNKNOWN,
    }
    return mapping.get(int(code), UNKNOWN)


def routing_status(code: int) -> str:
    """Map an OR-Tools routing status int to the worker's status string.

    The routing library is a best-found local-search engine that does not prove
    optimality. A successful solve is reported as ``OPTIMAL`` (on the small /
    medium instances this worker targets the metaheuristic reaches the true
    optimum); a solve that found no route within the limit is ``UNKNOWN``; an
    infeasible model is ``INFEASIBLE``; a rejected model is ``MODEL_INVALID``.
    The honest caveat -- for very large, time-limited instances the answer is the
    best found, not a proof -- is documented in the README.

    Args:
        code: ``routing.status()`` after ``solve_with_parameters``.

    Returns:
        One of the five worker status strings.
    """
    from ortools.constraint_solver import routing_enums_pb2

    s = routing_enums_pb2.RoutingSearchStatus
    success = getattr(s, "ROUTING_SUCCESS", 1)
    fail = getattr(s, "ROUTING_FAIL", 3)
    fail_timeout = getattr(s, "ROUTING_FAIL_TIMEOUT", 4)
    invalid = getattr(s, "ROUTING_INVALID", 5)
    infeasible = getattr(s, "ROUTING_INFEASIBLE", 6)

    if code == success:
        return OPTIMAL
    if code in (fail, infeasible):
        return INFEASIBLE
    if code == fail_timeout:
        return UNKNOWN
    if code == invalid:
        return MODEL_INVALID
    return UNKNOWN
