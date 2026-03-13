"""Asset Browser — Medallion tree nav, metadata cards, data preview."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from data_explorer.streamlit.components.asset_card import render_asset_card
from data_explorer.streamlit.components.manifest_timeline import render_manifest_timeline
from data_explorer.streamlit.components.metadata_panel import render_metadata_panel
from data_explorer.streamlit.config import get_s3_config
from data_explorer.streamlit.data_client import DataClient

st.set_page_config(page_title="Asset Browser", page_icon=":material/folder_open:", layout="wide")
st.header("Asset Browser")


@st.cache_resource
def _get_client() -> DataClient:
    cfg = get_s3_config()
    return DataClient(
        endpoint_url=cfg.endpoint_url,
        access_key=cfg.access_key,
        secret_key=cfg.secret_key,
        bucket=cfg.bucket,
    )


@st.cache_data(ttl=300)
def _list_assets() -> list[dict]:
    return _get_client().list_assets()


client = _get_client()
assets = _list_assets()

if not assets:
    st.warning("No assets found in S3.")
    st.stop()

# --- Sidebar tree navigation ---
with st.sidebar:
    st.subheader("Navigate")

    if st.button("Refresh"):
        st.cache_data.clear()
        st.rerun()

    # Build tree: layer -> code_location -> asset
    layers = sorted({a["layer"] for a in assets})
    selected_layer = st.selectbox("Layer", layers)

    filtered = [a for a in assets if a["layer"] == selected_layer]
    code_locs = sorted({a["code_location"] for a in filtered})
    selected_cl = st.selectbox("Code Location", code_locs)

    filtered = [a for a in filtered if a["code_location"] == selected_cl]
    asset_names = sorted({a["asset"] for a in filtered})
    selected_asset = st.selectbox("Asset", asset_names)

# --- Find selected asset ---
asset = next((a for a in filtered if a["asset"] == selected_asset), None)
if not asset:
    st.stop()

# --- Asset card ---
metadata = client.get_metadata(asset["root"])
render_asset_card(asset, metadata)

# --- Metadata panel ---
if metadata:
    render_metadata_panel(metadata)

# --- Manifest timeline ---
manifest = client.get_manifest(asset["root"])
if manifest:
    render_manifest_timeline(manifest)

# --- Data preview ---
st.subheader("Data Preview")
preview_limit = st.slider("Rows to load", 10, 500, 50, step=10)

if st.button("Load data"):
    with st.spinner("Loading..."):
        rows = client.load_data(asset["root"], limit=preview_limit)
    if rows:
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, height=400)
        st.caption(f"Showing {len(df)} rows")
    else:
        st.info("No data files found for this asset.")

# --- Raw keys ---
with st.expander("S3 keys"):
    data_keys = client.list_data_keys(asset["root"])
    for k in data_keys:
        st.code(k, language=None)
