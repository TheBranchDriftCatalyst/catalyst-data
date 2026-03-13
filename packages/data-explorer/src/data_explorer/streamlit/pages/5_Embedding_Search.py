"""Embedding Search — Semantic search over stored embeddings."""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st
from openai import OpenAI

from data_explorer.streamlit.config import get_llm_config, get_s3_config
from data_explorer.streamlit.data_client import DataClient
from data_explorer.streamlit.theme import apply_theme, get_plotly_template

st.set_page_config(page_title="Embedding Search", page_icon=":material/search:", layout="wide")
apply_theme()
st.header("Embedding Search")


@st.cache_resource
def _get_client() -> DataClient:
    cfg = get_s3_config()
    return DataClient(
        endpoint_url=cfg.endpoint_url,
        access_key=cfg.access_key,
        secret_key=cfg.secret_key,
        bucket=cfg.bucket,
    )


@st.cache_resource
def _get_openai() -> OpenAI:
    cfg = get_llm_config()
    return OpenAI(base_url=cfg.base_url, api_key=cfg.api_key)


@st.cache_data(ttl=300)
def _find_embedding_assets() -> list[dict]:
    client = _get_client()
    assets = client.list_assets()
    return [a for a in assets if "embedding" in a["asset"].lower() and a["layer"] == "gold"]


def _embed_query(text: str) -> list[float]:
    cfg = get_llm_config()
    client = _get_openai()
    resp = client.embeddings.create(input=text, model=cfg.embedding_model)
    return resp.data[0].embedding


embedding_assets = _find_embedding_assets()

if not embedding_assets:
    st.info("No embedding assets found (looking for gold/*embeddings*).")
    st.stop()

# --- Sidebar ---
with st.sidebar:
    st.subheader("Search Settings")
    asset_options = {f"{a['code_location']}/{a['asset']}": a for a in embedding_assets}
    selected_key = st.selectbox("Embedding source", list(asset_options.keys()))
    selected_asset = asset_options[selected_key]
    top_k = st.slider("Top K results", 5, 50, 10, step=5)

# --- Query input ---
query = st.text_input("Search query", placeholder="Enter a natural language query...")

if query:
    with st.spinner("Embedding query..."):
        try:
            query_vec = _embed_query(query)
        except Exception as e:
            st.error(f"Embedding failed: {e}")
            st.stop()

    with st.spinner("Searching embeddings..."):
        results = _get_client().search_embeddings(query_vec, selected_asset["root"], top_k=top_k)

    if results:
        st.success(f"Found {len(results)} results")
        df = pd.DataFrame(results)

        # Score column formatting
        if "score" in df.columns:
            df = df.sort_values("score", ascending=False)

        st.dataframe(df, use_container_width=True, height=400)

        # Result cards
        for i, row in enumerate(results):
            score = row.get("score", 0)
            text = row.get("text") or row.get("content") or row.get("chunk_text", "")
            doc_id = row.get("document_id") or row.get("id", f"result-{i}")

            with st.expander(f"**{doc_id}** (score: {score:.4f})"):
                if text:
                    st.text_area("Content", str(text), height=150, disabled=True, key=f"emb_result_{i}")
                remaining = {k: v for k, v in row.items() if k not in ("score", "text", "content", "chunk_text", "embedding", "vector")}
                if remaining:
                    st.json(remaining)
    else:
        st.warning("No results found.")

    # --- Optional: PCA scatter ---
    with st.expander("2D Embedding Visualization (PCA)"):
        st.caption("Loads all embeddings and projects to 2D. May be slow for large datasets.")
        if st.button("Generate visualization"):
            with st.spinner("Loading embeddings..."):
                rows = _get_client().load_data(selected_asset["root"], limit=2000)
            if rows:
                embeddings = []
                labels = []
                for r in rows:
                    emb = r.get("embedding") or r.get("vector")
                    if emb and isinstance(emb, list):
                        embeddings.append(emb)
                        labels.append(str(r.get("document_id") or r.get("id", ""))[:30])

                if len(embeddings) > 2:
                    mat = np.array(embeddings, dtype=np.float32)
                    # Simple PCA via SVD
                    mean = mat.mean(axis=0)
                    centered = mat - mean
                    _, _, vt = np.linalg.svd(centered, full_matrices=False)
                    projected = centered @ vt[:2].T

                    viz_df = pd.DataFrame({
                        "PC1": projected[:, 0],
                        "PC2": projected[:, 1],
                        "label": labels,
                    })
                    fig = px.scatter(viz_df, x="PC1", y="PC2", hover_data=["label"], title="Embedding Space (PCA)",
                                     color_discrete_sequence=["#00fcd6", "#c026d3", "#ff6ec7", "#00d4ff"])
                    fig.update_layout(height=500, **get_plotly_template()["layout"])
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    st.warning("Not enough embeddings for visualization.")
            else:
                st.warning("No embedding data loaded.")
