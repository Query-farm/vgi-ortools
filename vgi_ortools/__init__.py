"""vgi-ortools: Google OR-Tools optimization / constraint solving as DuckDB SQL.

A [VGI](https://query.farm) worker exposing the OR-Tools CP-SAT constraint
solver, the routing library (TSP / VRP), linear assignment, scheduling
(job-shop / RCPSP), min-cost / max-flow, and bin-packing / knapsack as DuckDB
table and scalar functions in the ``ortools`` catalog.

This is *prescriptive* compute (decide what to do) -- the complement to the
predictive ML workers (predict / xgboost / lightgbm). It makes no outbound
network calls: every solve is pure local CPU next to the data.
"""

from __future__ import annotations

__version__ = "0.1.0"

# The solve_cpsat JSON model dialect version. Bumped only on a breaking change to
# the dialect grammar (see vgi_ortools/models/cpsat.py and README section A).
DIALECT_VERSION = 1
