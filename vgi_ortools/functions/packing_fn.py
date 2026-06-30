"""Packing functions: ``solve_knapsack`` and ``solve_bin_packing`` (+ bin rollup)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, ClassVar

import pyarrow as pa
from vgi.arguments import Arg
from vgi.metadata import FunctionExample
from vgi.table_function import (
    BindParams,
    ProcessParams,
    TableCardinality,
    TableFunctionGenerator,
    bind_fixed_schema,
    init_single_worker,
)
from vgi_rpc.rpc import OutputCollector

from .. import stats
from ..arrow_io import emit_one, flat_int_list, null_row, rows_of
from ..limits import Limits
from ..models import packing
from ..schema_utils import field as sfield
from ._meta import LIST_INT, LIST_LIST_INT, function_tags

_KNAPSACK_URL = "https://developers.google.com/optimization/pack/knapsack"
_BINPACK_URL = "https://developers.google.com/optimization/pack/bin_packing"

_KNAPSACK_SCHEMA = pa.schema(
    [
        sfield("item", pa.int64(), "Item index (0-based).", nullable=False),
        sfield("chosen", pa.bool_(), "True if the item is packed into the knapsack."),
        sfield("status", pa.string(), "OPTIMAL / FEASIBLE / INFEASIBLE.", nullable=False),
    ]
)
_BINPACK_SCHEMA = pa.schema(
    [
        sfield("item", pa.int64(), "Item index (0-based).", nullable=False),
        sfield("bin", pa.int64(), "Bin the item is assigned to (0-based)."),
        sfield("status", pa.string(), "OPTIMAL / FEASIBLE / INFEASIBLE.", nullable=False),
    ]
)
_BINS_SCHEMA = pa.schema(
    [
        sfield("bin", pa.int64(), "Bin index (only bins holding >= 1 item).", nullable=False),
        sfield("used", pa.int64(), "Number of items packed into the bin."),
        sfield("status", pa.string(), "OPTIMAL / FEASIBLE / INFEASIBLE.", nullable=False),
    ]
)

_KNAPSACK_EXAMPLES = [
    {
        "description": "Single-dimension 0/1 knapsack: pick items maximizing value within capacity 40 (optimum 80).",
        "sql": (
            "SELECT item, chosen FROM ortools.solve_knapsack("
            "item_values := [10,20,30,40,50]::BIGINT[], weights := [[5],[10],[15],[20],[25]]::BIGINT[][], "
            "capacities := [40]::BIGINT[]) ORDER BY item"
        ),
    },
    {
        "description": (
            "Two-dimensional knapsack: each item has a weight and a volume, packed under both a "
            "weight budget (50) and a volume budget (5) at once -- one `weights` column per dimension."
        ),
        "sql": (
            "SELECT item, chosen, status FROM ortools.solve_knapsack("
            "item_values := [60,100,120]::BIGINT[], weights := [[10,2],[20,3],[30,4]]::BIGINT[][], "
            "capacities := [50,5]::BIGINT[]) ORDER BY item"
        ),
    },
]
_BINPACK_EXAMPLES = [
    {
        "description": "Pack six items into the fewest bins of capacity 10 (optimum 2 bins).",
        "sql": (
            "SELECT count(DISTINCT bin) AS bins FROM ortools.solve_bin_packing("
            "sizes := [3,5,2,7,1,4]::BIGINT[], bin_capacity := 10, time_limit_s := 5.0)"
        ),
    }
]


@dataclass(slots=True, frozen=True)
class SolveKnapsackArgs:
    """Arguments for ``solve_knapsack``."""

    item_values: Annotated[
        list[Any] | None, Arg("item_values", default=None, arrow_type=LIST_INT, doc="Per-item values [v0, v1, ...].")
    ]
    weights: Annotated[
        list[Any] | None,
        Arg(
            "weights",
            default=None,
            arrow_type=LIST_LIST_INT,
            doc="Per-item per-dimension weights [[w0_d0, w0_d1, ...], ...].",
        ),
    ]
    capacities: Annotated[
        list[Any] | None,
        Arg("capacities", default=None, arrow_type=LIST_INT, doc="Per-dimension capacities [c_d0, ...]."),
    ]


@init_single_worker
@bind_fixed_schema
class SolveKnapsack(TableFunctionGenerator[SolveKnapsackArgs]):
    """Multi-dimensional 0/1 knapsack maximizing packed value."""

    FunctionArguments: ClassVar[type] = SolveKnapsackArgs
    FIXED_SCHEMA: ClassVar[pa.Schema] = _KNAPSACK_SCHEMA

    class Meta:
        """SQL-facing metadata for ``solve_knapsack``."""

        name = "solve_knapsack"
        description = (
            "Multi-dimensional 0/1 knapsack: choose items to maximize total value subject to one or "
            "more capacity dimensions. Backed by OR-Tools KnapsackSolver (branch and bound)."
        )
        categories = ["optimization", "packing", "knapsack"]
        keywords = [
            "knapsack",
            "0/1 knapsack",
            "multidimensional knapsack",
            "selection",
            "budget",
            "value maximization",
            "or-tools",
            "optimization",
        ]
        tags = function_tags(
            title="Knapsack (Value Maximization)",
            doc_llm=(
                "## `solve_knapsack`\n\n"
                "Choose a subset of items to **maximize total value** subject to one or more **capacity "
                "dimensions** (multi-dimensional 0/1 knapsack).\n\n"
                "**Inputs.** `item_values` = `[v0, v1, ...]`; `weights` = `[[w0_d0, w0_d1, ...], ...]` (one "
                "row per item, one column per capacity dimension); `capacities` = `[c_d0, c_d1, ...]`. "
                "**Outputs.** One row per item with `chosen` (BOOLEAN) and `status`."
            ),
            doc_md=(
                "# solve_knapsack\n\n"
                "Multi-dimensional 0/1 knapsack maximizing value.\n\n"
                "```sql\n"
                "SELECT item, chosen FROM ortools.solve_knapsack(\n"
                "  item_values := (SELECT array_agg(value) FROM items),\n"
                "  weights := (SELECT array_agg([weight]) FROM items),\n"
                "  capacities := [100]::BIGINT[]);\n"
                "```\n"
            ),
            keywords=keywords,
            result_columns_md=(
                "| column | type | description |\n"
                "| --- | --- | --- |\n"
                "| `item` | `BIGINT` | Item index (0-based). |\n"
                "| `chosen` | `BOOLEAN` | Whether the item is packed. |\n"
                "| `status` | `VARCHAR` | `OPTIMAL` / `FEASIBLE` / `INFEASIBLE`. |"
            ),
            source_url=_KNAPSACK_URL,
            executable_examples=_KNAPSACK_EXAMPLES,
        )
        examples = [FunctionExample(sql=_KNAPSACK_EXAMPLES[0]["sql"], description="0/1 knapsack, optimum value 80.")]

    @classmethod
    def cardinality(cls, params: BindParams[SolveKnapsackArgs]) -> TableCardinality:
        """Estimate the output cardinality (one row per item)."""
        return TableCardinality(estimate=16, max=1_000_000)

    @classmethod
    def process(cls, params: ProcessParams[SolveKnapsackArgs], state: None, out: OutputCollector) -> None:
        """Solve the knapsack and emit per-item rows."""
        a = params.args
        result = packing.solve_knapsack(
            flat_int_list(a.item_values, where="item_values"),
            rows_of(a.weights, where="weights"),
            flat_int_list(a.capacities, where="capacities"),
        )
        stats.record(status=result.status, objective=float(result.value))
        n = len(result.item)
        emit_one(
            out,
            {"item": result.item, "chosen": result.chosen, "status": [result.status] * n},
            _KNAPSACK_SCHEMA,
        )
        out.finish()


@dataclass(slots=True, frozen=True)
class SolveBinPackingArgs:
    """Arguments for ``solve_bin_packing`` (and its bin rollup)."""

    sizes: Annotated[
        list[Any] | None, Arg("sizes", default=None, arrow_type=LIST_INT, doc="Per-item sizes [s0, s1, ...].")
    ]
    bin_capacity: Annotated[int, Arg("bin_capacity", default=0, doc="Capacity of each identical bin.")]
    time_limit_s: Annotated[float, Arg("time_limit_s", default=10.0, doc="Wall-clock cap in seconds.")]
    random_seed: Annotated[int, Arg("random_seed", default=1, doc="Random seed for determinism.")]


@init_single_worker
@bind_fixed_schema
class SolveBinPacking(TableFunctionGenerator[SolveBinPackingArgs]):
    """1-D bin packing minimizing the number of open bins."""

    FunctionArguments: ClassVar[type] = SolveBinPackingArgs
    FIXED_SCHEMA: ClassVar[pa.Schema] = _BINPACK_SCHEMA

    class Meta:
        """SQL-facing metadata for ``solve_bin_packing``."""

        name = "solve_bin_packing"
        description = (
            "1-D bin packing: assign items to the fewest identical fixed-capacity bins. CP-SAT model "
            "minimizing the number of open bins. Returns the item -> bin assignment."
        )
        categories = ["optimization", "packing", "bin-packing"]
        keywords = [
            "bin packing",
            "bin packing problem",
            "fewest bins",
            "capacity",
            "packing",
            "containers",
            "or-tools",
            "cp-sat",
        ]
        tags = function_tags(
            title="Bin Packing (Fewest Bins)",
            doc_llm=(
                "## `solve_bin_packing`\n\n"
                "Pack items into the **fewest identical fixed-capacity bins** (1-D bin packing as a "
                "CP-SAT model).\n\n"
                "**Inputs.** `sizes` = `[s0, s1, ...]`; `bin_capacity` (scalar). **Outputs.** One row "
                "per item with the `bin` it is assigned to and `status`. Use "
                "`solve_bin_packing_bins(...)` for the per-bin rollup (`bin`, `used`)."
            ),
            doc_md=(
                "# solve_bin_packing\n\n"
                "Pack items into the fewest fixed-capacity bins.\n\n"
                "```sql\n"
                "SELECT item, bin FROM ortools.solve_bin_packing(\n"
                "  sizes := (SELECT array_agg(size) FROM items), bin_capacity := 100);\n"
                "```\n"
            ),
            keywords=keywords,
            result_columns_md=(
                "| column | type | description |\n"
                "| --- | --- | --- |\n"
                "| `item` | `BIGINT` | Item index (0-based). |\n"
                "| `bin` | `BIGINT` | Bin the item is packed into (0-based). |\n"
                "| `status` | `VARCHAR` | `OPTIMAL` / `FEASIBLE` / `INFEASIBLE`. |"
            ),
            source_url=_BINPACK_URL,
            executable_examples=_BINPACK_EXAMPLES,
        )
        examples = [FunctionExample(sql=_BINPACK_EXAMPLES[0]["sql"], description="Pack six items into 2 bins.")]

    @classmethod
    def cardinality(cls, params: BindParams[SolveBinPackingArgs]) -> TableCardinality:
        """Estimate the output cardinality (one row per item)."""
        return TableCardinality(estimate=16, max=1_000_000)

    @classmethod
    def process(cls, params: ProcessParams[SolveBinPackingArgs], state: None, out: OutputCollector) -> None:
        """Solve bin packing and emit the item -> bin assignment (or one status row)."""
        result = _binpack(params)
        stats.record(status=result.status, objective=float(len(result.bin_used)) if result.bin_used else None)
        if not result.item:
            emit_one(out, null_row(_BINPACK_SCHEMA, status=result.status), _BINPACK_SCHEMA)
        else:
            n = len(result.item)
            emit_one(out, {"item": result.item, "bin": result.bin, "status": [result.status] * n}, _BINPACK_SCHEMA)
        out.finish()


@init_single_worker
@bind_fixed_schema
class SolveBinPackingBins(TableFunctionGenerator[SolveBinPackingArgs]):
    """Per-bin rollup of a bin-packing solution: how many items each open bin holds."""

    FunctionArguments: ClassVar[type] = SolveBinPackingArgs
    FIXED_SCHEMA: ClassVar[pa.Schema] = _BINS_SCHEMA

    class Meta:
        """SQL-facing metadata for ``solve_bin_packing_bins``."""

        name = "solve_bin_packing_bins"
        description = (
            "Per-bin rollup of the bin-packing solution: one row per open bin with the count of items "
            "it holds. Same inputs as solve_bin_packing."
        )
        categories = ["optimization", "packing", "bin-packing"]
        keywords = ["bin packing", "rollup", "open bins", "bin utilization", "packing", "or-tools", "cp-sat"]
        tags = function_tags(
            title="Bin Packing (Per-Bin Rollup)",
            doc_llm=(
                "## `solve_bin_packing_bins`\n\n"
                "The **per-bin rollup** of a 1-D bin-packing solution: one row per open bin with `used` "
                "(the number of items packed into it). Same inputs as `solve_bin_packing` (`sizes`, "
                "`bin_capacity`)."
            ),
            doc_md=(
                "# solve_bin_packing_bins\n\n"
                "Per-bin rollup of a bin-packing solution.\n\n"
                "```sql\n"
                "SELECT bin, used FROM ortools.solve_bin_packing_bins(\n"
                "  sizes := [3,5,2,7,1,4]::BIGINT[], bin_capacity := 10) ORDER BY bin;\n"
                "```\n"
            ),
            keywords=keywords,
            result_columns_md=(
                "| column | type | description |\n"
                "| --- | --- | --- |\n"
                "| `bin` | `BIGINT` | Bin index (only bins holding >= 1 item). |\n"
                "| `used` | `BIGINT` | Number of items packed into the bin. |\n"
                "| `status` | `VARCHAR` | `OPTIMAL` / `FEASIBLE` / `INFEASIBLE`. |"
            ),
            source_url=_BINPACK_URL,
            executable_examples=[
                {
                    "description": "Per-bin item counts for a six-item pack into capacity-10 bins.",
                    "sql": (
                        "SELECT bin, used FROM ortools.solve_bin_packing_bins("
                        "sizes := [3,5,2,7,1,4]::BIGINT[], bin_capacity := 10) ORDER BY bin"
                    ),
                }
            ],
        )
        examples = [
            FunctionExample(
                sql=(
                    "SELECT bin, used FROM ortools.solve_bin_packing_bins("
                    "sizes := [3,5,2,7,1,4]::BIGINT[], bin_capacity := 10) ORDER BY bin"
                ),
                description="Per-bin rollup of a six-item pack.",
            )
        ]

    @classmethod
    def cardinality(cls, params: BindParams[SolveBinPackingArgs]) -> TableCardinality:
        """Estimate the output cardinality (one row per open bin)."""
        return TableCardinality(estimate=8, max=1_000_000)

    @classmethod
    def process(cls, params: ProcessParams[SolveBinPackingArgs], state: None, out: OutputCollector) -> None:
        """Solve bin packing and emit the per-bin rollup (or one status row)."""
        result = _binpack(params)
        if not result.bin_used:
            emit_one(out, null_row(_BINS_SCHEMA, status=result.status), _BINS_SCHEMA)
        else:
            bins = sorted(result.bin_used)
            emit_one(
                out,
                {"bin": bins, "used": [result.bin_used[b] for b in bins], "status": [result.status] * len(bins)},
                _BINS_SCHEMA,
            )
        out.finish()


def _binpack(params: ProcessParams[SolveBinPackingArgs]) -> packing.BinPackingResult:
    """Run the shared bin-packing solve for both the assignment and rollup functions."""
    a = params.args
    limits = Limits.from_settings(getattr(params, "settings", None))
    return packing.solve_bin_packing(
        flat_int_list(a.sizes, where="sizes"),
        bin_capacity=int(a.bin_capacity),
        time_limit_s=a.time_limit_s,
        random_seed=a.random_seed,
        limits=limits,
    )
