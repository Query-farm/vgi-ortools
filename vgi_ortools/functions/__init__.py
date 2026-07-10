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

#: Callable table functions (every solver). The two introspection relations
#: (``solver_info`` / ``last_solve_stats``) are NOT here: they back browsable
#: base *tables* declared in :mod:`vgi_ortools.catalog` (VGI311/VGI146), and their
#: scan generators (:class:`SolverInfo` / :class:`LastSolveStats`) are exported
#: for that purpose rather than registered as parameterless table functions.
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
]

#: Scalar functions.
SCALAR_FUNCTIONS: list[type] = [
    MaxFlowValue,
]

#: The scan generators backing the introspection base tables (see the catalog).
INTROSPECTION_SCANS: list[type] = [SolverInfo, LastSolveStats]

ALL_FUNCTIONS: list[type] = [*TABLE_FUNCTIONS, *SCALAR_FUNCTIONS]
