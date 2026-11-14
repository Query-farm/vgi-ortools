# OR-Tools notebooks

[01 · Workload is not a completion time](01_workload_is_not_completion.ipynb)
asks why ten minutes of work on the busiest machine does not guarantee a
ten-minute batch. Visible SQL builds a job-shop matrix, derives lower bounds,
solves through the real VGI worker, validates precedence and machine capacity,
and tests a tighter deadline. Two embedded charts show workloads and the actual
Gantt schedule. The final experiment changes one operation's duration.

## Setup and execution

With Git and [uv](https://docs.astral.sh/uv/) on PATH, copy/download `notebooks/`
with its companion files and run **inside that directory**:

```sh
uv sync --locked
uv run --locked python execute.py
```

No worker checkout, root environment or local worker build is required. The
independent notebook environment supports Python 3.13–3.14 and pins published
Haybarn 2.0.0rc11. The visible ATTACH uses this literal launcher:

```text
uvx --python 3.13 --with vgi-python==0.51.1 --from git+https://github.com/Query-farm/vgi-ortools@4f2e5c6715c50ca86038a1e868dc9437b7e30c3a vgi-ortools
```

This full Git revision is the published **v0.4.0** release. Its assets are Python
packages, not platform binaries for `github-auto`. `uvx` downloads and caches the
worker separately, using Python 3.13 and an explicitly pinned compatible SDK.
Initial package, Git and extension downloads need internet access. The optimization
is local deterministic CPU work. Worker transitive dependencies follow the release's
constraints; OR-Tools remains within its pinned 9.15 minor range. To upgrade, change
the literal revision/SDK pin and rerun both parameter cases before adopting it.

Select this directory's `.venv/bin/python` as the interactive kernel, with this
directory as kernel cwd. No global kernel registration is needed. The headless
runner starts a fresh kernel using its own interpreter and notebook-directory cwd.
Parameters and analytical/connection SQL stay visible. Imports, plotting, derived
prose and cleanup are folded; editors can retain their own folding state.

## One-parameter experiment

Change `A_CUT_MINUTES` from 3 to 6 and restart/run all, or save a separate copy:

```sh
uv run --locked python execute.py \
  --parameters '{"A_CUT_MINUTES": 6}' \
  --output-dir validation/a-cut-6
```

The runner rejects unknown parameters and overrides without a separate output
directory. It writes only after a successful full execution, preserving the last
successful notebook on failure. Experiment copies include branding, helper, runner, documentation and environment
companions. `validation/` holds ignored logs, chart images and experiment
copies; the main notebook retains its embedded outputs. The notebook directory and its companions are sufficient to rerun the examples.

The selected duration accepts integers 1–10 minutes. The reference solve keeps
A0 at 3 minutes; the comparison table derives the change from the two returned
solutions. The saved default proves an 11-minute makespan; the 6-minute experiment
proves 12 minutes despite adding 3 minutes of processing. Both simple bounds are
10 minutes. This illustrates re-optimization, not a general sensitivity formula.

## Provenance and model limits

Eight synthetic operations reproduce the small `_JOBSHOP` fixture in
[`tests/test_models.py`](https://github.com/Query-farm/vgi-ortools/blob/4f2e5c6715c50ca86038a1e868dc9437b7e30c3a/tests/test_models.py); job letters, machine labels and
minute units are illustrative. This is not factory telemetry or a benchmark of
real production throughput. No optimizer is called directly from notebook Python:
all three solves use `ortools.main.solve_jobshop` through Haybarn/VGI.

All jobs release at elapsed minute zero. Operations run non-preemptively in task
index order; each machine handles at most one operation at a time. Machines are
always available. Setup, transfer, staffing, breakdown, shift and due-date
constraints are omitted. A makespan objective minimizes the last finish time,
not total job delay or machine idle time. The schedule is not necessarily unique.

Durations are integer minutes, with one solver tick per minute and half-open
`[start, end)` intervals. Subminute inputs need a documented common integer scale
before solving. The main horizon is the sum of durations; the diagnostic solve
uses the returned makespan minus one. CP-SAT stays single-threaded using the
worker's default (parallel CP-SAT is known to hang on this macOS host), a seed of
1, and a 5-second solve cap. `OPTIMAL` is a CP-SAT proof; `FEASIBLE` is an incumbent
without such proof. No routing-library status interpretation is used.

Ordinary in-memory tables survive JupySQL's separate cursors; session variables
do not. The notebook serializes each SQL-built integer matrix as JSON list text
and transfers that scalar to the visible solver call through JupySQL interpolation.
SQL handles domain
calculations and checks; Python only configures the connection, formats prose and
renders charts with Seaborn/Matplotlib. The connection closes after the experiment.
The official Strata Sun + Query.Farm and VGI assets are copied unchanged from the
approved perps examples, originally from the Query.Farm media kit. The linked
Strata Sun appears at the top and final bottom.
