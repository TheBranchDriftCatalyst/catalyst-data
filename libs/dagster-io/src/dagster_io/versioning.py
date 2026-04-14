"""Code versioning for Dagster assets — content-hash module source files.

When a Dagster code server starts (new Docker image deploy), it computes
code_version from the source files of modules that affect each asset's
output. If the hash differs from the last materialization, Dagster marks
the asset as "stale" in the UI. Assets with AutoMaterializePolicy.eager()
will auto-rematerialize; others wait for manual trigger.

Usage:
    from dagster_io.versioning import code_version_from_modules
    import dagster_io.concordance as _concordance

    @asset(code_version=code_version_from_modules(_concordance))
    def my_asset(...): ...
"""

from __future__ import annotations

import hashlib
import inspect
from types import ModuleType


def code_version_from_modules(*modules: ModuleType) -> str:
    """Compute a stable content hash from Python module source files.

    Pass the modules whose changes should trigger rematerialization.
    Returns a 12-char hex digest — short enough for Dagster UI, long
    enough to avoid collisions.

    The hash is computed once at import time (code server startup) and
    stays fixed for the process lifetime. A new Docker image deploy
    restarts the code server → new hash → Dagster marks asset stale.
    """
    h = hashlib.sha256()
    for mod in sorted(modules, key=lambda m: m.__name__):
        try:
            source_file = inspect.getfile(mod)
            with open(source_file, "rb") as f:
                h.update(f.read())
        except (TypeError, OSError):
            # Built-in modules or C extensions without source files
            h.update(mod.__name__.encode())
    return h.hexdigest()[:12]
