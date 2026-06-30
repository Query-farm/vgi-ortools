# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "vgi-python[http,oauth]>=0.8.8",
#     "ortools>=9.15,<9.16",
# ]
# ///
"""Stdio entry shim for the OR-Tools VGI worker.

Lets the worker run straight from a source checkout (``uv run
ortools_worker.py``) and from a container, and keeps ``import ortools_worker``
working for tests. The implementation lives in ``vgi_ortools.worker``; installed
users invoke the ``vgi-ortools`` console script instead.

    ATTACH 'ortools' (TYPE vgi, LOCATION 'uv run ortools_worker.py');
"""

from vgi_ortools.worker import OrtoolsWorker, main

__all__ = ["OrtoolsWorker", "main"]

if __name__ == "__main__":
    main()
