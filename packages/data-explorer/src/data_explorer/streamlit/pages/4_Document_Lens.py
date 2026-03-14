"""Document Lens — Deep-read page that renders a document with NLP overlays."""

from __future__ import annotations

from collections import Counter

import pandas as pd
import streamlit as st

from data_explorer.streamlit.config import get_s3_config
from data_explorer.streamlit.data_client import DataClient
from data_explorer.streamlit.theme import apply_theme
from data_explorer.streamlit.navigation import get_nav_params, render_breadcrumbs
from data_explorer.streamlit.components.document_renderer import (
    render_document,
    render_entity_legend,
)
from data_explorer.streamlit.components.entity_chip import render_entity_chip_list

st.set_page_config(
    page_title="Document Lens",
    page_icon=":material/auto_stories:",
    layout="wide",
)
apply_theme()

# ---------------------------------------------------------------------------
# Client / cached loaders
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


@st.cache_data(ttl=300, show_spinner="Listing sources...")
def _list_sources() -> list[str]:
    return _get_client().list_sources()


@st.cache_data(ttl=300, show_spinner="Loading documents...")
def _load_documents(source: str, limit: int = 2000) -> list[dict]:
    return _get_client().load_documents(source, limit=limit)


@st.cache_data(ttl=300, show_spinner="Loading chunks...")
def _load_chunks(source: str) -> list[dict]:
    return _get_client().load_chunks(source)


@st.cache_data(ttl=300, show_spinner="Loading entities...")
def _load_document_entities(document_id: str, source: str) -> list[dict]:
    return _get_client().get_document_entities(document_id, source)


@st.cache_data(ttl=300, show_spinner="Loading propositions...")
def _load_document_propositions(document_id: str, source: str) -> list[dict]:
    return _get_client().get_document_propositions(document_id, source)


# ---------------------------------------------------------------------------
# Nav params (pre-selection from other pages)
# ---------------------------------------------------------------------------

nav = get_nav_params()
nav_source: str | None = nav.get("source")
nav_document_id: str | None = nav.get("document_id")

# ---------------------------------------------------------------------------
# Source selector (sidebar)
# ---------------------------------------------------------------------------

client = _get_client()
sources = _list_sources()

if not sources:
    st.warning("No data sources found in S3.")
    st.stop()

with st.sidebar:
    st.subheader("Document Lens")

    source_index = 0
    if nav_source and nav_source in sources:
        source_index = sources.index(nav_source)

    selected_source = st.selectbox("Source", sources, index=source_index, key="dl_source")

    # --- Load documents for the selected source ---
    documents = _load_documents(selected_source)

    if not documents:
        st.warning(f"No documents found for source **{selected_source}**.")
        st.stop()

    # Build display labels: prefer title, fall back to id
    doc_labels: list[str] = []
    doc_id_map: dict[str, dict] = {}
    for doc in documents:
        doc_id = doc.get("id", "")
        title = doc.get("title") or doc_id or "Untitled"
        label = f"{title}" if title != doc_id else doc_id
        doc_labels.append(label)
        doc_id_map[label] = doc

    doc_index = 0
    if nav_document_id:
        for i, doc in enumerate(documents):
            if doc.get("id") == nav_document_id:
                doc_index = i
                break

    selected_label = st.selectbox(
        "Document", doc_labels, index=doc_index, key="dl_document"
    )

    st.divider()
    show_chunks = st.toggle("Show chunk boundaries", value=True, key="dl_chunks")
    show_entities = st.toggle("Show entity highlights", value=True, key="dl_entities")

    if st.button("Refresh", key="dl_refresh"):
        st.cache_data.clear()
        st.rerun()

# ---------------------------------------------------------------------------
# Resolve selected document
# ---------------------------------------------------------------------------

selected_doc = doc_id_map[selected_label]
document_id = selected_doc.get("id", "")
document_title = selected_doc.get("title") or document_id or "Untitled"
document_text = selected_doc.get("content", "")

# ---------------------------------------------------------------------------
# Breadcrumbs & header
# ---------------------------------------------------------------------------

render_breadcrumbs([("Home", "app.py"), ("Document Lens", None)])
st.header("Document Lens")
st.caption(f"**{document_title}**  |  source: `{selected_source}`  |  id: `{document_id}`")

# ---------------------------------------------------------------------------
# Load NLP overlays
# ---------------------------------------------------------------------------

chunks: list[dict] = []
entities: list[dict] = []
propositions: list[dict] = []

if document_id:
    if show_chunks or show_entities:
        all_chunks = _load_chunks(selected_source)
        chunks = [c for c in all_chunks if c.get("document_id") == document_id]
        chunks.sort(key=lambda c: c.get("index", 0))

    if show_entities:
        entities = _load_document_entities(document_id, selected_source)

    propositions = _load_document_propositions(document_id, selected_source)

# ---------------------------------------------------------------------------
# Entity legend
# ---------------------------------------------------------------------------

if show_entities and entities:
    render_entity_legend()

# ---------------------------------------------------------------------------
# Document rendering
# ---------------------------------------------------------------------------

if not document_text and not chunks:
    st.info("No document content available.")
    st.stop()

# If we have chunks but no top-level content, reconstruct from chunks
effective_text = document_text
if not effective_text and chunks:
    effective_text = "\n\n".join(c.get("text", "") for c in chunks)

render_document(
    text=effective_text,
    entities=entities if show_entities else None,
    chunks=chunks if show_chunks else None,
    show_chunk_boundaries=show_chunks,
)

# ---------------------------------------------------------------------------
# Metrics bar
# ---------------------------------------------------------------------------

m1, m2, m3, m4 = st.columns(4)
m1.metric("Characters", f"{len(effective_text):,}")
m2.metric("Chunks", len(chunks))
m3.metric("Entities", len(entities))
m4.metric("Propositions", len(propositions))

st.divider()

# ---------------------------------------------------------------------------
# Bottom panel: Entity summary + Propositions table
# ---------------------------------------------------------------------------

col_entities, col_propositions = st.columns(2)

# --- Left column: Entity summary ---
with col_entities:
    st.subheader("Entity Summary")

    if entities:
        # Group entities by label and count occurrences
        label_groups: dict[str, list[dict]] = {}
        for e in entities:
            label = e.get("label", "UNKNOWN")
            label_groups.setdefault(label, []).append(e)

        for label in sorted(label_groups.keys()):
            group = label_groups[label]
            st.markdown(f"**{label}** ({len(group)} mentions)")

            # Count unique entity texts within this label
            text_counts = Counter(e.get("text", "") for e in group if e.get("text"))
            chip_data = [
                {"text": text, "label": label, "count": count}
                for text, count in text_counts.most_common()
            ]

            render_entity_chip_list(chip_data, max_display=12, columns=3)
    else:
        st.info("No entities found for this document.")

# --- Right column: Propositions table ---
with col_propositions:
    st.subheader("Propositions")

    if propositions:
        prop_df = pd.DataFrame(propositions)
        display_cols = [
            c for c in ["subject", "predicate", "object", "confidence"]
            if c in prop_df.columns
        ]
        if display_cols:
            st.dataframe(
                prop_df[display_cols],
                use_container_width=True,
                height=400,
            )
        else:
            st.dataframe(prop_df, use_container_width=True, height=400)
    else:
        st.info("No propositions found for this document.")
