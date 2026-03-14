"""Semantic Explorer — Enhanced semantic search over stored embeddings."""

from __future__ import annotations

import numpy as np
import streamlit as st

from data_explorer.streamlit.config import get_s3_config
from data_explorer.streamlit.data_client import DataClient
from data_explorer.streamlit.llm_client import get_llm_client
from data_explorer.streamlit.navigation import get_nav_params, navigate_to, render_breadcrumbs
from data_explorer.streamlit.theme import apply_theme
from data_explorer.streamlit.components.embedding_scatter import (
    render_embedding_scatter,
    render_reduction_controls,
)
from data_explorer.streamlit.components.entity_chip import render_entity_chip_html

st.set_page_config(
    page_title="Semantic Explorer",
    page_icon=":material/search:",
    layout="wide",
)
apply_theme()
render_breadcrumbs([("Home", "app.py"), ("Semantic Explorer", None)])
st.header("Semantic Explorer")


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------


@st.cache_resource
def _get_client() -> DataClient:
    cfg = get_s3_config()
    return DataClient(
        endpoint_url=cfg.endpoint_url,
        access_key=cfg.access_key,
        secret_key=cfg.secret_key,
        bucket=cfg.bucket,
    )


# ---------------------------------------------------------------------------
# Cached helpers
# ---------------------------------------------------------------------------


@st.cache_data(ttl=300)
def _find_embedding_assets() -> list[dict]:
    client = _get_client()
    assets = client.list_assets()
    return [a for a in assets if "embedding" in a["asset"].lower() and a["layer"] == "gold"]


@st.cache_data(ttl=3600)
def _embed_query(text: str) -> list[float]:
    """Embed a query string via the LLM client (cached for 1 hour)."""
    return get_llm_client().embed(text)


@st.cache_data(ttl=300)
def _load_entities_for_source(source: str) -> list[dict]:
    """Load all NER entities for a given source."""
    return _get_client().load_entities(source)


# ---------------------------------------------------------------------------
# Asset discovery
# ---------------------------------------------------------------------------

embedding_assets = _find_embedding_assets()

if not embedding_assets:
    st.info("No embedding assets found (looking for gold/*embedding*).")
    st.stop()


# ---------------------------------------------------------------------------
# Sidebar controls
# ---------------------------------------------------------------------------

with st.sidebar:
    st.subheader("Search Settings")

    asset_options = {f"{a['code_location']}/{a['asset']}": a for a in embedding_assets}
    selected_key = st.selectbox("Embedding source", list(asset_options.keys()))
    selected_asset = asset_options[selected_key]

    top_k = st.slider("Top K results", 5, 50, 10, step=5)
    score_threshold = st.slider("Score threshold", 0.0, 1.0, 0.5, step=0.05)

reduction_method, reduction_params = render_reduction_controls()


# ---------------------------------------------------------------------------
# Nav params — pre-populate query from cross-page navigation
# ---------------------------------------------------------------------------

nav = get_nav_params()
default_query = nav.get("query_text", "")


# ---------------------------------------------------------------------------
# Query input
# ---------------------------------------------------------------------------

query = st.text_input(
    "Search query",
    value=default_query,
    placeholder="Enter a natural language query...",
)


# ---------------------------------------------------------------------------
# Search execution
# ---------------------------------------------------------------------------

if query:
    # 1. Embed query
    with st.spinner("Embedding query..."):
        try:
            query_vec = _embed_query(query)
        except Exception as e:
            st.error(f"Embedding failed: {e}")
            st.stop()

    # 2. Search embeddings
    with st.spinner("Searching embeddings..."):
        results = _get_client().search_embeddings(
            query_vec, selected_asset["root"], top_k=top_k,
        )

    # 3. Apply score threshold
    results = [r for r in results if r.get("score", 0) >= score_threshold]

    if not results:
        st.warning("No results above the score threshold.")
        st.stop()

    st.success(f"Found {len(results)} results above threshold ({score_threshold})")

    # 4. Load entities for the selected source (for chip rendering)
    source_code = selected_asset.get("code_location", "")
    all_entities: list[dict] = []
    if source_code:
        try:
            all_entities = _load_entities_for_source(source_code)
        except Exception:
            pass  # entities are optional enrichment

    # Build a chunk_id -> entities lookup
    chunk_entity_map: dict[str, list[dict]] = {}
    for ent in all_entities:
        cid = ent.get("chunk_id", "")
        if cid:
            chunk_entity_map.setdefault(cid, []).append(ent)

    # ------------------------------------------------------------------
    # Result cards
    # ------------------------------------------------------------------

    for i, row in enumerate(results):
        score = row.get("score", 0)
        text = row.get("text") or row.get("content") or row.get("chunk_text", "")
        doc_id = row.get("document_id") or row.get("source_doc_id") or row.get("id", f"result-{i}")
        chunk_id = row.get("chunk_id", "")

        with st.container(border=True):
            # -- Score bar + value
            score_col, label_col = st.columns([3, 1])
            with score_col:
                st.progress(min(score, 1.0))
            with label_col:
                st.metric("Score", f"{score:.4f}")

            # -- Text preview
            if text:
                preview = str(text)[:300]
                if len(str(text)) > 300:
                    preview += "..."
                st.markdown(
                    f'<div style="'
                    f"font-family:'Space Mono',monospace;"
                    f"font-size:0.82rem;"
                    f"line-height:1.6;"
                    f"color:#e4e4e7;"
                    f"background:#16161d;"
                    f"border:1px solid #27272a;"
                    f"border-radius:0.25rem;"
                    f"padding:0.75rem 1rem;"
                    f"white-space:pre-wrap;"
                    f"word-wrap:break-word;"
                    f'">{preview}</div>',
                    unsafe_allow_html=True,
                )

            # -- Document link
            if doc_id:
                if st.button(
                    f"Open document: {str(doc_id)[:60]}",
                    key=f"nav_doc_{i}",
                ):
                    navigate_to(
                        "pages/2_Document_Explorer.py",
                        document_id=doc_id,
                    )

            # -- Entity chips for this chunk
            chunk_entities = chunk_entity_map.get(chunk_id, [])
            if chunk_entities:
                # Deduplicate by (text, label)
                seen: set[tuple[str, str]] = set()
                unique_ents: list[dict] = []
                for ent in chunk_entities:
                    key = (ent.get("text", ""), ent.get("label", ""))
                    if key not in seen and key[0]:
                        seen.add(key)
                        unique_ents.append(ent)

                chips_html = " ".join(
                    render_entity_chip_html(
                        text=ent["text"],
                        label=ent.get("label", ""),
                    )
                    for ent in unique_ents[:12]
                )
                st.markdown(
                    f'<div style="display:flex;flex-wrap:wrap;gap:0.35rem;'
                    f'padding:0.4rem 0;">{chips_html}</div>',
                    unsafe_allow_html=True,
                )

            # -- Extra metadata
            extra = {
                k: v
                for k, v in row.items()
                if k not in (
                    "score", "text", "content", "chunk_text",
                    "embedding", "vector", "document_id",
                    "source_doc_id", "id", "chunk_id",
                )
            }
            if extra:
                with st.expander("Metadata"):
                    st.json(extra)

    # ------------------------------------------------------------------
    # Embedding scatter visualization
    # ------------------------------------------------------------------

    st.divider()
    st.subheader("Embedding Space Visualization")
    st.caption(
        "All embeddings from the selected asset projected to 2D. "
        "Search results are highlighted with white borders."
    )

    with st.spinner("Loading embeddings for visualization..."):
        all_rows = _get_client().load_data(selected_asset["root"], limit=2000)

    if all_rows:
        embeddings: list[list[float]] = []
        embed_labels: list[str] = []
        embed_metadata: list[dict] = []

        for r in all_rows:
            emb = r.get("embedding") or r.get("vector")
            if emb and isinstance(emb, list):
                embeddings.append(emb)
                embed_labels.append(
                    str(
                        r.get("document_id")
                        or r.get("source_doc_id")
                        or r.get("id", "")
                    )[:30]
                )
                embed_metadata.append(r)

        if len(embeddings) > 2:
            mat = np.array(embeddings, dtype=np.float32)

            # Determine highlight indices — match result doc/chunk IDs to
            # positions in the full embedding list.
            result_keys = set()
            for r in results:
                rid = r.get("chunk_id") or r.get("document_id") or r.get("id")
                if rid:
                    result_keys.add(str(rid))

            highlight_indices: list[int] = []
            for idx, r in enumerate(embed_metadata):
                rid = r.get("chunk_id") or r.get("document_id") or r.get("id")
                if rid and str(rid) in result_keys:
                    highlight_indices.append(idx)

            render_embedding_scatter(
                embeddings=mat,
                labels=None,
                metadata=[
                    {
                        "text": str(
                            m.get("text") or m.get("content") or m.get("chunk_text", "")
                        ),
                        "document_id": str(
                            m.get("document_id") or m.get("source_doc_id") or m.get("id", "")
                        ),
                    }
                    for m in embed_metadata
                ],
                highlight_indices=highlight_indices if highlight_indices else None,
                method=reduction_method,
                title="Embedding Space — Search Results Highlighted",
                **reduction_params,
            )
        else:
            st.warning("Not enough embeddings for visualization.")
    else:
        st.warning("No embedding data loaded for visualization.")
