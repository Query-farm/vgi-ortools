# CLAUDE.md — vgi-ortools

Context and conventions for working on this repo. Read this before changing code.

## What this is

A **VGI worker (Python, vgi-python SDK)** exposing Google OR-Tools as DuckDB SQL
functions in the `ortools` catalog: CP-SAT (`solve_cpsat`), routing
(`solve_tsp`, `solve_vrp`), assignment, scheduling (`solve_jobshop`,
`solve_rcpsp`), flow (`min_cost_flow`, `max_flow`, scalar `max_flow_value`),
packing (`solve_knapsack`, `solve_bin_packing` + bin rollup), and introspection
(`solver_info`, `last_solve_stats`). Prescriptive compute, no network, pure local
CPU. Built to the conventions of the sibling Python workers `vgi-explain` /
`vgi-match` / `vgi-scikit-learn`.

## Layout

```
vgi_ortools/
  __init__.py        # __version__, DIALECT_VERSION
  worker.py          # Worker + catalog_interface + main/main_http entry points
  catalog.py         # the `ortools` Catalog: schema, registry, catalog/schema tags
  schema_utils.py    # field() helper (column comments)
  status.py          # the 5-value status enum + OR-Tools -> status mapping
  validate.py        # ModelValidationError + integer-scaling validation
  limits.py          # time-limit clamp + problem-size guards (ATTACH-configurable)
  arrow_io.py        # LIST<LIST<int>> marshaling, sparse/dense matrices, null status row
  stats.py           # last_solve_stats backing store (per-process)
  models/            # PURE solver builders (Arrow-free, VGI-free, unit-testable)
    cpsat.py         #   the JSON dialect -> CpModel (the load-bearing escape hatch)
    routing.py       #   TSP / VRP on the routing library
    assignment.py    #   SimpleLinearSumAssignment
    scheduling.py    #   job-shop / RCPSP on CP-SAT
    flow.py          #   SimpleMinCostFlow / SimpleMaxFlow
    packing.py       #   KnapsackSolver / CP-SAT bin packing
  functions/         # the SQL surface: one source TableFunctionGenerator per solver
    _meta.py         #   shared metadata tag builder + Arrow LIST types
tests/               # pure golden tests (test_models) + RPC end-to-end (test_client)
test/sql/            # haybarn sqllogictest E2E
ci/                  # run-integration.sh + preprocess-require.awk (transport matrix)
```

## The model-input design (load-bearing)

A VGI table function reserves its single subquery slot, so the whole problem is
passed as **scalar arguments**, exactly as the spec's SQL surface documents:

- **Matrices / vectors** are `LIST<LIST<BIGINT>>` (or `LIST<BIGINT>`) values built
  with `array_agg([...])` or inline list literals — `cost`, `distance`,
  `demand`, `arcs`, `tasks`, etc. Marshaled in `arrow_io.py`.
- **The CP-SAT model** travels as a JSON string (`solve_cpsat(model := '...')`) —
  the versioned dialect in `models/cpsat.py` / README section A. This is the
  load-bearing escape hatch; everything the per-domain builders produce is
  expressible in it.
- **Advanced VRP config** (per-vehicle / per-dimension capacities, per-vehicle
  depots) travels as a JSON string `settings_json` on `solve_vrp` — the
  proven "buffered config + JSON" idiom from `vgi-match`.

This round-trips through the real vgi community extension (the haybarn E2E asserts
it) and over subprocess / unix / http transports.

### The empty-vs-infeasible contract

Every solver returns a `status` column. A solve with **no solution** returns
**exactly one row** carrying `status` in {`INFEASIBLE`, `MODEL_INVALID`,
`UNKNOWN`} with NULL data columns — never an empty result set. See
`arrow_io.null_row`. Malformed *input* (a fractional distance, bad JSON, a bad
shape) raises a clean `ModelValidationError` → SQL error naming the value; a
genuine no-solution is a status row. Callers test `WHERE status='INFEASIBLE'`,
never `count(*)=0`.

## Conventions

- All distances / costs / demands / durations are **integers** (OR-Tools is an
  integer engine). `validate.ensure_int` rejects fractional floats pre-flight
  with the scaling guidance — never silently rounds.
- The pure `models/` builders are Arrow-free and VGI-free so they unit-test fast
  (`tests/test_models.py`); the `functions/` layer only marshals args, calls a
  builder, and emits rows.
- Scalar LIST arguments arrive as Python lists (`pa.Scalar.as_py()`); the SDK
  uses the **dataclass field name** as the SQL parameter name (so the knapsack
  value arg is the field `item_values`, not `values`, which collides with the
  SQL `VALUES` keyword).
- Every function carries vgi-lint metadata: `vgi.title` / `vgi.doc_llm` /
  `vgi.doc_md` / `vgi.keywords` / `vgi.result_columns_schema` (structured
  `{name,type,description}` derived from the Arrow output schema; `solve_vrp`
  uses `vgi.result_dynamic_columns_md` since its `load_*` columns vary) /
  `vgi.executable_examples` (fully catalog-qualified as `ortools.main.<fn>`)
  built via `functions/_meta.function_tags`. The OR-Tools docs deep link is
  appended inline to `vgi.doc_md` (the canonical `vgi.source_url` lives on the
  catalog only). The two introspection relations (`solver_info`,
  `last_solve_stats`) are browsable base **tables** (declared in `catalog.py`
  via `Table(function=...)`), not parameterless table functions.

## Threading / determinism (IMPORTANT)

CP-SAT defaults to **single-threaded** search (`num_workers := 0` →
`default_num_workers() == 1`) for a fully deterministic, reproducible objective
*and* assignment with a fixed `random_seed`. A caller can pass `num_workers := 8`
to opt into parallel search.

> On the macOS arm64 build host, OR-Tools' **multi-threaded** CP-SAT solve
> deadlocked (any `num_workers > 1` hung indefinitely, sandbox or not). The
> single-threaded default sidesteps this and is deterministic; parallel search is
> opt-in. If you change the default, test it on the target platform.

## Routing status

The routing library is a best-found local-search engine that does not prove
optimality. `routing_status` maps `ROUTING_SUCCESS` → `OPTIMAL` (on the small /
medium instances this targets the metaheuristic reaches the true optimum),
`FAIL`/`INFEASIBLE` → `INFEASIBLE`, `FAIL_TIMEOUT` → `UNKNOWN`, `INVALID` →
`MODEL_INVALID`. For very large time-limited instances the answer is the best
found, not a proof — documented in the README.

## Gates (all must be green)

```sh
uv run --no-sync ruff format --check .
uv run --no-sync ruff check .
uv run --no-sync mypy vgi_ortools/
uv run --no-sync pydoclint --config pyproject.toml vgi_ortools/
uv run --no-sync pytest -q
make test-sql            # haybarn sqllogictest E2E (needs the vgi community extension)
# vgi-lint at --fail-on info (Query-farm/vgi-lint-check@v1 in CI)
```

## Tests

- `tests/test_models.py` — golden fixtures with known optima (assignment 201,
  TSP 17, CVRP 6, job-shop 11, min-cost-flow 150, max-flow 30, knapsack 80,
  CP-SAT makespan 10), the INFEASIBLE and MODEL_INVALID contracts, determinism
  (re-solve → same objective), and the time-limit test (ft10 under 0.5s →
  `FEASIBLE`, proving the clamp).
- `tests/test_client.py` — drives the worker over the real `vgi.client.Client`
  RPC stack (subprocess), proving the LIST / JSON arguments and the dynamic VRP
  load-column schema marshal across Arrow.
- `test/sql/ortools.test` — haybarn sqllogictest over the signed vgi community
  extension; uses `LOAD vgi;` + `require-env VGI_ORTOOLS_WORKER` + `ATTACH`.

## License

MIT (`LICENSE`); OR-Tools is Apache-2.0. (The original spec text differed; the
fleet convention is MIT — applied here.)
