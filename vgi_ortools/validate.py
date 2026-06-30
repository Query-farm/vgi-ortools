"""Pre-flight validation: integer-scaling + JSON-dialect schema checks.

OR-Tools' routing and CP-SAT engines are *integer* engines. Any non-integer
value in a distance / cost / demand / capacity / duration position is rejected
here (before any solver object is touched) as a clean ``MODEL_INVALID``-class
error -- never a silent round (rounding changes the optimum) and never a Python
traceback leaking to SQL.

``ModelValidationError`` is the single exception type raised by every validation
helper; the function layer catches it and turns it into the documented
``MODEL_INVALID`` status row (for the solvers that return a status row) or a
clear SQL error (for the scalar / introspection functions).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


class ModelValidationError(ValueError):
    """A model that is malformed or violates the integer-scaling rule.

    Carries a message that names the offending value / JSON path and the fix, so
    the SQL caller gets an actionable error rather than a stack trace.
    """


def ensure_int(value: Any, *, where: str) -> int:
    """Coerce a model value to ``int``, rejecting non-integral floats.

    A ``bool`` is accepted (Python ``True``/``False`` are ``1``/``0``). A float
    that is exactly integral (``12.0``) is accepted and narrowed; a fractional
    float (``12.4``) is rejected with the integer-scaling guidance.

    Args:
        value: The candidate numeric value from the model / matrix.
        where: A human label for the position (used in the error message).

    Returns:
        The value as a Python ``int``.

    Raises:
        ModelValidationError: If ``value`` is not an integer (fractional float,
            None, string, etc.).
    """
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        raise ModelValidationError(
            f"{where} {value!r} is not an integer -- scale your floats to integers "
            "(e.g. pass meters not kilometres, cents not dollars) before solving; "
            "rounding is refused because it changes the optimum."
        )
    raise ModelValidationError(
        f"{where} {value!r} is not an integer -- distances/costs/demands/durations "
        "must be integers (OR-Tools is an integer engine)."
    )


def ensure_int_list(values: Iterable[Any], *, where: str) -> list[int]:
    """Coerce every element of an iterable to ``int`` (per :func:`ensure_int`).

    Args:
        values: The iterable of candidate values.
        where: A human label for the position (indexed per element in errors).

    Returns:
        A list of Python ints.

    Raises:
        ModelValidationError: If any element is not an integer.
    """
    return [ensure_int(v, where=f"{where}[{i}]") for i, v in enumerate(values)]


def require(condition: bool, message: str) -> None:
    """Raise :class:`ModelValidationError` with ``message`` when ``condition`` is false.

    Args:
        condition: The invariant that must hold.
        message: The error message naming what was wrong and how to fix it.

    Raises:
        ModelValidationError: If ``condition`` is falsy.
    """
    if not condition:
        raise ModelValidationError(message)
