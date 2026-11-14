# OR-Tools optimization notebooks

Three independent, executed investigations use the real released OR-Tools worker through visible Haybarn/VGI SQL:

| Notebook | Question | One-parameter experiment |
|---|---|---|
| [01 · Workload is not a completion time](01_workload_is_not_completion.ipynb) | Why can a batch outlast the busiest machine's workload? | A's first operation: 3 → 6 minutes |
| [02 · Shipping cost and capacity](02_shipping_cost_and_capacity.ipynb) | What does a requested shipment cost, and how much more can the network carry? | Requested shipment: 12 → 17 crates |
| [03 · Selecting is not packing everything](03_selecting_is_not_packing_everything.ipynb) | How does selecting one valuable subset differ from packing every item? | Container capacity: 10 → 12 kg |

The notebooks contain synthetic, explicitly modeled problems, not production observations. All optimizer calls are visible SQL. Python configures the connection, transfers SQL-created scalar input lists, and presents returned results. Each story has two SVG figures, interpreted tables, and invariants or independent reconciliations.

## Run locally or in Colab

Use the **Open in Colab** link at the top of a notebook, or download only the `.ipynb` file and run it in a fresh compatible notebook kernel. The bootstrap installs the shared **base** package from [v0.3.23](https://github.com/Query-farm/vgi-example-notebooks/tree/v0.3.23), preserving supported preinstalled pandas/Matplotlib/IPython versions. No worker source tree or helper files are required. If an older shared module was already imported, restart after installation.

For a locked local run, copy `notebooks/`, install [uv](https://docs.astral.sh/uv/) and Git, then run **inside that directory**:

```sh
uv sync --locked
uv run --locked vgi-notebooks
```

Select this directory's `.venv/bin/python` as the interactive kernel with `notebooks/` as its working directory. The local environment installs the shared `[runner]` extra; the Colab bootstrap installs only base dependencies. No global kernel registration is required. Published Haybarn **2.0.0rc11** runs the real community VGI extension. The shared connection creates an empty temporary SQL secret directory and disables persistent secrets.

Every notebook shows this literal released launch in its `INSTALL` / `LOAD` / `ATTACH` cell:

```text
uvx --python 3.13 --from git+https://github.com/Query-farm/vgi-ortools@v0.4.0 vgi-ortools
```

[v0.4.0](https://github.com/Query-farm/vgi-ortools/tree/v0.4.0) resolves to source commit `4f2e5c6715c50ca86038a1e868dc9437b7e30c3a`. The worker uses its own managed Python 3.13 environment, separate from the notebook kernel. Its release dependencies work without an additional SDK override. First-time Git/package/extension downloads require network access. Solving then runs locally without credentials or outbound API calls.

The notebook dependencies are locked independently of the repository's root environment. The release's worker dependencies retain their declared constraints, including OR-Tools `>=9.15,<9.16`; `solver_info` records **9.15.6755** in the validated runs. The worker source pin does not freeze every transitive package. To upgrade, change the literal release tag and rerun the default, experiment, and catalog checks before relying on changed outputs.

## Save parameter experiments separately

```sh
uv run --locked vgi-notebooks 01_workload_is_not_completion.ipynb \
  --parameters '{"A_CUT_MINUTES":6}' --output-dir artifacts/a-cut-6
uv run --locked vgi-notebooks 02_shipping_cost_and_capacity.ipynb \
  --parameters '{"SHIP_UNITS":17}' --output-dir artifacts/shipment-17
uv run --locked vgi-notebooks 03_selecting_is_not_packing_everything.ipynb \
  --parameters '{"CAPACITY_KG":12}' --output-dir artifacts/capacity-12
```

The runner rejects overrides without a distinct output directory and writes notebooks only after full success. Experiments leave saved defaults intact. Parameter domains are checked before ATTACH. The runner adds experiment documentation/branding and writes review artifacts under ignored `artifacts/`; saved default SVG links remain readable on GitHub.

- The job-shop comparison holds seven operation durations fixed and changes only A0. Compare the makespan, simple bound and machine ordering; multiple equally optimal schedules can exist.
- The shipping experiment fixes every arc capacity and unit cost. Compare the selected cost and flow allocation; maximum capacity and the over-capacity infeasibility diagnostic stay fixed.
- The packing experiment fixes all six item masses and scores. Compare selected labels/score with all-item bin count; raw bin IDs are labels, not physical container identities.

## Provenance and model limits

The job-shop story preserves the original eight-operation example from the released [test fixture](https://github.com/Query-farm/vgi-ortools/blob/v0.4.0/tests/test_models.py). All jobs release at elapsed minute zero; operations are non-preemptive, each job follows task order, and each machine handles one operation at a time. Intervals are half-open `[start,end)`, with one integer tick per minute. Setup, transfer, staffing, breakdown, shift and due-date constraints are absent. CP-SAT uses a fixed seed, its single-threaded default, and a five-second cap. `OPTIMAL` proves the minimum; `FEASIBLE` is only an incumbent. The tighter-horizon call preserves the worker's single NULL-data status row when there is no solution.

The shipping story uses an authored five-arc network matching the current repository's composed flow example, with explicit crate and illustrative cost-unit labels. The published release exposes the same solver behavior; changes since the release are metadata/example changes. Supply and demand balance, every arc is directed, and crate capacities apply per planning period. Arc costs apply per traversed arc. There are no losses, transit times or fixed charges. Positive-flow-only outputs are left-joined to known arcs only after a successful status check. Saturation alone is not a bottleneck-expansion proof.

The packing story authors six indivisible items with integer kilogram masses and additive priority points. Knapsack maximizes score in one container; bin packing includes every item and minimizes container count. The notebook independently enumerates all 64 subsets in SQL to reconcile the knapsack score. This is mass-only capacity, not geometric or operational loading advice.

All scalar matrices are assembled in SQL, serialized as integer list text, then passed to visible function calls. This works with JupySQL's separate cursors and the worker's constant table-function arguments. Worker calls are materialized before DataFrame captures to avoid repeating a solve merely to display its results. Last-process solve metrics are not used as authoritative query diagnostics; explicit returned statuses and modeled invariants are.

## Catalog coverage and validation

[coverage.json](coverage.json) maps **7 of 14 distinct named surfaces** in both actual released and current runtime catalogs to visible executed SQL, results and interpretation. Complete functions/tables/views exports, explicit reviews and execution receipts are under [coverage/](coverage/). Overloads and aliases count once; no authentication/write capability is excluded. The unrepresented assignment, general CP-SAT, RCPSP, TSP, VRP, bin-rollup and process-statistics surfaces remain explicitly uncovered. Release execution is reported separately from current-main runtime execution.

Validation includes all three defaults and parameter experiments, six default and six experimental chart inspections, a fresh notebook-only preloaded compatible kernel for this shared launcher path, runner/parameter guards, Ruff/format and notebook structure checks. Hosted Colab itself was not manually exercised. Existing green [source CI at baseline main](https://github.com/Query-farm/vgi-ortools/actions/runs/43561107269) is reused; worker source and root locks are unchanged, and no worker build or redundant source suite was run.

The proper catalog title, linked repository heading, official unchanged Strata Sun at top/final bottom, VGI logo and SVG chart presentation come from the shared package. Parameters and SQL remain visible; imports, plotting and cleanup are folded.
