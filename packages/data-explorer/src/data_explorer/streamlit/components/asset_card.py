"""Reusable asset summary card widget."""

from __future__ import annotations

import streamlit as st


def render_asset_card(asset: dict, metadata: dict | None = None) -> None:
    """Render a compact card summarizing an asset."""
    with st.container(border=True):
        st.subheader(asset["asset"], divider="blue")
        cols = st.columns(4)
        cols[0].caption("Layer")
        cols[0].write(f"**{asset['layer']}**")
        cols[1].caption("Code Location")
        cols[1].write(f"**{asset['code_location']}**")
        cols[2].caption("Group")
        cols[2].write(f"**{asset['group']}**")

        if metadata:
            fmt = metadata.get("format", "unknown")
            count = metadata.get("count", "—")
            cols[3].caption("Format / Count")
            cols[3].write(f"**{fmt}** · {count} rows")
            if "timestamp" in metadata:
                st.caption(f"Last materialized: {metadata['timestamp']}")
            if "fields" in metadata:
                st.caption(f"Fields: {', '.join(metadata['fields'])}")
