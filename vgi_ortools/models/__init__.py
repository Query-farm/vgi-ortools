"""Pure solver builders (Arrow-free, VGI-free) over Google OR-Tools.

Each module turns plain Python inputs (parsed from the worker's scalar LIST /
JSON arguments) into an OR-Tools model, solves it, and returns the result as
column lists plus a status string. Keeping these Arrow-free makes the
optimization logic unit-testable in isolation (see ``tests/``).
"""
