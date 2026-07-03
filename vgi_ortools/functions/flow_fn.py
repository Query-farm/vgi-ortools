"""Flow functions: ``min_cost_flow`` / ``max_flow`` (table) + ``max_flow_value`` (scalar)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, ClassVar

import pyarrow as pa
from vgi.arguments import Arg, ConstParam, Param, Returns
from vgi.metadata import FunctionExample
from vgi.scalar_function import ScalarFunction
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
from ..arrow_io import emit_one, null_row, rows_of
from ..models import flow
from ..schema_utils import field as sfield
from ._meta import LIST_LIST_INT, function_tags

_MCF_URL = "https://developers.google.com/optimization/flow/mincostflow"
_MF_URL = "https://developers.google.com/optimization/flow/maxflow"

_MCF_SCHEMA = pa.schema(
    [
        sfield("from", pa.int64(), "Arc tail node.", nullable=False),
        sfield("to", pa.int64(), "Arc head node."),
        sfield("flow", pa.int64(), "Flow sent on this arc (rows with zero flow are omitted)."),
        sfield("cost", pa.int64(), "Flow times unit cost on this arc."),
        sfield("status", pa.string(), "OPTIMAL / INFEASIBLE / UNKNOWN.", nullable=False),
    ]
)
_MF_SCHEMA = pa.schema(
    [
        sfield("from", pa.int64(), "Arc tail node.", nullable=False),
        sfield("to", pa.int64(), "Arc head node."),
        sfield("flow", pa.int64(), "Flow sent on this arc (rows with zero flow are omitted)."),
        sfield("status", pa.string(), "OPTIMAL / INFEASIBLE / UNKNOWN.", nullable=False),
    ]
)

_MCF_EXAMPLES = [
    {
        "description": "Min-cost flow of 20 units from node 0 to node 4 (optimal total cost 150).",
        "sql": (
            'SELECT "from", "to", flow, cost FROM ortools.min_cost_flow('
            "arcs := [[0,1,15,4],[0,2,8,4],[1,2,20,2],[1,3,4,2],[2,3,15,1],[2,4,4,3],[3,4,20,2],"
            "[4,2,5,3]]::BIGINT[][], "
            'supplies := [[0,20],[4,-20]]::BIGINT[][]) ORDER BY "from", "to"'
        ),
    }
]
_MF_EXAMPLES = [
    {
        "description": "Max flow from node 0 to node 3 (value 30).",
        "sql": (
            'SELECT sum(flow) FILTER (WHERE "from" = 0) AS out_of_source FROM ortools.max_flow('
            "arcs := [[0,1,20],[0,2,30],[1,2,10],[1,3,10],[2,3,20]]::BIGINT[][], source := 0, sink := 3)"
        ),
    }
]


@dataclass(slots=True, frozen=True)
class MinCostFlowArgs:
    """Arguments for ``min_cost_flow``."""

    arcs: Annotated[
        list[Any] | None,
        Arg("arcs", default=None, arrow_type=LIST_LIST_INT, doc="[from, to, capacity, unit_cost] arcs of the network."),
    ]
    supplies: Annotated[
        list[Any] | None,
        Arg(
            "supplies",
            default=None,
            arrow_type=LIST_LIST_INT,
            doc="[node, supply] pairs (positive = source, negative = sink; must sum to 0).",
        ),
    ]


@init_single_worker
@bind_fixed_schema
class MinCostFlow(TableFunctionGenerator[MinCostFlowArgs]):
    """Minimum-cost flow over a capacitated, costed network."""

    FunctionArguments: ClassVar[type] = MinCostFlowArgs
    FIXED_SCHEMA: ClassVar[pa.Schema] = _MCF_SCHEMA

    class Meta:
        """SQL-facing metadata for ``min_cost_flow``."""

        name = "min_cost_flow"
        description = (
            "Minimum-cost flow: route supply to demand over a capacitated, costed network at minimum "
            "total cost. Backed by OR-Tools SimpleMinCostFlow. Returns one row per arc with positive flow."
        )
        categories = ["optimization", "flow", "network"]
        keywords = [
            "min cost flow",
            "minimum cost flow",
            "network flow",
            "transportation problem",
            "supply demand",
            "logistics",
            "or-tools",
            "optimization",
        ]
        tags = function_tags(
            title="Minimum-Cost Flow",
            category="Network Flow",
            doc_llm=(
                "## `min_cost_flow`\n\n"
                "Route flow from supply nodes to demand nodes over a **capacitated, costed network** so "
                "that total cost is minimized. Classic transportation / logistics optimization.\n\n"
                "**Inputs.** `arcs` = `[from, to, capacity, unit_cost]` integer arcs; `supplies` = "
                "`[node, supply]` (positive at sources, negative at sinks; the supplies **must sum to "
                "zero**). All values are integers.\n\n"
                "**Outputs.** One row per arc that carries positive flow: `from`, `to`, `flow`, `cost`, "
                "`status`. An infeasible instance returns a single `status='INFEASIBLE'` row."
            ),
            doc_md=(
                "# min_cost_flow\n\n"
                "Minimum-cost flow over a capacitated, costed network.\n\n"
                "```sql\n"
                'SELECT "from", "to", flow, cost FROM ortools.min_cost_flow('
                "arcs := (SELECT array_agg([f, t, cap, c]) FROM edges), "
                "supplies := (SELECT array_agg([node, s]) FROM nodes));\n"
                "```\n\n"
                "Supplies must sum to zero. Returns one row per arc with positive flow."
            ),
            keywords=keywords,
            result_columns_md=(
                "| column | type | description |\n"
                "| --- | --- | --- |\n"
                "| `from` | `BIGINT` | Arc tail node. |\n"
                "| `to` | `BIGINT` | Arc head node. |\n"
                "| `flow` | `BIGINT` | Flow on the arc (zero-flow arcs omitted). |\n"
                "| `cost` | `BIGINT` | `flow * unit_cost`. |\n"
                "| `status` | `VARCHAR` | `OPTIMAL` / `INFEASIBLE` / `UNKNOWN`. |"
            ),
            source_url=_MCF_URL,
            executable_examples=_MCF_EXAMPLES,
        )
        examples = [FunctionExample(sql=_MCF_EXAMPLES[0]["sql"], description="Min-cost flow, total cost 150.")]

    @classmethod
    def cardinality(cls, params: BindParams[MinCostFlowArgs]) -> TableCardinality:
        """Estimate the output cardinality (one row per arc)."""
        return TableCardinality(estimate=16, max=1_000_000)

    @classmethod
    def process(cls, params: ProcessParams[MinCostFlowArgs], state: None, out: OutputCollector) -> None:
        """Solve min-cost flow and emit per-arc flow rows (or one status row)."""
        a = params.args
        result = flow.min_cost_flow(rows_of(a.arcs, where="arcs"), rows_of(a.supplies, where="supplies"))
        stats.record(status=result.status, objective=float(sum(result.cost)) if result.cost else None)
        if not result.src:
            emit_one(out, null_row(_MCF_SCHEMA, status=result.status), _MCF_SCHEMA)
        else:
            emit_one(
                out,
                {
                    "from": result.src,
                    "to": result.dst,
                    "flow": result.flow,
                    "cost": result.cost,
                    "status": [result.status] * len(result.src),
                },
                _MCF_SCHEMA,
            )
        out.finish()


@dataclass(slots=True, frozen=True)
class MaxFlowArgs:
    """Arguments for ``max_flow``."""

    arcs: Annotated[
        list[Any] | None,
        Arg("arcs", default=None, arrow_type=LIST_LIST_INT, doc="[from, to, capacity] arcs of the network."),
    ]
    source: Annotated[int, Arg("source", default=0, doc="Source node id.")]
    sink: Annotated[int, Arg("sink", default=0, doc="Sink node id.")]


@init_single_worker
@bind_fixed_schema
class MaxFlow(TableFunctionGenerator[MaxFlowArgs]):
    """Maximum flow from a source to a sink over a capacitated network."""

    FunctionArguments: ClassVar[type] = MaxFlowArgs
    FIXED_SCHEMA: ClassVar[pa.Schema] = _MF_SCHEMA

    class Meta:
        """SQL-facing metadata for ``max_flow``."""

        name = "max_flow"
        description = (
            "Maximum flow from a source to a sink over a capacitated network. Backed by OR-Tools "
            "SimpleMaxFlow. Returns one row per arc with positive flow; see max_flow_value for the scalar total."
        )
        categories = ["optimization", "flow", "network"]
        keywords = [
            "max flow",
            "maximum flow",
            "min cut",
            "network flow",
            "throughput",
            "connectivity",
            "or-tools",
            "optimization",
        ]
        tags = function_tags(
            title="Maximum Flow",
            category="Network Flow",
            doc_llm=(
                "## `max_flow`\n\n"
                "Compute the **maximum flow** that can be pushed from `source` to `sink` over a "
                "capacitated network (the max-flow / min-cut value).\n\n"
                "**Inputs.** `arcs` = `[from, to, capacity]` integer arcs; `source` and `sink` node "
                "ids. **Outputs.** One row per arc with positive flow: `from`, `to`, `flow`, `status`. "
                "Use the scalar `max_flow_value(arcs, source, sink)` when you only need the total."
            ),
            doc_md=(
                "# max_flow\n\n"
                "Maximum flow from `source` to `sink` over a capacitated network.\n\n"
                "```sql\n"
                'SELECT "from", "to", flow FROM ortools.max_flow(\n'
                "  arcs := (SELECT array_agg([f, t, cap]) FROM edges), source := 0, sink := 5);\n"
                "```\n"
            ),
            keywords=keywords,
            result_columns_md=(
                "| column | type | description |\n"
                "| --- | --- | --- |\n"
                "| `from` | `BIGINT` | Arc tail node. |\n"
                "| `to` | `BIGINT` | Arc head node. |\n"
                "| `flow` | `BIGINT` | Flow on the arc (zero-flow arcs omitted). |\n"
                "| `status` | `VARCHAR` | `OPTIMAL` / `INFEASIBLE` / `UNKNOWN`. |"
            ),
            source_url=_MF_URL,
            executable_examples=_MF_EXAMPLES,
        )
        examples = [FunctionExample(sql=_MF_EXAMPLES[0]["sql"], description="Max flow value 30.")]

    @classmethod
    def cardinality(cls, params: BindParams[MaxFlowArgs]) -> TableCardinality:
        """Estimate the output cardinality (one row per arc)."""
        return TableCardinality(estimate=16, max=1_000_000)

    @classmethod
    def process(cls, params: ProcessParams[MaxFlowArgs], state: None, out: OutputCollector) -> None:
        """Solve max flow and emit per-arc flow rows (or one status row)."""
        a = params.args
        result = flow.max_flow(rows_of(a.arcs, where="arcs"), source=int(a.source), sink=int(a.sink))
        stats.record(status=result.status, objective=float(result.value))
        if not result.src:
            emit_one(out, null_row(_MF_SCHEMA, status=result.status), _MF_SCHEMA)
        else:
            emit_one(
                out,
                {
                    "from": result.src,
                    "to": result.dst,
                    "flow": result.flow,
                    "status": [result.status] * len(result.src),
                },
                _MF_SCHEMA,
            )
        out.finish()


class MaxFlowValue(ScalarFunction):
    """``max_flow_value(arcs, source, sink)`` -- the scalar max-flow value."""

    class Meta:
        """SQL-facing metadata for ``max_flow_value``."""

        name = "max_flow_value"
        description = (
            "The scalar maximum-flow value from source to sink over a capacitated network "
            "(arcs as a [[from, to, capacity], ...] LIST). Backed by OR-Tools SimpleMaxFlow."
        )
        categories = ["optimization", "flow", "network"]
        keywords = ["max flow", "maximum flow", "min cut", "network flow", "scalar", "or-tools", "optimization"]
        tags = function_tags(
            title="Maximum-Flow Value (Scalar)",
            category="Network Flow",
            doc_llm=(
                "## `max_flow_value`\n\n"
                "Return the **scalar maximum-flow value** from `source` to `sink` over a capacitated "
                "network, one value per input row. `arcs` is a `LIST<LIST<BIGINT>>` of "
                "`[from, to, capacity]` triples (build it with `array_agg([f, t, cap])`); `source` and "
                "`sink` are node ids. Use the `max_flow` table function for the per-arc flow."
            ),
            doc_md=(
                "# max_flow_value\n\n"
                "Scalar maximum-flow value.\n\n"
                "```sql\n"
                "SELECT ortools.max_flow_value(\n"
                "  [[0,1,20],[0,2,30],[1,2,10],[1,3,10],[2,3,20]]::BIGINT[][], 0, 3);  -- 30\n"
                "```\n"
            ),
            keywords=keywords,
            result_columns_md=(
                "Returns a single `BIGINT`: the maximum flow value from `source` to `sink` "
                "(`0` for an infeasible / disconnected instance)."
            ),
            source_url=_MF_URL,
            executable_examples=[
                {
                    "description": "Max-flow value from node 0 to node 3 (= 30).",
                    "sql": (
                        "SELECT ortools.max_flow_value("
                        "[[0,1,20],[0,2,30],[1,2,10],[1,3,10],[2,3,20]]::BIGINT[][], 0, 3)"
                    ),
                }
            ],
        )
        examples = [
            FunctionExample(
                sql=("SELECT ortools.max_flow_value([[0,1,20],[0,2,30],[1,2,10],[1,3,10],[2,3,20]]::BIGINT[][], 0, 3)"),
                description="Maximum flow value from node 0 to node 3 (= 30).",
            )
        ]

    @classmethod
    def compute(
        cls,
        arcs: Annotated[
            pa.ListArray, Param(arrow_type=LIST_LIST_INT, doc="[from, to, capacity] arcs, one network per row.")
        ],
        source: Annotated[int, ConstParam("Source node id.")],
        sink: Annotated[int, ConstParam("Sink node id.")],
    ) -> Annotated[pa.Int64Array, Returns()]:
        """Compute the max-flow value for each row's network."""
        out: list[int | None] = []
        for row in arcs.to_pylist():
            if row is None:
                out.append(None)
                continue
            result = flow.max_flow(rows_of(row, where="arcs"), source=int(source), sink=int(sink))
            stats.record(status=result.status, objective=float(result.value))
            out.append(result.value)
        return pa.array(out, type=pa.int64())
