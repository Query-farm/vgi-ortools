"""VGI worker hosting the ``ortools`` optimization catalog.

Assembles the OR-Tools solver functions into the single ``ortools`` catalog and
provides the process entry points. The repo-root ``ortools_worker.py`` /
``serve.py`` are thin shims over this module for ``uv run`` and containers;
installed users get the ``vgi-ortools`` / ``vgi-ortools-http`` console scripts.

    ATTACH 'vgi:ortools' AS opt;   -- via the vgi community extension
    SELECT * FROM opt.solve_tsp(distance := ..., start := 0);
"""

from __future__ import annotations

import dataclasses
import os
import sys
from typing import Any

from vgi import Worker
from vgi.catalog import ReadOnlyCatalogInterface
from vgi.catalog.catalog_interface import CatalogAttachResult, CatalogInfo

from .catalog import DATA_VERSION, DATA_VERSION_SPEC, ORTOOLS_CATALOG, SOURCE_URL

GIT_COMMIT = os.environ.get("VGI_ORTOOLS_GIT_COMMIT") or "unknown"


class OrtoolsCatalog(ReadOnlyCatalogInterface):
    """Advertises the worker's data + implementation version on ATTACH."""

    catalog = ORTOOLS_CATALOG
    catalog_name = ORTOOLS_CATALOG.name

    def catalogs(self) -> list[CatalogInfo]:
        """Advertise the ortools catalog and its data/implementation versions."""
        return [
            CatalogInfo(
                name=self._effective_catalog_name,
                implementation_version=GIT_COMMIT,
                data_version_spec=DATA_VERSION_SPEC,
                source_url=SOURCE_URL,
                attach_option_specs=[spec.serialize() for spec in self.attach_option_specs],
            )
        ]

    def catalog_attach(self, **kwargs: Any) -> CatalogAttachResult:
        """Resolve ATTACH, stamping the data and implementation versions."""
        result = super().catalog_attach(**kwargs)
        return dataclasses.replace(
            result,
            resolved_data_version=DATA_VERSION,
            resolved_implementation_version=GIT_COMMIT,
        )


class OrtoolsWorker(Worker):
    """Worker process hosting the ortools catalog."""

    catalog = ORTOOLS_CATALOG
    catalog_interface = OrtoolsCatalog


def main() -> None:
    """Run the worker (stdio by default; pass ``--http`` for the HTTP server)."""
    OrtoolsWorker.main()


def main_http() -> None:
    """Run the worker over HTTP (injects ``--http`` into the worker CLI)."""
    argv = sys.argv[1:]
    if "--http" not in argv:
        argv = ["--http", *argv]
    sys.argv = [sys.argv[0], *argv]
    OrtoolsWorker.main()
