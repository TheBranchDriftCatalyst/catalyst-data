"""Document Explorer — Browse documents across all pipelines."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from data_explorer.streamlit.config import get_s3_config
from data_explorer.streamlit.data_client import DataClient

st.set_page_config(page_title="Document Explorer", page_icon=":material/description:", layout="wide")
st.header("Document Explorer")


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
def _load_document_assets() -> list[dict]:
    """Find all silver *_documents assets."""
    client = _get_client()
    assets = client.list_assets()
    return [a for a in assets if "document" in a["asset"].lower() and a["layer"] == "silver"]


@st.cache_data(ttl=300)
def _load_documents(asset_root: str, limit: int) -> list[dict]:
    return _get_client().load_data(asset_root, limit=limit)


client = _get_client()
doc_assets = _load_document_assets()

if not doc_assets:
    st.info("No document assets found (looking for silver/*_documents).")
    st.stop()

# --- Sidebar ---
with st.sidebar:
    st.subheader("Document Sources")
    asset_options = {f"{a['code_location']}/{a['asset']}": a for a in doc_assets}
    selected_key = st.selectbox("Source", list(asset_options.keys()))
    selected_asset = asset_options[selected_key]
    load_limit = st.slider("Max documents", 50, 1000, 200, step=50)

    if st.button("Refresh"):
        st.cache_data.clear()
        st.rerun()

# --- Load documents ---
docs = _load_documents(selected_asset["root"], load_limit)
if not docs:
    st.warning("No documents found in this asset.")
    st.stop()

df = pd.DataFrame(docs)
st.caption(f"Loaded {len(df)} documents from **{selected_key}**")

# --- Faceted filtering ---
col1, col2, col3 = st.columns(3)

with col1:
    if "source" in df.columns:
        sources = ["All"] + sorted(df["source"].dropna().unique().tolist())
        sel_source = st.selectbox("Source", sources)
        if sel_source != "All":
            df = df[df["source"] == sel_source]

with col2:
    if "document_type" in df.columns:
        doc_types = ["All"] + sorted(df["document_type"].dropna().unique().tolist())
        sel_type = st.selectbox("Type", doc_types)
        if sel_type != "All":
            df = df[df["document_type"] == sel_type]

with col3:
    if "domain" in df.columns:
        domains = ["All"] + sorted(df["domain"].dropna().unique().tolist())
        sel_domain = st.selectbox("Domain", domains)
        if sel_domain != "All":
            df = df[df["domain"] == sel_domain]

# --- Full-text search ---
search = st.text_input("Search documents", placeholder="Type to filter by title or content...")
if search:
    mask = pd.Series(False, index=df.index)
    for col in ["title", "content", "id"]:
        if col in df.columns:
            mask |= df[col].astype(str).str.contains(search, case=False, na=False)
    df = df[mask]

st.metric("Matching documents", len(df))

# --- Document cards ---
for _, row in df.head(50).iterrows():
    with st.expander(f"**{row.get('title', row.get('id', 'Untitled'))}**"):
        meta_cols = st.columns(4)
        for i, field in enumerate(["source", "document_type", "domain", "entity_type"]):
            if field in row and pd.notna(row[field]):
                meta_cols[i % 4].caption(field)
                meta_cols[i % 4].write(str(row[field]))

        if "content" in row and pd.notna(row["content"]):
            content = str(row["content"])
            if len(content) > 2000:
                st.text_area("Content", content[:2000] + "\n\n... (truncated)", height=300, disabled=True)
            else:
                st.text_area("Content", content, height=200, disabled=True)

        if "source_url" in row and pd.notna(row["source_url"]):
            st.markdown(f"[Source link]({row['source_url']})")

        if "sections" in row and isinstance(row["sections"], dict) and row["sections"]:
            st.markdown("**Sections:**")
            for sec_name, sec_content in row["sections"].items():
                st.markdown(f"*{sec_name}*")
                st.text(str(sec_content)[:500])

if len(df) > 50:
    st.info(f"Showing first 50 of {len(df)} matching documents.")
