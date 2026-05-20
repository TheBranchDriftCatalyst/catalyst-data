"""Filesystem paths anchored at a single data root.

One env var (``CATALYST_DATA_ROOT``) selects the root. The layout below it
is identical in prod and dev — same subdirs, same code paths.

- Prod (k8s): ``CATALYST_DATA_ROOT=/data`` (NFS mount via PVC).
- Dev (host): ``CATALYST_DATA_ROOT=$PROJECT_DIR/.dev-data`` (Tilt sets it
  + provisions the subdirs and symlinks the test fixtures into
  ``metube``/``tubesync``).

If you find yourself writing a second env override for a specific cache
or fixture root, stop — add it under this root instead.
"""

from __future__ import annotations

import os

CATALYST_DATA_ROOT = os.environ.get("CATALYST_DATA_ROOT", "/data")

METUBE_DIR = os.path.join(CATALYST_DATA_ROOT, "metube")
TUBESYNC_DIR = os.path.join(CATALYST_DATA_ROOT, "tubesync")
WHISPER_MODEL_CACHE = os.path.join(CATALYST_DATA_ROOT, "whisper-models")
