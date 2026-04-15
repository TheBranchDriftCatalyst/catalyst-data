"""Parameterized sensor closure test across all code locations.

Every sensor with an asset_selection must form a closed subgraph — all
partitioned parents of selected assets must also be selected. Otherwise
sensor-triggered runs fail at IO manager load time with NoSuchKey.

CD-x1u: Extends the media-ingest-only test to cover all 4 code locations.
"""

from __future__ import annotations

import pytest


def _load_defs(module_name: str):
    """Dynamically import a code location's Definitions object."""
    import importlib

    mod = importlib.import_module(module_name)
    return mod.defs


@pytest.mark.parametrize(
    "module_name",
    [
        "media_ingest",
        "congress_data",
        "open_leaks",
        "knowledge_graph",
    ],
)
def test_sensor_asset_selections_are_closed(module_name: str):
    """Every sensor's asset_selection forms a closed subgraph."""
    defs = _load_defs(module_name)
    graph = defs.resolve_asset_graph()
    sensors = defs.sensors or []

    for sensor in sensors:
        if sensor.asset_selection is None:
            continue
        selection = sensor.asset_selection.resolve(graph)
        for key in selection:
            node = graph.get(key)
            for parent in node.parent_keys:
                parent_node = graph.get(parent)
                if parent_node.partitions_def is None:
                    continue
                assert parent in selection, (
                    f"[{module_name}] Sensor '{sensor.name}' selects {key} but its "
                    f"partitioned parent {parent} is NOT in the selection. Runs will "
                    f"fail loading {parent} from the IO manager."
                )
