"""Config Comparison — Side-by-side evaluation of embedding configurations."""

from __future__ import annotations

import numpy as np
import streamlit as st

from data_explorer.streamlit.config import get_s3_config
from data_explorer.streamlit.data_client import DataClient
from data_explorer.streamlit.llm_client import get_llm_client
from data_explorer.streamlit.components.model_selector import embedding_model_selector
from data_explorer.streamlit.navigation import render_breadcrumbs
from data_explorer.streamlit.theme import apply_theme

st.set_page_config(
    page_title="Config Comparison",
    page_icon=":material/compare:",
    layout="wide",
)
apply_theme()
render_breadcrumbs([("Home", "app.py"), ("Config Comparison", None)])
st.header("Config Comparison")

# --------------------------------------------------------------------------- #
# Clients
# --------------------------------------------------------------------------- #


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
def _find_embedding_assets() -> list[dict]:
    client = _get_client()
    assets = client.list_assets()
    return [a for a in assets if "embedding" in a["asset"].lower() and a["layer"] == "gold"]


@st.cache_data(ttl=3600)
def _embed_query(text: str) -> list[float]:
    return get_llm_client().embed(text)


# --------------------------------------------------------------------------- #
# Embedding assets
# --------------------------------------------------------------------------- #

embedding_assets = _find_embedding_assets()

if not embedding_assets:
    st.info("No embedding assets found (looking for gold/*embedding*).")
    st.stop()


# --------------------------------------------------------------------------- #
# Sidebar — shared controls
# --------------------------------------------------------------------------- #

with st.sidebar:
    st.subheader("Comparison Settings")

    asset_options = {f"{a['code_location']}/{a['asset']}": a for a in embedding_assets}
    selected_key = st.selectbox("Embedding source", list(asset_options.keys()))
    selected_asset = asset_options[selected_key]

    query = st.text_input("Search query", placeholder="Enter a natural language query...")
    top_k = st.slider("Top K", 5, 30, 10, step=5)


# --------------------------------------------------------------------------- #
# Tabs
# --------------------------------------------------------------------------- #

tab_existing, tab_experiment = st.tabs(["Compare Existing", "Run Experiment"])


# =========================================================================== #
# Tab 1: Compare Existing Configs
# =========================================================================== #

with tab_existing:
    configs = _get_client().list_configs(selected_asset["root"])

    if len(configs) < 2:
        st.info(
            f"Only **{len(configs)}** config(s) found for this asset "
            f"(`{'`, `'.join(configs)}`). Run Dagster jobs with different "
            f"`EmbeddingConfigResource` settings to create more."
        )

    col_a, col_b = st.columns(2)

    with col_a:
        config_a = st.selectbox("Config A", configs, index=0, key="cfg_a")
    with col_b:
        default_b = min(1, len(configs) - 1)
        config_b = st.selectbox("Config B", configs, index=default_b, key="cfg_b")

    # Load metadata for both configs
    meta_a = _get_client().load_config_metadata(selected_asset["root"], config_a)
    meta_b = _get_client().load_config_metadata(selected_asset["root"], config_b)

    col_stat_a, col_stat_b = st.columns(2)

    with col_stat_a:
        st.subheader(f"Config A: `{config_a}`")
        if meta_a:
            ca, cb = st.columns(2)
            ca.metric("Chunks", meta_a.get("count", "?"))
            cb.metric("Format", meta_a.get("format", "?"))
            if meta_a.get("config_key"):
                st.caption(f"Config key: `{meta_a['config_key']}`")
            with st.expander("Full metadata"):
                st.json(meta_a)
        else:
            st.caption("No metadata available")

    with col_stat_b:
        st.subheader(f"Config B: `{config_b}`")
        if meta_b:
            ca, cb = st.columns(2)
            ca.metric("Chunks", meta_b.get("count", "?"))
            cb.metric("Format", meta_b.get("format", "?"))
            if meta_b.get("config_key"):
                st.caption(f"Config key: `{meta_b['config_key']}`")
            with st.expander("Full metadata"):
                st.json(meta_b)
        else:
            st.caption("No metadata available")

    # -- Search comparison -----------------------------------------------

    if query:
        with st.spinner("Embedding query..."):
            try:
                query_vec = _embed_query(query)
            except Exception as e:
                st.error(f"Embedding failed: {e}")
                st.stop()

        with st.spinner("Searching both configs..."):
            results_a = _get_client().search_embeddings_with_config(
                query_vec, selected_asset["root"], config_a, top_k=top_k,
            )
            results_b = _get_client().search_embeddings_with_config(
                query_vec, selected_asset["root"], config_b, top_k=top_k,
            )

        st.divider()
        st.subheader("Search Results")

        res_col_a, res_col_b = st.columns(2)

        def _render_results(results: list[dict], label: str, col) -> None:
            with col:
                st.markdown(f"**{label}** — {len(results)} results")
                for i, row in enumerate(results):
                    score = row.get("score", 0)
                    doc_id = row.get("document_id") or row.get("id", f"r-{i}")
                    text = str(row.get("text") or row.get("content") or row.get("chunk_text", ""))
                    preview = text[:150] + "..." if len(text) > 150 else text
                    with st.container(border=True):
                        sc, dc = st.columns([1, 3])
                        sc.metric("Score", f"{score:.4f}")
                        dc.caption(str(doc_id)[:60])
                        st.text(preview)

        _render_results(results_a, f"Config A: {config_a}", res_col_a)
        _render_results(results_b, f"Config B: {config_b}", res_col_b)

        # -- Overlap analysis -------------------------------------------------

        def _result_ids(results: list[dict]) -> set[str]:
            ids = set()
            for r in results:
                rid = r.get("chunk_id") or r.get("document_id") or r.get("id")
                if rid:
                    ids.add(str(rid))
            return ids

        ids_a = _result_ids(results_a)
        ids_b = _result_ids(results_b)
        shared = ids_a & ids_b

        scores_a = [r.get("score", 0) for r in results_a]
        scores_b = [r.get("score", 0) for r in results_b]

        st.divider()
        st.subheader("Overlap Analysis")
        oa, ob, oc = st.columns(3)
        oa.metric("Shared results", f"{len(shared)}/{top_k}")
        if scores_a:
            ob.metric("Avg score A", f"{np.mean(scores_a):.4f}")
        if scores_b:
            oc.metric("Avg score B", f"{np.mean(scores_b):.4f}")

        # Note about dimension mismatch
        model_a = (meta_a or {}).get("config_key", config_a)
        model_b = (meta_b or {}).get("config_key", config_b)
        if model_a != model_b:
            st.info(
                "These configs may use different embedding models. "
                "Raw similarity scores are not directly comparable across models."
            )


# =========================================================================== #
# Tab 2: Run Experiment
# =========================================================================== #

with tab_experiment:
    st.caption(
        "Re-chunk and re-embed a sample of documents with two configurations, "
        "then compare search results. Results are NOT saved to S3."
    )

    exp_col_a, exp_col_b = st.columns(2)

    with exp_col_a:
        st.subheader("Config A")
        exp_cs_a = st.slider("Chunk size", 200, 3000, 500, step=100, key="exp_cs_a")
        exp_co_a = st.slider("Chunk overlap", 0, 500, 100, step=50, key="exp_co_a")
        exp_model_a = embedding_model_selector(key="exp_emb_a", label="Embedding model")

    with exp_col_b:
        st.subheader("Config B")
        exp_cs_b = st.slider("Chunk size", 200, 3000, 1000, step=100, key="exp_cs_b")
        exp_co_b = st.slider("Chunk overlap", 0, 500, 200, step=50, key="exp_co_b")
        exp_model_b = embedding_model_selector(key="exp_emb_b", label="Embedding model")

    sample_limit = st.slider("Document sample limit", 1, 50, 10, step=1)

    if st.button("Run Experiment", type="primary", use_container_width=True):
        if not query:
            st.warning("Enter a search query in the sidebar first.")
            st.stop()

        # 1. Load sample documents from the selected source
        asset_name = selected_asset.get("asset", "")
        source_prefix = asset_name.rsplit("_", 1)[0] if "_" in asset_name else ""

        with st.spinner("Loading sample documents..."):
            docs = _get_client().load_chunks(source_prefix, limit=sample_limit * 20)

        if not docs:
            with st.spinner("Falling back to raw embedding data..."):
                docs = _get_client().load_data(selected_asset["root"], limit=sample_limit * 20)

        if not docs:
            st.error("No source documents found for re-chunking.")
            st.stop()

        # 2. Re-chunk with both configs
        try:
            from dagster_io.chunking import chunk_text
        except ImportError:
            st.error("dagster_io.chunking not available. Install dagster-io.")
            st.stop()

        doc_texts = []
        for d in docs[:sample_limit]:
            text = d.get("text") or d.get("content") or d.get("chunk_text", "")
            if text:
                doc_texts.append(str(text))

        if not doc_texts:
            st.error("No text content found in sample documents.")
            st.stop()

        combined_text = "\n\n---\n\n".join(doc_texts)

        with st.spinner("Chunking with Config A..."):
            chunks_a = chunk_text(combined_text, chunk_size=exp_cs_a, chunk_overlap=exp_co_a)

        with st.spinner("Chunking with Config B..."):
            chunks_b = chunk_text(combined_text, chunk_size=exp_cs_b, chunk_overlap=exp_co_b)

        ca, cb = st.columns(2)
        ca.metric("Config A chunks", len(chunks_a))
        cb.metric("Config B chunks", len(chunks_b))

        if not chunks_a or not chunks_b:
            st.warning("One or both configs produced zero chunks.")
            st.stop()

        # 3. Embed both sets
        llm_client = get_llm_client()

        progress = st.progress(0, text="Embedding Config A chunks...")
        try:
            embeddings_a = llm_client.embed_batch(chunks_a)
            progress.progress(50, text="Embedding Config B chunks...")
            embeddings_b = llm_client.embed_batch(chunks_b)
            progress.progress(100, text="Done!")
        except Exception as e:
            st.error(f"Embedding failed: {e}")
            st.stop()

        # 4. Embed query and search in-memory
        query_vec = _embed_query(query)
        q = np.array(query_vec, dtype=np.float32)

        def _search_inmemory(chunks: list[str], embeddings: list[list[float]], k: int) -> list[dict]:
            mat = np.array(embeddings, dtype=np.float32)
            norms = np.linalg.norm(mat, axis=1) * np.linalg.norm(q)
            norms = np.where(norms == 0, 1, norms)
            scores = mat @ q / norms
            top_idx = np.argsort(scores)[::-1][:k]
            results = []
            for idx in top_idx:
                preview = chunks[idx][:200] + "..." if len(chunks[idx]) > 200 else chunks[idx]
                results.append({
                    "text": preview,
                    "score": float(scores[idx]),
                    "chunk_index": int(idx),
                })
            return results

        exp_results_a = _search_inmemory(chunks_a, embeddings_a, top_k)
        exp_results_b = _search_inmemory(chunks_b, embeddings_b, top_k)

        # 5. Display results side-by-side
        st.divider()
        st.subheader("Experiment Results")

        er_col_a, er_col_b = st.columns(2)

        with er_col_a:
            st.markdown(f"**Config A** — cs={exp_cs_a}, co={exp_co_a}")
            for r in exp_results_a:
                with st.container(border=True):
                    st.metric("Score", f"{r['score']:.4f}")
                    st.text(r["text"])

        with er_col_b:
            st.markdown(f"**Config B** — cs={exp_cs_b}, co={exp_co_b}")
            for r in exp_results_b:
                with st.container(border=True):
                    st.metric("Score", f"{r['score']:.4f}")
                    st.text(r["text"])

        # Overlap analysis
        exp_scores_a = [r["score"] for r in exp_results_a]
        exp_scores_b = [r["score"] for r in exp_results_b]

        st.divider()
        oa, ob = st.columns(2)
        if exp_scores_a:
            oa.metric("Avg score A", f"{np.mean(exp_scores_a):.4f}")
        if exp_scores_b:
            ob.metric("Avg score B", f"{np.mean(exp_scores_b):.4f}")

        total_embed_calls = len(chunks_a) + len(chunks_b)
        st.caption(
            f"Experiment used {total_embed_calls} embedding API calls "
            f"({len(chunks_a)} + {len(chunks_b)} chunks)."
        )
