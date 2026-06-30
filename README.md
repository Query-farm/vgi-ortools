# vgi-ortools — Optimization & Constraint Solving in SQL

![Query Farm](https://query.farm/logo.png)

**Prescriptive compute inside DuckDB: CP-SAT constraint programming, vehicle
routing, scheduling, assignment, and network flow as plain SQL functions, backed
by [Google OR-Tools](https://developers.google.com/optimization).**

A [VGI](https://query.farm) worker that exposes OR-Tools — the CP-SAT constraint
solver, the routing library (TSP / VRP with capacity / time-window /
pickup-delivery), the linear assignment solver, scheduling (job-shop / RCPSP),
and min-cost / max-flow — as DuckDB table functions. You build the cost /
distance / capacity matrices with ordinary SQL joins, hand them to a solver
function, and get the optimal (or best-found) solution back as rows you can join
straight back into your warehouse.

This is *prescriptive* compute (decide what to do), the complement to the
predictive ML workers (`predict` / `xgboost` / `lightgbm`) that say what will
happen — and the in-engine capability the `highs` community extension (LP / MIP)
stops short of: **constraint programming, routing, and scheduling**.

## Why this exists (honest framing)

The defensibility is exactly the thing DuckDB cannot do natively and the thing
`highs` stops short of: CP-SAT constraint programming, vehicle routing, and
scheduling — global constraints (`AllDifferent`, `NoOverlap`, `Cumulative`,
`Circuit`), time windows, pickup-delivery. The *solver* is free (Apache-2.0,
`pip install ortools`); what you pay for is **not having to build and operate the
plumbing** — turning relational data (orders, vehicles, depots, shifts, demand)
into a correctly-shaped model, running the solve next to the data, and joining
the result back, without a separate Python optimization microservice, its own
deploy, its own data copy, and the ETL round-trip.

**Data residency / egress: none.** This worker makes **no outbound network
calls** — pure local CPU. No API keys, no ToS, no rate limits, no data leaves the
host. Your routing problem (which encodes your customers and depots) never leaves
the box.

## SQL surface

```sql
INSTALL vgi FROM community;
LOAD vgi;
-- worker binary resolved from the vgi community extension; no secrets needed (offline compute)
ATTACH 'vgi:ortools' AS opt;

-- 1) Linear assignment: 3 workers to 3 tasks, minimize cost
SELECT worker, task, cost FROM opt.solve_assignment(
  cost := (SELECT array_agg([w, t, c]) FROM costs),   -- [worker, task, cost] triples
  maximize := false
);

-- 2) Capacitated VRP: 1 depot, 12 stops, 4 vehicles, cap 15, from a SQL distance matrix
SELECT route_id, seq, node, arrival, load_0 AS load
FROM opt.solve_vrp(
  distance := (SELECT array_agg([from_node, to_node, meters]) FROM dist_edges),
  demand   := (SELECT array_agg([node, units])               FROM stop_demand),
  num_vehicles := 4,
  vehicle_capacity := 15,
  depot := 0,
  first_solution_strategy := 'PATH_CHEAPEST_ARC',
  local_search_metaheuristic := 'GUIDED_LOCAL_SEARCH',
  time_limit_s := 10.0
)
ORDER BY route_id, seq;

-- 3) CP-SAT job-shop: minimize makespan over a tasks table, return start times
SELECT job, task, machine, start, "end"
FROM opt.solve_jobshop(
  tasks := (SELECT array_agg([job, task, machine, duration]) FROM jobshop_tasks),
  time_limit_s := 30.0
);

-- 4) The general escape hatch: a hand-built CP-SAT model as JSON
SELECT var, value, status, objective
FROM opt.solve_cpsat(model := $${ ...JSON dialect, see below... }$$, time_limit_s := 5.0);
```

### Passing matrices: `array_agg` LIST arguments

Every matrix / vector argument is a DuckDB `LIST` value built with
`array_agg([...])` (sparse triples) or a list literal (`[[...], ...]::BIGINT[][]`).
All distances / costs / demands / durations are **integers** — OR-Tools is an
integer engine (see *Integer scaling* below).

> **DuckDB tip.** A table function cannot take a *scalar subquery* argument
> directly, so if `cost := (SELECT array_agg(...) FROM t)` is rejected by the
> binder, hoist it to a session variable first — the same idiom the other VGI
> workers use:
>
> ```sql
> SET VARIABLE cost = (SELECT array_agg([w, t, c]) FROM costs);
> SELECT * FROM opt.solve_assignment(cost := getvariable('cost'), maximize := false);
> ```
>
> Inline list literals (`[[0,0,90], ...]::BIGINT[][]`) always work as arguments.

## Function catalog

Every function returns a `status` column drawn from one five-value enum
(`OPTIMAL` / `FEASIBLE` / `INFEASIBLE` / `MODEL_INVALID` / `UNKNOWN`). A solve
with no solution returns **exactly one row** carrying the status (with NULL data
columns) — never an empty result set, so callers test
`WHERE status = 'INFEASIBLE'`, never `count(*) = 0`.

| Function | Purpose |
|---|---|
| `solve_cpsat(model, time_limit_s, num_workers, random_seed, enumerate, project)` | General CP-SAT model from the JSON dialect → `(var, value, status, objective, best_bound, wall_time, num_branches)` |
| `solve_assignment(cost, maximize)` | Linear sum assignment → `(worker, task, cost, status)` |
| `solve_tsp(distance, start, time_limit_s, …)` | Travelling-salesperson tour → `(seq, node, arrival, status)` |
| `solve_vrp(distance, demand, num_vehicles, vehicle_capacity, depot, time_windows, service_time, travel_time, pickup_delivery, allow_dropping, drop_penalty, first_solution_strategy, local_search_metaheuristic, span_cost_coefficient, time_limit_s, lns_time_limit_s, solution_limit, random_seed, settings_json)` | Vehicle routing → `(route_id, seq, node, arrival, departure, load_0…load_k, is_dropped, status)` |
| `solve_jobshop(tasks, horizon, time_limit_s)` | Job-shop scheduling → `(job, task, machine, start, "end", makespan, status)` |
| `solve_rcpsp(tasks, precedences, resources, time_limit_s)` | Resource-constrained project scheduling → `(task, start, "end", makespan, status)` |
| `min_cost_flow(arcs, supplies)` | Minimum-cost flow → `(from, to, flow, cost, status)` |
| `max_flow(arcs, source, sink)` | Maximum flow → `(from, to, flow, status)` |
| `max_flow_value(arcs, source, sink)` | Scalar maximum-flow value → `BIGINT` |
| `solve_knapsack(item_values, weights, capacities)` | Multi-dimensional 0/1 knapsack → `(item, chosen, status)` |
| `solve_bin_packing(sizes, bin_capacity, …)` | 1-D bin packing → `(item, bin, status)` |
| `solve_bin_packing_bins(sizes, bin_capacity, …)` | Per-bin rollup → `(bin, used, status)` |
| `solver_info()` | OR-Tools / worker / dialect versions → `(component, version)` |
| `last_solve_stats()` | Stats of the most recent solve → `(metric, value)` |

> The knapsack value argument is named `item_values` (not `values`) because
> `values` collides with the SQL `VALUES` keyword in DuckDB's named-argument
> position.

## A. The `solve_cpsat` JSON model dialect

`solve_cpsat` is the general escape hatch: a stable, versioned JSON dialect that
deserializes one-to-one onto `ortools.sat.python.cp_model.CpModel`. Everything
the per-domain builders produce internally is expressible in this dialect. The
dialect is **declared, versioned, and validated** before any model object is
touched: a malformed node is a clean `MODEL_INVALID`-class error returned to SQL
naming the offending JSON path — never a Python traceback.

### Top-level document

```jsonc
{
  "version": 1,                 // dialect version; worker rejects unknown majors
  "variables": [ <Var>, ... ],  // every var has a unique "name" used everywhere else
  "constraints": [ <Constraint>, ... ],
  "objective": <Objective>      // optional; omit for a pure feasibility model
}
```

All numeric domains are **integers**. A `LinearExpr` operand is referenced by
`{"var": "<name>"}`, an integer literal by `{"const": k}`, or an affine
combination by `{"sum": [{"coef": c, "var": "x"}, ...], "offset": k}`. A *literal*
(for reification / boolean constraints) is a bool var name, or its negation as
`{"not": "<bool_name>"}`.

### Variable nodes (`variables[]`)

| `kind` | Fields | Maps to |
|---|---|---|
| `int` | `name`, `lb`, `ub` | `new_int_var(lb, ub, name)` |
| `int` (sparse) | `name`, `domain`: `[[lo,hi],...]` | `new_int_var_from_domain(...)` |
| `bool` | `name` | `new_bool_var(name)` |
| `interval` | `name`, `start`, `size`, `end` (any two of three may be exprs) | `new_interval_var(start, size, end, name)` |
| `interval` (optional) | `name`, `start`, `size`, `end`, `presence`: `<literal>` | `new_optional_interval_var(...)` |

### Constraint nodes (`constraints[]`)

Every constraint may carry `"enforce": [<literal>, ...]` (half-reification,
`.only_enforce_if(...)`) — accepted on `linear`, `bool_or`, `bool_and`,
`bool_xor`; rejected on the rest.

| `type` | Fields |
|---|---|
| `linear` | `expr`, `op` (`<=` `==` `>=` `!=` `<` `>`), `rhs` int **or** `bounds`: `[lo,hi]` |
| `all_different` | `vars`: `[<expr>,...]` |
| `element` | `index`, `vars`, `target` |
| `table` | `vars`, `tuples`, `allowed` (default true) |
| `no_overlap` | `intervals`: `[<interval name>,...]` |
| `cumulative` | `intervals`, `demands`, `capacity` |
| `circuit` | `arcs`: `[[tail, head, <literal>],...]` |
| `reservoir` | `times`, `level_changes`, `min_level`, `max_level`, `actives?` |
| `mult_equality` | `target`, `factors`: `[<expr>,<expr>]` |
| `div_equality` | `target`, `num`, `den` |
| `abs_equality` | `target`, `expr` |
| `min_equality` / `max_equality` | `target`, `exprs`: `[...]` |
| `bool_or` / `bool_and` / `bool_xor` | `literals`: `[<literal>,...]` |
| `implication` | `a`: `<literal>`, `b`: `<literal>` |

### Objective node

```jsonc
"objective": {
  "sense": "minimize" | "maximize",
  "terms":  [ {"coef": c, "var": "x"}, ... ],   // a LinearExpr
  "offset": 0                                    // optional integer
}
```

### Result rows

`solve_cpsat` returns **one row per variable**, plus the solve metadata repeated
on every row: `var`, `value`, `status`, `objective`, `best_bound`, `wall_time`,
`num_branches`. Interval variables expand to `<name>.start` / `.size` / `.end`
(and `.presence` for optional intervals). The `project := 'x,y,z'` argument (a
comma-separated list) restricts the emitted variable set.

### Worked example — a 3-job single-machine no-overlap schedule minimizing makespan

```json
{
  "version": 1,
  "variables": [
    {"kind":"int","name":"s0","lb":0,"ub":20}, {"kind":"int","name":"e0","lb":0,"ub":20},
    {"kind":"int","name":"s1","lb":0,"ub":20}, {"kind":"int","name":"e1","lb":0,"ub":20},
    {"kind":"int","name":"s2","lb":0,"ub":20}, {"kind":"int","name":"e2","lb":0,"ub":20},
    {"kind":"interval","name":"i0","start":{"var":"s0"},"size":{"const":3},"end":{"var":"e0"}},
    {"kind":"interval","name":"i1","start":{"var":"s1"},"size":{"const":5},"end":{"var":"e1"}},
    {"kind":"interval","name":"i2","start":{"var":"s2"},"size":{"const":2},"end":{"var":"e2"}},
    {"kind":"int","name":"makespan","lb":0,"ub":20}
  ],
  "constraints": [
    {"type":"no_overlap","intervals":["i0","i1","i2"]},
    {"type":"max_equality","target":{"var":"makespan"},"exprs":[{"var":"e0"},{"var":"e1"},{"var":"e2"}]}
  ],
  "objective": {"sense":"minimize","terms":[{"coef":1,"var":"makespan"}],"offset":0}
}
```

Returns `makespan = 3 + 5 + 2 = 10` with one valid packing.

## Integer scaling (hard rule)

OR-Tools routing and CP-SAT are integer engines. Any non-integer value in a
distance / cost / demand / capacity / duration position is rejected pre-flight as
a clean error naming the offending value and the fix — *"distance 12.4 is not an
integer — scale your floats to integers (e.g. pass meters not kilometres, cents
not dollars) before solving."* We do **not** silently round, because rounding
changes the optimum. (The one place floats are legal in output is the
`objective` / `best_bound` / `wall_time` columns, which are solver-reported
doubles.)

## Time limits, size guards, determinism

- Every solver takes a mandatory `time_limit_s` (default 10s) **clamped** to an
  ATTACH-configurable hard cap (`max_time_limit_s`, default 60s) so a worker
  thread can't be pinned forever.
- Before building a model the worker guards problem size: `max_nodes` (routing,
  2000), `max_vars` / `max_constraints` (CP-SAT, 100k / 200k), `max_arcs`
  (flow). Exceeding a guard is a clean error naming the limit, not an OOM. Raise
  any of them per ATTACH, e.g. `ATTACH 'vgi:ortools' AS opt (max_nodes '5000')`.
- **Determinism.** CP-SAT runs single-threaded by default (`num_workers := 0`)
  with a fixed `random_seed`, so the objective *and* the assignment are
  reproducible. Pass `num_workers := 8` to opt into parallel search (faster, but
  the assignment may tie-break differently). Routing determinism is governed by
  the fixed `first_solution_strategy` / `local_search_metaheuristic`.

## No secrets, no scan-state (proxy note)

This is offline compute: the catalog declares **no secret requirements** (the
proxy never prompts for a credential) and **no externalized scan-state** (the
proxy never tries to serialize/resume a cursor). Each table-function call is a
single self-contained solve.

## Install / run

```sh
uv sync --extra dev          # install the worker + dev tools
uv run --no-sync pytest -q   # unit + golden + RPC tests
make test-sql                # haybarn sqllogictest E2E over the vgi extension
```

The worker ships the `vgi-ortools` (stdio) and `vgi-ortools-http` (HTTP) console
scripts; DuckDB spawns the stdio one after `ATTACH 'vgi:ortools'`.

## Dependencies & licensing

| Dep | License | Notes |
|---|---|---|
| `ortools` (PyPI, 9.15.x) | Apache-2.0 | Prebuilt CP-SAT / GLOP / PDLP / routing in the wheel (~30 MB — the largest single dep in the Python fleet; pinned, no extras). |
| `pyarrow` | Apache-2.0 | SDK transport. |
| vgi-python SDK | Query Farm | Worker framework. |

No GPL / AGPL / commercial-data-license anywhere. **Worker license: MIT** (see
`LICENSE`). OR-Tools and the solvers bundled in the wheel are Apache-2.0 / permissive.

## Pairs with

`geocode` + `geo-utils` / `proj` (build the VRP distance matrix from real
coordinates), `predict` / `lightgbm` / `xgboost` (forecast demand → feed as VRP
`demand` / scheduling load), `quant` / `trading` (portfolio + cardinality
constraints via CP-SAT), and the `highs` community extension (delegate pure LP /
MIP; vgi-ortools owns CP / routing / scheduling). The canonical demo: `geocode`
→ SQL distance join → `solve_vrp` → route rows back into the warehouse, all in
one query, no service.
