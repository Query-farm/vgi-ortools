# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "vgi-python[http,oauth]>=0.8.8",
#     "ortools>=9.15,<9.16",
# ]
# ///
"""HTTP entry shim for the OR-Tools VGI worker (used by container deploys).

Forces the worker CLI into HTTP mode. The implementation lives in
``vgi_ortools.worker``; installed users invoke the ``vgi-ortools-http`` console
script instead.
"""

from vgi_ortools.worker import OrtoolsWorker, main_http

__all__ = ["OrtoolsWorker", "main_http"]


def main() -> None:
    """Run the worker over HTTP."""
    main_http()


if __name__ == "__main__":
    main()
