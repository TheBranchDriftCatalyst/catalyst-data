"""Data Explorer — Streamlit entry point."""

from __future__ import annotations

import streamlit as st

from data_explorer.streamlit.config import get_s3_config
from data_explorer.streamlit.data_client import DataClient
from data_explorer.streamlit.theme import apply_theme

st.set_page_config(
    page_title="Data Explorer",
    page_icon=":material/database:",
    layout="wide",
    initial_sidebar_state="expanded",
)
apply_theme()


@st.cache_resource
def _get_client() -> DataClient:
    cfg = get_s3_config()
    return DataClient(
        endpoint_url=cfg.endpoint_url,
        access_key=cfg.access_key,
        secret_key=cfg.secret_key,
        bucket=cfg.bucket,
    )


def main() -> None:
    client = _get_client()

    with st.sidebar:
        st.title("Data Explorer")
        st.caption(f"S3: `{client.s3.bucket}`")

        try:
            keys = client.s3.list_objects("")
            st.success(f"Connected — {len(keys)} top-level keys")
        except Exception as e:
            st.error(f"S3 connection failed: {e}")

        st.divider()

        # Asset catalog (cached)
        if st.button("Refresh catalog"):
            st.cache_data.clear()

    # Landing page
    st.header("Catalyst Data Explorer")
    st.markdown(
        "Browse materialized Dagster assets stored in MinIO S3. "
        "Use the sidebar pages to explore assets, documents, entities, media, and embeddings."
    )

    assets = client.list_assets()
    if assets:
        col1, col2, col3 = st.columns(3)
        layers = {}
        for a in assets:
            layers.setdefault(a["layer"], []).append(a)
        col1.metric("Assets", len(assets))
        col2.metric("Layers", len(layers))
        col3.metric("Code Locations", len({a["code_location"] for a in assets}))

        for layer in sorted(layers):
            with st.expander(f"**{layer}** ({len(layers[layer])} assets)", expanded=False):
                for a in sorted(layers[layer], key=lambda x: x["asset"]):
                    st.text(f"  {a['code_location']}/{a['asset']}")
    else:
        st.info("No assets found. Check S3 connection and bucket contents.")


# Streamlit pages are discovered from the pages/ directory alongside this file.
# This file serves as the home page.
main()
