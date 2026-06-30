"""Time-limit clamp and problem-size guards.

These guards protect the shared worker process: a single huge or unbounded solve
must not pin a worker thread forever or OOM the box and starve other
connections. Every solver takes a mandatory ``time_limit_s`` (default 10s)
clamped to an ATTACH-configurable hard cap (default 60s), and refuses a model
that exceeds a size guard with a clean error that names the limit and how to
raise it -- never an OOM.

The caps are read from worker settings (ATTACH options) when present and fall
back to the module defaults otherwise, so a deployment can raise them centrally.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .validate import ModelValidationError

# Defaults (overridable per-ATTACH via settings; see Limits.from_settings).
DEFAULT_TIME_LIMIT_S = 10.0
DEFAULT_MAX_TIME_LIMIT_S = 60.0
DEFAULT_MAX_NODES = 2000  # routing (TSP / VRP)
DEFAULT_MAX_VARS = 100_000  # CP-SAT
DEFAULT_MAX_CONSTRAINTS = 200_000  # CP-SAT
DEFAULT_MAX_ARCS = 1_000_000  # flow


@dataclass(frozen=True, slots=True)
class Limits:
    """Resolved limit configuration for one solve."""

    max_time_limit_s: float = DEFAULT_MAX_TIME_LIMIT_S
    max_nodes: int = DEFAULT_MAX_NODES
    max_vars: int = DEFAULT_MAX_VARS
    max_constraints: int = DEFAULT_MAX_CONSTRAINTS
    max_arcs: int = DEFAULT_MAX_ARCS

    @classmethod
    def from_settings(cls, settings: dict[str, Any] | None) -> Limits:
        """Build limits from worker settings, falling back to the defaults.

        Args:
            settings: The worker settings dict (ATTACH options); may be ``None``.

        Returns:
            A resolved :class:`Limits`.
        """
        s = settings or {}

        def _num(key: str, default: float) -> float:
            raw = s.get(key)
            if raw is None or raw == "":
                return default
            try:
                return float(raw)
            except (TypeError, ValueError):
                return default

        return cls(
            max_time_limit_s=_num("max_time_limit_s", DEFAULT_MAX_TIME_LIMIT_S),
            max_nodes=int(_num("max_nodes", DEFAULT_MAX_NODES)),
            max_vars=int(_num("max_vars", DEFAULT_MAX_VARS)),
            max_constraints=int(_num("max_constraints", DEFAULT_MAX_CONSTRAINTS)),
            max_arcs=int(_num("max_arcs", DEFAULT_MAX_ARCS)),
        )

    def clamp_time(self, time_limit_s: float | None, *, default: float = DEFAULT_TIME_LIMIT_S) -> float:
        """Clamp a requested time limit into ``(0, max_time_limit_s]``.

        Args:
            time_limit_s: The caller's requested limit (seconds); ``None`` /
                non-positive falls back to ``default``.
            default: The default limit when none is requested.

        Returns:
            A positive float no greater than ``max_time_limit_s``.
        """
        t = default if time_limit_s is None or time_limit_s <= 0 else float(time_limit_s)
        return min(t, self.max_time_limit_s)

    def guard_nodes(self, n: int) -> None:
        """Reject a routing problem with too many nodes.

        Args:
            n: The number of routing nodes.

        Raises:
            ModelValidationError: If ``n`` exceeds ``max_nodes``.
        """
        if n > self.max_nodes:
            raise ModelValidationError(
                f"routing problem has {n} nodes, over the max_nodes guard ({self.max_nodes}); "
                "raise it with ATTACH ... (max_nodes '<N>') if your host can afford it."
            )

    def guard_cpsat(self, num_vars: int, num_constraints: int) -> None:
        """Reject a CP-SAT model that exceeds the variable / constraint guards.

        Args:
            num_vars: The number of variables in the model.
            num_constraints: The number of constraints in the model.

        Raises:
            ModelValidationError: If either count exceeds its guard.
        """
        if num_vars > self.max_vars:
            raise ModelValidationError(
                f"CP-SAT model has {num_vars} variables, over the max_vars guard ({self.max_vars}); "
                "raise it with ATTACH ... (max_vars '<N>')."
            )
        if num_constraints > self.max_constraints:
            raise ModelValidationError(
                f"CP-SAT model has {num_constraints} constraints, over the max_constraints guard "
                f"({self.max_constraints}); raise it with ATTACH ... (max_constraints '<N>')."
            )

    def guard_arcs(self, n: int) -> None:
        """Reject a flow problem with too many arcs.

        Args:
            n: The number of arcs.

        Raises:
            ModelValidationError: If ``n`` exceeds ``max_arcs``.
        """
        if n > self.max_arcs:
            raise ModelValidationError(
                f"flow problem has {n} arcs, over the max_arcs guard ({self.max_arcs}); "
                "raise it with ATTACH ... (max_arcs '<N>')."
            )
