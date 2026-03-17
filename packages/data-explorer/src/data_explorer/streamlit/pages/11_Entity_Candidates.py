"""Entity Candidates Browser — explore how mentions collapse into entity candidates."""

from __future__ import annotations

from collections import Counter

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dagster_io.logging import get_logger
from data_explorer.streamlit.components.entity_chip import (
    render_entity_chip,
    render_entity_chip_list,
)
from data_explorer.streamlit.config import get_s3_config
from data_explorer.streamlit.data_client import DataClient
from data_explorer.streamlit.navigation import (
    ENTITY_COLORS,
    navigate_to,
    render_breadcrumbs,
)
from data_explorer.streamlit.theme import apply_theme, get_plotly_template

logger = get_logger(__name__)

st.set_page_config(
    page_title="Entity Candidates",
    page_icon=":material/groups:",
    layout="wide",
)
apply_theme()
render_breadcrumbs([("Home", "app.py"), ("Entity Candidates", None)])
st.header("Entity Candidates Browser")
st.caption("Explore how mentions collapse into entity candidates via concordance resolution")


# ---------------------------------------------------------------------------
# Data access
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


@st.cache_data(ttl=300, show_spinner="Discovering sources...")
def _list_sources(endpoint_url: str, access_key: str, secret_key: str, bucket: str) -> list[str]:
    client = DataClient(endpoint_url=endpoint_url, access_key=access_key, secret_key=secret_key, bucket=bucket)
    return client.list_sources()


@st.cache_data(ttl=300, show_spinner="Loading entity candidates...")
def _load_entity_candidates(
    endpoint_url: str, access_key: str, secret_key: str, bucket: str, source: str,
) -> list[dict]:
    client = DataClient(endpoint_url=endpoint_url, access_key=access_key, secret_key=secret_key, bucket=bucket)
    return client.load_entity_candidates(source)


@st.cache_data(ttl=300, show_spinner="Loading mentions...")
def _load_mentions(
    endpoint_url: str, access_key: str, secret_key: str, bucket: str, source: str,
) -> list[dict]:
    client = DataClient(endpoint_url=endpoint_url, access_key=access_key, secret_key=secret_key, bucket=bucket)
    return client.load_mentions(source)


client = _get_client()
conn = (client._endpoint_url, client._access_key, client._secret_key, client._bucket)
sources = _list_sources(*conn)

if not sources:
    st.info("No data sources found. Ensure pipelines have produced entity_candidates assets.")
    st.stop()


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.subheader("Source")
    selected_source = st.selectbox("Source", sources, key="ec_source")

    st.subheader("Filters")
    entity_types = ["All", "PERSON", "ORG", "GPE", "DATE", "LAW", "EVENT", "LOC", "MONEY", "NORP", "FACILITY"]
    selected_type = st.selectbox("Entity Type", entity_types, key="ec_type")

    search_query = st.text_input("Search by name/alias", key="ec_search")

    min_mentions = st.slider("Min mention count", 1, 50, 1, key="ec_min_mentions")

    sort_by = st.radio(
        "Sort by",
        options=["Mention Count", "Alphabetical", "Alias Count"],
        index=0,
        key="ec_sort",
    )


# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------

candidates = _load_entity_candidates(*conn, source=selected_source)
mentions = _load_mentions(*conn, source=selected_source)

if not candidates:
    st.info(
        f"No entity candidates found for source **{selected_source}**. "
        "Ensure the entity_candidates pipeline has been materialized."
    )
    st.stop()

# Build mention lookup by candidate_id
mention_by_candidate: dict[str, list[dict]] = {}
for m in mentions:
    # Mentions might link to candidates via candidate_id or entity_candidate_id
    cid = m.get("candidate_id") or m.get("entity_candidate_id", "")
    if cid:
        mention_by_candidate.setdefault(cid, []).append(m)

logger.info("Loaded %d candidates and %d mentions for source=%s", len(candidates), len(mentions), selected_source)


# ---------------------------------------------------------------------------
# Filter candidates
# ---------------------------------------------------------------------------

filtered = candidates
if selected_type != "All":
    filtered = [c for c in filtered if c.get("entity_type", "").upper() == selected_type]

if search_query:
    q = search_query.lower()
    filtered = [
        c for c in filtered
        if q in c.get("representative_name", "").lower()
        or any(q in a.lower() for a in c.get("aliases", []))
    ]

if min_mentions > 1:
    filtered = [c for c in filtered if c.get("mention_count", 0) >= min_mentions]

# Sort
if sort_by == "Mention Count":
    filtered.sort(key=lambda c: c.get("mention_count", 0), reverse=True)
elif sort_by == "Alphabetical":
    filtered.sort(key=lambda c: c.get("representative_name", "").lower())
elif sort_by == "Alias Count":
    filtered.sort(key=lambda c: len(c.get("aliases", [])), reverse=True)


# ---------------------------------------------------------------------------
# Summary stats
# ---------------------------------------------------------------------------

s1, s2, s3, s4 = st.columns(4)
s1.metric("Total Candidates", len(candidates))
s2.metric("Filtered", len(filtered))
total_mentions = sum(c.get("mention_count", 0) for c in candidates)
s3.metric("Total Mentions", total_mentions)
avg_aliases = sum(len(c.get("aliases", [])) for c in candidates) / max(len(candidates), 1)
s4.metric("Avg Aliases/Candidate", f"{avg_aliases:.1f}")

st.divider()


# ---------------------------------------------------------------------------
# Distribution charts
# ---------------------------------------------------------------------------

chart_col1, chart_col2 = st.columns(2)

with chart_col1:
    st.markdown("**Entity Type Distribution**")
    type_counts: Counter[str] = Counter(c.get("entity_type", "UNKNOWN") for c in candidates)
    if type_counts:
        types = sorted(type_counts.keys(), key=lambda k: type_counts[k], reverse=True)
        colors = [ENTITY_COLORS.get(t, "#a1a1aa") for t in types]
        counts = [type_counts[t] for t in types]
        fig = go.Figure(
            go.Bar(
                x=types,
                y=counts,
                marker_color=colors,
                text=counts,
                textposition="auto",
                textfont=dict(color="#e4e4e7", family="Space Mono, monospace", size=11),
            )
        )
        fig.update_layout(template=get_plotly_template())
        fig.update_layout(
            height=300,
            xaxis_title="",
            yaxis_title="Count",
            margin=dict(t=10, b=40, l=40, r=10),
            showlegend=False,
        )
        st.plotly_chart(fig, use_container_width=True, key="ec_type_dist")

with chart_col2:
    st.markdown("**Mention Count Distribution**")
    mention_counts = [c.get("mention_count", 0) for c in candidates]
    if mention_counts:
        import numpy as np
        bins = np.histogram(mention_counts, bins=min(20, max(mention_counts) - min(mention_counts) + 1) if max(mention_counts) > min(mention_counts) else 1)
        bin_edges = [(bins[1][i] + bins[1][i + 1]) / 2 for i in range(len(bins[1]) - 1)]
        fig = go.Figure(
            go.Bar(
                x=bin_edges,
                y=bins[0].tolist(),
                marker_color="#c026d3",
                textfont=dict(color="#e4e4e7", family="Space Mono, monospace", size=11),
            )
        )
        fig.update_layout(template=get_plotly_template())
        fig.update_layout(
            height=300,
            xaxis_title="Mentions per Candidate",
            yaxis_title="Count",
            margin=dict(t=10, b=40, l=40, r=10),
            showlegend=False,
        )
        st.plotly_chart(fig, use_container_width=True, key="ec_mention_dist")

st.divider()


# ---------------------------------------------------------------------------
# Candidate table
# ---------------------------------------------------------------------------

st.subheader(f"Entity Candidates ({len(filtered)})")

if not filtered:
    st.info("No candidates match the current filters.")
    st.stop()

df = pd.DataFrame([{
    "Name": c.get("representative_name", ""),
    "Type": c.get("entity_type", ""),
    "Mentions": c.get("mention_count", 0),
    "Aliases": ", ".join(c.get("aliases", [])[:3]),
    "Alias Count": len(c.get("aliases", [])),
} for c in filtered])

selected_idx = st.dataframe(
    df, use_container_width=True, hide_index=True,
    on_select="rerun", selection_mode="single-row",
)

# ---------------------------------------------------------------------------
# Detail view for selected candidate
# ---------------------------------------------------------------------------

if selected_idx and selected_idx.selection and selected_idx.selection.rows:
    row_idx = selected_idx.selection.rows[0]
    candidate = filtered[row_idx]

    st.divider()
    st.subheader(f"Candidate: {candidate.get('representative_name', '')}")

    tab_identity, tab_mentions, tab_resolution = st.tabs([
        "Identity", "Source Mentions", "Resolution Details",
    ])

    with tab_identity:
        id_col1, id_col2 = st.columns(2)
        with id_col1:
            st.write("**Representative Name:**", candidate.get("representative_name", ""))
            st.write("**Entity Type:**", candidate.get("entity_type", ""))
            st.write("**Mention Count:**", candidate.get("mention_count", 0))
            cid = candidate.get("candidate_id") or candidate.get("id", "")
            if cid:
                st.write("**Candidate ID:**")
                st.code(cid)

        with id_col2:
            aliases = candidate.get("aliases", [])
            if aliases:
                st.write("**Aliases:**")
                chip_data = [
                    {"text": a, "label": candidate.get("entity_type", "UNKNOWN"), "count": None}
                    for a in aliases
                ]
                render_entity_chip_list(chip_data, max_display=12, columns=3)
            else:
                st.caption("No aliases")

        # Navigation buttons
        st.divider()
        nav_col1, nav_col2 = st.columns(2)
        with nav_col1:
            if st.button("View in Entity Concordance", key="ec_nav_concordance"):
                navigate_to(
                    "pages/8_Entity_Concordance.py",
                    entity_text=candidate.get("representative_name", ""),
                    entity_label=candidate.get("entity_type", ""),
                )
        with nav_col2:
            if st.button("View in Knowledge Graph", key="ec_nav_kg"):
                navigate_to(
                    "pages/3_Knowledge_Graph.py",
                    entity_text=candidate.get("representative_name", ""),
                )

    with tab_mentions:
        cid = candidate.get("candidate_id") or candidate.get("id", "")
        candidate_mentions = mention_by_candidate.get(cid, [])

        if not candidate_mentions:
            # Fallback: match mentions by representative_name text
            rep_name = candidate.get("representative_name", "").lower()
            all_names = {rep_name} | {a.lower() for a in candidate.get("aliases", [])}
            candidate_mentions = [
                m for m in mentions
                if m.get("text", "").lower() in all_names
            ]

        if candidate_mentions:
            st.caption(f"{len(candidate_mentions)} source mentions")
            mention_df = pd.DataFrame([{
                "Text": m.get("text", ""),
                "Type": m.get("mention_type", ""),
                "Document": m.get("document_id", ""),
                "Chunk": m.get("chunk_id", ""),
                "Span": f"{m.get('span_start', '?')}-{m.get('span_end', '?')}",
                "Context": (m.get("context", "") or "")[:100],
                "Confidence": (m.get("provenance", {}) or {}).get("confidence", ""),
            } for m in candidate_mentions])
            st.dataframe(mention_df, use_container_width=True, hide_index=True, height=400)
        else:
            st.info("No linked mentions found for this candidate.")

    with tab_resolution:
        st.markdown("**Resolution Method Details**")
        resolution = candidate.get("resolution_metadata") or candidate.get("resolution", {})
        if resolution:
            st.json(resolution)
        else:
            st.caption("No resolution metadata available")

        # Show source document distribution
        cid = candidate.get("candidate_id") or candidate.get("id", "")
        candidate_mentions = mention_by_candidate.get(cid, [])
        if candidate_mentions:
            doc_counts: Counter[str] = Counter(
                m.get("document_id", "unknown") for m in candidate_mentions
                if m.get("document_id")
            )
            if doc_counts:
                st.divider()
                st.markdown("**Source Document Distribution**")
                doc_df = pd.DataFrame(
                    [{"Document": d, "Mentions": c} for d, c in doc_counts.most_common()],
                )
                st.dataframe(doc_df, use_container_width=True, hide_index=True)
