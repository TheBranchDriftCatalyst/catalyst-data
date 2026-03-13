"""Materialization history timeline chart from _manifest.json."""

from __future__ import annotations

import plotly.express as px
import plotly.io as pio
import streamlit as st
from dagster_io.manifest import AssetManifest

from data_explorer.streamlit.theme import get_plotly_template


def render_manifest_timeline(manifest: AssetManifest) -> None:
    """Render a materialization timeline from an asset manifest."""
    records = manifest.materializations
    if not records:
        st.info("No materialization history available.")
        return

    data = [
        {
            "timestamp": r.timestamp,
            "count": r.count,
            "size_mb": r.size_bytes / (1024 * 1024) if r.size_bytes else 0,
            "format": r.format,
            "run_id": r.run_id[:8],
            "partition": r.partition or "—",
        }
        for r in records
    ]

    fig = px.scatter(
        data,
        x="timestamp",
        y="count",
        size="size_mb",
        color="format",
        hover_data=["run_id", "partition"],
        title=f"Materializations — {manifest.asset}",
        labels={"timestamp": "Time", "count": "Row Count", "size_mb": "Size (MB)"},
    )
    fig.update_layout(height=350, margin=dict(t=40, b=20), **get_plotly_template()["layout"])
    st.plotly_chart(fig, use_container_width=True)

    with st.expander(f"History ({len(records)} records)"):
        st.dataframe(data, use_container_width=True)
