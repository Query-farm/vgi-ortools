# vgi-ortools — dev and test targets.
#
# Usage:
#   make test          # unit/integration (pytest) + end-to-end SQL (haybarn-unittest)
#   make test-unit     # pytest only
#   make test-sql      # DuckDB sqllogictest .test files via haybarn-unittest
#   make lint          # ruff + mypy + pydoclint
#
# test-sql is self-contained: it points VGI_ORTOOLS_WORKER at the worker run as a
# uv stdio subprocess (exactly how DuckDB drives it after ATTACH) and runs the
# files under test/sql/. haybarn-unittest is a uv tool:
#   uv tool install 'haybarn-unittest<2'   # installs ~/.local/bin/haybarn-unittest

# Worker command DuckDB uses for ATTACH (overridable). Use the installed console
# script from the project venv (exactly what CI's VGI_ORTOOLS_WORKER points at),
# NOT `uv run ortools_worker.py` — a `uv run` PEP-723 script env caches its own
# copy of the SDK and can drift from the venv the rest of the gates use.
WORKER_STDIO    ?= $(CURDIR)/.venv/bin/vgi-ortools

# haybarn-unittest lives in the uv tools bin; keep it on PATH.
HAYBARN_BIN     ?= $(HOME)/.local/bin
TEST_DIR         = .
TEST_PATTERN     = test/sql/*

.PHONY: test test-unit test-sql lint format

test: test-unit test-sql

test-unit:
	uv run --no-sync pytest -q

test-sql:
	PATH="$(HAYBARN_BIN):$$PATH" \
		VGI_ORTOOLS_WORKER="$(WORKER_STDIO)" \
		haybarn-unittest --test-dir "$(TEST_DIR)" "$(TEST_PATTERN)"

lint:
	uv run --no-sync ruff format --check .
	uv run --no-sync ruff check .
	uv run --no-sync mypy vgi_ortools/
	uv run --no-sync pydoclint --config pyproject.toml vgi_ortools/

format:
	uv run --no-sync ruff format .
	uv run --no-sync ruff check --fix .
