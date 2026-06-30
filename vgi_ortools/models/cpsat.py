"""The ``solve_cpsat`` JSON model dialect (README section A) -> ``cp_model.CpModel``.

This is the general escape hatch and the load-bearing surface of the worker: a
stable, versioned JSON dialect that deserializes one-to-one onto an OR-Tools
``CpModel``. The per-domain builders (job-shop, RCPSP, bin-packing) all produce
models expressible in this dialect.

The dialect is *declared, versioned, and validated* before any model object is
touched: a malformed node is a clean ``MODEL_INVALID`` (via
:class:`~vgi_ortools.validate.ModelValidationError`) naming the offending JSON
path -- never a Python traceback. All numeric domains are integers; a fractional
float anywhere in a bound / coefficient / constant is rejected per the
integer-scaling rule.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from .. import DIALECT_VERSION
from ..limits import DEFAULT_TIME_LIMIT_S, Limits
from ..status import SOLUTION_STATUSES, cpsat_status
from ..validate import ModelValidationError, ensure_int, require

_OPS = {"<=", "==", ">=", "!=", "<", ">"}
_ENFORCEABLE = {"linear", "bool_or", "bool_and", "bool_xor"}


@dataclass(slots=True)
class CpSatResult:
    """Expanded ``solve_cpsat`` result: parallel column lists + solve metadata.

    The columns are emitted verbatim by the function layer. For a non-solution
    status the lists hold a single ``(var=None, value=None)`` row so a solve is
    never an empty result set (the empty-vs-infeasible contract).
    """

    var: list[str | None] = field(default_factory=list)
    value: list[int | None] = field(default_factory=list)
    status: str = "UNKNOWN"
    objective: float | None = None
    best_bound: float | None = None
    wall_time: float = 0.0
    num_branches: int = 0
    # Extra stats mirrored into last_solve_stats (not emitted as columns).
    num_conflicts: int = 0
    num_booleans: int = 0
    deterministic_time: float = 0.0

    def stats(self) -> dict[str, Any]:
        """Return the full stat set for ``last_solve_stats`` recording."""
        return {
            "status": self.status,
            "objective": self.objective,
            "best_bound": self.best_bound,
            "wall_time": self.wall_time,
            "num_branches": self.num_branches,
            "num_conflicts": self.num_conflicts,
            "num_booleans": self.num_booleans,
            "deterministic_time": self.deterministic_time,
        }


def default_num_workers() -> int:
    """The default CP-SAT worker count.

    Defaults to ``1`` (single-threaded, fully deterministic search). A caller can
    request parallel search by passing ``num_workers`` explicitly; on a healthy
    host ``min(8, ncpu)`` is a good value. Single-threaded is the safe default
    because parallel CP-SAT is non-deterministic in wall-clock and, on some
    platforms, the multi-threaded engine can deadlock -- see CLAUDE.md.

    Returns:
        The default search-worker count (``1``).
    """
    return 1


def parallel_num_workers() -> int:
    """A reasonable parallel worker count for hosts that opt in: ``min(8, ncpu)``.

    Returns:
        ``min(8, ncpu)``.
    """
    return min(8, os.cpu_count() or 1)


# ---------------------------------------------------------------------------
# Document parsing + validation (pre-flight, before any model object is built).
# ---------------------------------------------------------------------------


def parse_document(model_json: str) -> dict[str, Any]:
    """Parse + structurally validate the model JSON before building anything.

    Args:
        model_json: The ``model`` argument (a JSON document string).

    Returns:
        The parsed document dict.

    Raises:
        ModelValidationError: On invalid JSON, an unknown dialect major version,
            or a structurally malformed document.
    """
    if not model_json or not model_json.strip():
        raise ModelValidationError("model is required: pass a JSON CpModel document (see README section A)")
    try:
        doc = json.loads(model_json)
    except json.JSONDecodeError as exc:
        raise ModelValidationError(f"model is not valid JSON: {exc}") from exc
    require(isinstance(doc, dict), "model must be a JSON object with 'variables'/'constraints'/'objective'")
    version = doc.get("version", DIALECT_VERSION)
    require(
        isinstance(version, int) and version == DIALECT_VERSION,
        f"unsupported model dialect version {version!r}; this worker speaks version {DIALECT_VERSION}",
    )
    require(isinstance(doc.get("variables", []), list), "model.variables must be a list")
    require(isinstance(doc.get("constraints", []), list), "model.constraints must be a list")
    assert isinstance(doc, dict)
    return doc


# ---------------------------------------------------------------------------
# The builder: dialect document -> CpModel.
# ---------------------------------------------------------------------------


class _Builder:
    """Turns a validated dialect document into a ``CpModel`` and tracks outputs.

    Holds the var registry used to resolve ``{"var": name}`` references, the
    ordered list of scalar (int/bool) output variables, and the interval
    components to expand on output.
    """

    def __init__(self) -> None:
        from ortools.sat.python import cp_model

        self.cp = cp_model
        self.model = cp_model.CpModel()
        self.vars: dict[str, Any] = {}
        # Ordered scalar outputs: (name, var-or-expr).
        self.scalars: list[tuple[str, Any]] = []
        # Interval outputs: (name, start_expr, size_expr, end_expr, presence|None).
        self.intervals: list[tuple[str, Any, Any, Any, Any]] = []
        self._names: set[str] = set()

    # -- expression / literal parsing --------------------------------------

    def expr(self, node: Any, *, where: str) -> Any:
        """Parse a ``LinearExpr`` node (a const, var, or affine sum)."""
        if isinstance(node, bool):
            raise ModelValidationError(f"{where}: a bare boolean is not a linear expression")
        if isinstance(node, (int, float)):
            return ensure_int(node, where=where)
        require(isinstance(node, dict), f"{where} must be a linear-expression object or integer")
        if "const" in node:
            return ensure_int(node["const"], where=f"{where}.const")
        if "var" in node:
            return self._var(node["var"], where=f"{where}.var")
        if "sum" in node:
            terms = node["sum"]
            require(isinstance(terms, list), f"{where}.sum must be a list of {{coef, var}} terms")
            acc: Any = ensure_int(node.get("offset", 0), where=f"{where}.offset")
            for k, term in enumerate(terms):
                require(isinstance(term, dict), f"{where}.sum[{k}] must be a {{coef, var}} object")
                coef = ensure_int(term.get("coef", 1), where=f"{where}.sum[{k}].coef")
                acc = acc + coef * self._var(term["var"], where=f"{where}.sum[{k}].var")
            return acc
        raise ModelValidationError(f"{where}: unrecognized linear-expression node {sorted(node)}")

    def literal(self, node: Any, *, where: str) -> Any:
        """Parse a boolean literal: a bool var name or its ``{"not": name}`` negation."""
        if isinstance(node, str):
            return self._bool(node, where=where)
        require(isinstance(node, dict) and "not" in node, f"{where} must be a bool var name or {{'not': name}}")
        return self._bool(node["not"], where=f"{where}.not").Not()

    def _var(self, name: Any, *, where: str) -> Any:
        require(isinstance(name, str), f"{where} must be a variable name (string)")
        v = self.vars.get(name)
        if v is None:
            raise ModelValidationError(f"{where} references unknown variable {name!r}")
        return v

    def _bool(self, name: Any, *, where: str) -> Any:
        v = self._var(name, where=where)
        if not isinstance(v, self.cp.IntVar):  # BoolVar is an IntVar subtype in the API
            raise ModelValidationError(f"{where} variable {name!r} is not usable as a boolean literal")
        return v

    # -- variables ---------------------------------------------------------

    def add_variable(self, node: Any, *, where: str) -> None:
        """Add one ``variables[]`` node to the model."""
        require(isinstance(node, dict), f"{where} must be an object")
        kind = node.get("kind")
        name = node.get("name")
        require(isinstance(name, str) and name != "", f"{where} requires a non-empty string 'name'")
        require(name not in self._names, f"{where}: duplicate variable name {name!r}")
        self._names.add(name)

        if kind == "int":
            self._add_int(node, name, where=where)
        elif kind == "bool":
            v = self.model.new_bool_var(name)
            self.vars[name] = v
            self.scalars.append((name, v))
        elif kind == "interval":
            self._add_interval(node, name, where=where)
        else:
            raise ModelValidationError(f"{where}: unknown variable kind {kind!r} (int|bool|interval)")

    def _add_int(self, node: Any, name: str, *, where: str) -> None:
        if "domain" in node:
            intervals = node["domain"]
            require(
                isinstance(intervals, list) and len(intervals) > 0,
                f"{where}.domain must be a non-empty list of [lo,hi]",
            )
            flat: list[int] = []
            for k, iv in enumerate(intervals):
                require(isinstance(iv, list) and len(iv) == 2, f"{where}.domain[{k}] must be a [lo, hi] pair")
                lo = ensure_int(iv[0], where=f"{where}.domain[{k}][0]")
                hi = ensure_int(iv[1], where=f"{where}.domain[{k}][1]")
                flat.extend((lo, hi))
            domain = self.cp.Domain.from_flat_intervals(flat)
            v = self.model.new_int_var_from_domain(domain, name)
        else:
            require("lb" in node and "ub" in node, f"{where} int needs lb+ub (or a sparse 'domain')")
            lb = ensure_int(node["lb"], where=f"{where}.lb")
            ub = ensure_int(node["ub"], where=f"{where}.ub")
            require(lb <= ub, f"{where}: lb ({lb}) must be <= ub ({ub})")
            v = self.model.new_int_var(lb, ub, name)
        self.vars[name] = v
        self.scalars.append((name, v))

    def _add_interval(self, node: Any, name: str, *, where: str) -> None:
        start = self.expr(node["start"], where=f"{where}.start") if "start" in node else None
        size = self.expr(node["size"], where=f"{where}.size") if "size" in node else None
        end = self.expr(node["end"], where=f"{where}.end") if "end" in node else None
        present = [x is not None for x in (start, size, end)]
        require(sum(present) >= 2, f"{where} interval needs at least two of start/size/end")
        # At least two of the three are present, so the missing one is derivable;
        # the operands are CP-SAT expressions (Any) once the two are bound.
        start_e: Any = start
        size_e: Any = size
        end_e: Any = end
        if start_e is None:
            start_e = end_e - size_e
        if size_e is None:
            size_e = end_e - start_e
        if end_e is None:
            end_e = start_e + size_e
        start, size, end = start_e, size_e, end_e
        if "presence" in node:
            presence = self.literal(node["presence"], where=f"{where}.presence")
            iv = self.model.new_optional_interval_var(start, size, end, presence, name)
            self.vars[name] = iv
            self.intervals.append((name, start, size, end, presence))
        else:
            iv = self.model.new_interval_var(start, size, end, name)
            self.vars[name] = iv
            self.intervals.append((name, start, size, end, None))

    # -- constraints -------------------------------------------------------

    def add_constraint(self, node: Any, *, where: str) -> None:
        """Add one ``constraints[]`` node to the model (with optional reification)."""
        require(isinstance(node, dict), f"{where} must be an object")
        ctype = node.get("type")
        require(isinstance(ctype, str), f"{where} requires a string 'type'")
        handler = getattr(self, f"_c_{ctype}", None)
        if handler is None:
            raise ModelValidationError(f"{where}: unknown constraint type {ctype!r}")
        ct = handler(node, where=where)
        if "enforce" in node:
            require(
                ctype in _ENFORCEABLE,
                f"{where}: constraint type {ctype!r} does not accept 'enforce' "
                f"(only {sorted(_ENFORCEABLE)} are reifiable)",
            )
            lits = node["enforce"]
            require(isinstance(lits, list), f"{where}.enforce must be a list of literals")
            ct.only_enforce_if([self.literal(x, where=f"{where}.enforce[{i}]") for i, x in enumerate(lits)])

    def _exprs(self, nodes: Any, *, where: str) -> list[Any]:
        require(isinstance(nodes, list), f"{where} must be a list of expressions")
        return [self.expr(n, where=f"{where}[{i}]") for i, n in enumerate(nodes)]

    def _intervals(self, names: Any, *, where: str) -> list[Any]:
        require(isinstance(names, list), f"{where} must be a list of interval names")
        out = []
        for i, nm in enumerate(names):
            v = self._var(nm, where=f"{where}[{i}]")
            require(isinstance(v, self.cp.IntervalVar), f"{where}[{i}] {nm!r} is not an interval variable")
            out.append(v)
        return out

    def _c_linear(self, node: Any, *, where: str) -> Any:
        expr = self.expr(node["expr"], where=f"{where}.expr")
        if "bounds" in node:
            lo, hi = node["bounds"]
            return self.model.add_linear_constraint(
                expr, ensure_int(lo, where=f"{where}.bounds[0]"), ensure_int(hi, where=f"{where}.bounds[1]")
            )
        op = node.get("op")
        require(op in _OPS, f"{where}.op must be one of {sorted(_OPS)}")
        rhs = ensure_int(node["rhs"], where=f"{where}.rhs")
        if op == "<=":
            return self.model.add(expr <= rhs)
        if op == ">=":
            return self.model.add(expr >= rhs)
        if op == "==":
            return self.model.add(expr == rhs)
        if op == "!=":
            return self.model.add(expr != rhs)
        if op == "<":
            return self.model.add(expr < rhs)
        return self.model.add(expr > rhs)

    def _c_all_different(self, node: Any, *, where: str) -> Any:
        return self.model.add_all_different(self._exprs(node["vars"], where=f"{where}.vars"))

    def _c_element(self, node: Any, *, where: str) -> Any:
        index = self.expr(node["index"], where=f"{where}.index")
        target = self.expr(node["target"], where=f"{where}.target")
        return self.model.add_element(index, self._exprs(node["vars"], where=f"{where}.vars"), target)

    def _c_table(self, node: Any, *, where: str) -> Any:
        variables = self._exprs(node["vars"], where=f"{where}.vars")
        tuples = node["tuples"]
        require(isinstance(tuples, list), f"{where}.tuples must be a list of integer tuples")
        rows = [
            [ensure_int(v, where=f"{where}.tuples[{r}][{c}]") for c, v in enumerate(t)] for r, t in enumerate(tuples)
        ]
        if node.get("allowed", True):
            return self.model.add_allowed_assignments(variables, rows)
        return self.model.add_forbidden_assignments(variables, rows)

    def _c_no_overlap(self, node: Any, *, where: str) -> Any:
        return self.model.add_no_overlap(self._intervals(node["intervals"], where=f"{where}.intervals"))

    def _c_cumulative(self, node: Any, *, where: str) -> Any:
        intervals = self._intervals(node["intervals"], where=f"{where}.intervals")
        demands = self._exprs(node["demands"], where=f"{where}.demands")
        capacity = self.expr(node["capacity"], where=f"{where}.capacity")
        return self.model.add_cumulative(intervals, demands, capacity)

    def _c_circuit(self, node: Any, *, where: str) -> Any:
        arcs = node["arcs"]
        require(isinstance(arcs, list), f"{where}.arcs must be a list of [tail, head, literal] triples")
        built = []
        for i, arc in enumerate(arcs):
            require(isinstance(arc, list) and len(arc) == 3, f"{where}.arcs[{i}] must be [tail, head, literal]")
            tail = ensure_int(arc[0], where=f"{where}.arcs[{i}][0]")
            head = ensure_int(arc[1], where=f"{where}.arcs[{i}][1]")
            lit = self.literal(arc[2], where=f"{where}.arcs[{i}][2]")
            built.append((tail, head, lit))
        return self.model.add_circuit(built)

    def _c_reservoir(self, node: Any, *, where: str) -> Any:
        times = self._exprs(node["times"], where=f"{where}.times")
        changes = [ensure_int(c, where=f"{where}.level_changes[{i}]") for i, c in enumerate(node["level_changes"])]
        lo = ensure_int(node["min_level"], where=f"{where}.min_level")
        hi = ensure_int(node["max_level"], where=f"{where}.max_level")
        if "actives" in node:
            actives = [self.literal(a, where=f"{where}.actives[{i}]") for i, a in enumerate(node["actives"])]
            return self.model.add_reservoir_constraint_with_active(times, changes, actives, lo, hi)
        return self.model.add_reservoir_constraint(times, changes, lo, hi)

    def _c_mult_equality(self, node: Any, *, where: str) -> Any:
        target = self.expr(node["target"], where=f"{where}.target")
        factors = self._exprs(node["factors"], where=f"{where}.factors")
        return self.model.add_multiplication_equality(target, factors)

    def _c_div_equality(self, node: Any, *, where: str) -> Any:
        return self.model.add_division_equality(
            self.expr(node["target"], where=f"{where}.target"),
            self.expr(node["num"], where=f"{where}.num"),
            self.expr(node["den"], where=f"{where}.den"),
        )

    def _c_abs_equality(self, node: Any, *, where: str) -> Any:
        return self.model.add_abs_equality(
            self.expr(node["target"], where=f"{where}.target"),
            self.expr(node["expr"], where=f"{where}.expr"),
        )

    def _c_min_equality(self, node: Any, *, where: str) -> Any:
        return self.model.add_min_equality(
            self.expr(node["target"], where=f"{where}.target"),
            self._exprs(node["exprs"], where=f"{where}.exprs"),
        )

    def _c_max_equality(self, node: Any, *, where: str) -> Any:
        return self.model.add_max_equality(
            self.expr(node["target"], where=f"{where}.target"),
            self._exprs(node["exprs"], where=f"{where}.exprs"),
        )

    def _c_bool_or(self, node: Any, *, where: str) -> Any:
        return self.model.add_bool_or(
            [self.literal(x, where=f"{where}.literals[{i}]") for i, x in enumerate(node["literals"])]
        )

    def _c_bool_and(self, node: Any, *, where: str) -> Any:
        return self.model.add_bool_and(
            [self.literal(x, where=f"{where}.literals[{i}]") for i, x in enumerate(node["literals"])]
        )

    def _c_bool_xor(self, node: Any, *, where: str) -> Any:
        return self.model.add_bool_xor(
            [self.literal(x, where=f"{where}.literals[{i}]") for i, x in enumerate(node["literals"])]
        )

    def _c_implication(self, node: Any, *, where: str) -> Any:
        return self.model.add_implication(
            self.literal(node["a"], where=f"{where}.a"),
            self.literal(node["b"], where=f"{where}.b"),
        )

    # -- objective ---------------------------------------------------------

    def add_objective(self, node: Any) -> bool:
        """Add the objective node if present; return whether an objective was set."""
        if node is None:
            return False
        require(isinstance(node, dict), "objective must be an object with sense+terms")
        sense = node.get("sense")
        require(sense in ("minimize", "maximize"), "objective.sense must be 'minimize' or 'maximize'")
        terms = node.get("terms", [])
        require(isinstance(terms, list), "objective.terms must be a list of {coef, var}")
        acc: Any = ensure_int(node.get("offset", 0), where="objective.offset")
        for i, term in enumerate(terms):
            coef = ensure_int(term.get("coef", 1), where=f"objective.terms[{i}].coef")
            acc = acc + coef * self._var(term["var"], where=f"objective.terms[{i}].var")
        if sense == "minimize":
            self.model.minimize(acc)
        else:
            self.model.maximize(acc)
        return True


def solve(
    model_json: str,
    *,
    time_limit_s: float | None = DEFAULT_TIME_LIMIT_S,
    num_workers: int | None = None,
    random_seed: int = 1,
    enumerate_all: bool = False,
    project: list[str] | None = None,
    limits: Limits | None = None,
) -> CpSatResult:
    """Build, validate, solve a dialect model and expand the per-variable rows.

    Args:
        model_json: The JSON CpModel document (README section A).
        time_limit_s: Wall-clock cap in seconds (clamped to the worker hard cap).
        num_workers: Parallel CP-SAT workers; defaults to ``min(8, ncpu)``.
        random_seed: Fixed seed for a deterministic objective value.
        enumerate_all: Reserved (solution-pool streaming is roadmap, README F);
            when true the solver enumerates internally but the final solution is
            still what is reported.
        project: Optional subset of variable names to emit (others are dropped).
        limits: Resolved size / time limits; defaults to module defaults.

    Returns:
        A :class:`CpSatResult` with parallel column lists and solve metadata.

    Raises:
        ModelValidationError: If the document is malformed or violates the
            integer-scaling rule (surfaced as a ``MODEL_INVALID`` row upstream).
    """
    limits = limits or Limits()
    doc = parse_document(model_json)

    builder = _Builder()
    for i, vnode in enumerate(doc.get("variables", [])):
        builder.add_variable(vnode, where=f"variables[{i}]")
    for i, cnode in enumerate(doc.get("constraints", [])):
        builder.add_constraint(cnode, where=f"constraints[{i}]")
    has_objective = builder.add_objective(doc.get("objective"))

    proto = builder.model.proto
    limits.guard_cpsat(len(proto.variables), len(proto.constraints))

    cp = builder.cp
    solver = cp.CpSolver()
    solver.parameters.max_time_in_seconds = limits.clamp_time(time_limit_s)
    solver.parameters.random_seed = int(random_seed)
    workers = int(num_workers) if num_workers else default_num_workers()
    solver.parameters.num_workers = workers
    if workers > 1:
        # interleave_search keeps a parallel solve's objective deterministic for a
        # fixed seed (the assignment may still tie-break differently).
        solver.parameters.interleave_search = True
    if enumerate_all:
        solver.parameters.enumerate_all_solutions = True

    code = solver.solve(builder.model)
    status = cpsat_status(code)

    result = CpSatResult(
        status=status,
        wall_time=float(solver.wall_time),
        num_branches=int(solver.num_branches),
        num_conflicts=int(solver.num_conflicts),
        num_booleans=int(solver.num_booleans),
        deterministic_time=float(solver.deterministic_time),
    )
    if has_objective and status in SOLUTION_STATUSES:
        result.objective = float(solver.objective_value)
        result.best_bound = float(solver.best_objective_bound)

    if status not in SOLUTION_STATUSES:
        # Empty-vs-infeasible contract: exactly one (NULL, NULL) row.
        result.var = [None]
        result.value = [None]
        return result

    keep = set(project) if project else None
    for name, var in builder.scalars:
        if keep is not None and name not in keep:
            continue
        result.var.append(name)
        result.value.append(int(solver.value(var)))
    for name, start, size, end, presence in builder.intervals:
        if keep is not None and name not in keep:
            continue
        result.var.append(f"{name}.start")
        result.value.append(_eval(solver, start))
        result.var.append(f"{name}.size")
        result.value.append(_eval(solver, size))
        result.var.append(f"{name}.end")
        result.value.append(_eval(solver, end))
        if presence is not None:
            result.var.append(f"{name}.presence")
            result.value.append(int(solver.value(presence)))

    if not result.var:
        # A model with no (projected) variables still must not be empty.
        result.var = [None]
        result.value = [None]
    return result


def _eval(solver: Any, expr: Any) -> int:
    """Evaluate a parsed expression (a const int or an OR-Tools expr) under a solution."""
    if isinstance(expr, int):
        return expr
    return int(solver.value(expr))
