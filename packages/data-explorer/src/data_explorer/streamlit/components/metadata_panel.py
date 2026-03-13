"""Metadata display panel for _metadata.json content."""

from __future__ import annotations

import json

import streamlit as st


def render_metadata_panel(metadata: dict) -> None:
    """Render _metadata.json contents in a structured panel."""
    with st.container(border=True):
        st.subheader("Metadata", divider="gray")

        col1, col2 = st.columns(2)
        with col1:
            st.metric("Format", metadata.get("format", "unknown"))
            st.metric("Count", metadata.get("count", "—"))
        with col2:
            st.metric("Type", metadata.get("type", "unknown"))
            if "size_bytes" in metadata:
                size_mb = metadata["size_bytes"] / (1024 * 1024)
                st.metric("Size", f"{size_mb:.2f} MB")

        if "timestamp" in metadata:
            st.caption(f"Timestamp: {metadata['timestamp']}")

        if "fields" in metadata:
            st.markdown("**Schema fields:**")
            st.code(", ".join(metadata["fields"]))

        with st.expander("Raw JSON"):
            st.json(metadata)
