"""The SQL function surface of the ortools worker.

Every solver is a *source* table function whose entire problem is passed as
scalar arguments -- the matrices as ``LIST<LIST<BIGINT>>`` values built with
``array_agg([...])`` and the CP-SAT model / advanced VRP config as JSON
strings. There is no streamed table input: a VGI table function reserves its one
subquery slot, and the proven fleet idiom for handing a whole model to a solver
is a scalar LIST plus a JSON config (see CLAUDE.md). Each call is a
self-contained, stateless solve.

Functions are grouped by domain into submodules; this package assembles them
into the two registries the catalog consumes.
"""

from __future__ import annotations

from .assignment_fn import SolveAssignment
from .cpsat_fn import SolveCpsat
from .flow_fn import MaxFlow, MaxFlowValue, MinCostFlow
from .info_fn import LastSolveStats, SolverInfo
from .packing_fn import SolveBinPacking, SolveBinPackingBins, SolveKnapsack
from .routing_fn import SolveTsp, SolveVrp
from .scheduling_fn import SolveJobshop, SolveRcpsp

#: Table functions (every solver + introspection table fns).
TABLE_FUNCTIONS: list[type] = [
    SolveCpsat,
    SolveAssignment,
    SolveTsp,
    SolveVrp,
    SolveJobshop,
    SolveRcpsp,
    MinCostFlow,
    MaxFlow,
    SolveKnapsack,
    SolveBinPacking,
    SolveBinPackingBins,
    SolverInfo,
    LastSolveStats,
]

#: Scalar functions.
SCALAR_FUNCTIONS: list[type] = [
    MaxFlowValue,
]

ALL_FUNCTIONS: list[type] = [*TABLE_FUNCTIONS, *SCALAR_FUNCTIONS]
