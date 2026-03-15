"""Cross-Source Linker — Explore canonical entities resolved across all data sources."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dagster_io.logging import get_logger
from data_explorer.streamlit.config import get_s3_config
from data_explorer.streamlit.data_client import DataClient
from data_explorer.streamlit.navigation import (
    ENTITY_COLORS,
    navigate_to,
    render_breadcrumbs,
)
from data_explorer.streamlit.theme import apply_theme, get_plotly_template

logger = get_logger(__name__)

st.set_page_config(page_title="Cross-Source Linker", page_icon=":material/link:", layout="wide")
apply_theme()
render_breadcrumbs([
    ("Home", "app.py"),
    ("Cross-Source Linker", None),
])
st.title("Cross-Source Entity Linker")
st.caption("Explore canonical entities resolved across all data sources")


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


@st.cache_data(ttl=300, show_spinner="Loading canonical entities...")
def _load_canonical_entities(endpoint_url: str, access_key: str, secret_key: str, bucket: str) -> list[dict]:
    client = DataClient(endpoint_url=endpoint_url, access_key=access_key, secret_key=secret_key, bucket=bucket)
    return client.load_canonical_entities()


@st.cache_data(ttl=300, show_spinner="Loading entity alignments...")
def _load_entity_alignments(endpoint_url: str, access_key: str, secret_key: str, bucket: str) -> list[dict]:
    client = DataClient(endpoint_url=endpoint_url, access_key=access_key, secret_key=secret_key, bucket=bucket)
    return client.load_entity_alignments()


client = _get_client()

# ---------------------------------------------------------------------------
# Load platinum-layer data
# ---------------------------------------------------------------------------

with st.spinner("Loading platinum-layer entities..."):
    canonical_entities = _load_canonical_entities(
        client._endpoint_url, client._access_key, client._secret_key, client._bucket,
    )
    alignments = _load_entity_alignments(
        client._endpoint_url, client._access_key, client._secret_key, client._bucket,
    )

if not canonical_entities:
    st.warning("No canonical entities found. Run the knowledge-graph pipeline first.")
    st.stop()

logger.info("Loaded %d canonical entities and %d alignments", len(canonical_entities), len(alignments))

# ---------------------------------------------------------------------------
# Summary stats
# ---------------------------------------------------------------------------

col1, col2, col3, col4 = st.columns(4)
col1.metric("Canonical Entities", len(canonical_entities))
col2.metric("Alignment Edges", len(alignments))

multi_source = [e for e in canonical_entities if len(e.get("source_code_locations", [])) > 1]
col3.metric("Multi-Source Entities", len(multi_source))

avg_mentions = sum(e.get("mention_count", 0) for e in canonical_entities) / max(len(canonical_entities), 1)
col4.metric("Avg Mentions/Entity", f"{avg_mentions:.1f}")

st.divider()

# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------

filter_col1, filter_col2, filter_col3 = st.columns(3)
with filter_col1:
    entity_types = sorted(set(e.get("entity_type", "UNKNOWN") for e in canonical_entities))
    selected_type = st.selectbox("Entity Type", ["All"] + entity_types)
with filter_col2:
    all_sources = sorted(set(s for e in canonical_entities for s in e.get("source_code_locations", [])))
    selected_source = st.selectbox("Source", ["All"] + all_sources)
with filter_col3:
    search_query = st.text_input("Search by name/alias")

# Apply filters
filtered = canonical_entities
if selected_type != "All":
    filtered = [e for e in filtered if e.get("entity_type") == selected_type]
if selected_source != "All":
    filtered = [e for e in filtered if selected_source in e.get("source_code_locations", [])]
if search_query:
    q = search_query.lower()
    filtered = [
        e for e in filtered
        if q in e.get("canonical_name", "").lower()
        or any(q in a.lower() for a in e.get("aliases", []))
    ]

# ---------------------------------------------------------------------------
# Entity table
# ---------------------------------------------------------------------------

st.subheader(f"Canonical Entities ({len(filtered)})")

if filtered:
    df = pd.DataFrame([{
        "Name": e.get("canonical_name", ""),
        "Type": e.get("entity_type", ""),
        "Sources": ", ".join(e.get("source_code_locations", [])),
        "Mentions": e.get("mention_count", 0),
        "Aliases": ", ".join(e.get("aliases", [])[:3]),
    } for e in filtered])

    selected_idx = st.dataframe(
        df, use_container_width=True, hide_index=True,
        on_select="rerun", selection_mode="single-row",
    )

    # Detail view for selected entity
    if selected_idx and selected_idx.selection and selected_idx.selection.rows:
        row_idx = selected_idx.selection.rows[0]
        entity = filtered[row_idx]

        st.divider()
        st.subheader(f"Entity: {entity.get('canonical_name', '')}")

        tab1, tab2, tab3 = st.tabs(["Identity", "Alignments", "Source Candidates"])

        with tab1:
            id_col1, id_col2 = st.columns(2)
            with id_col1:
                st.write("**Type:**", entity.get("entity_type", ""))
                st.write("**Canonical Name:**", entity.get("canonical_name", ""))
                st.write("**Mention Count:**", entity.get("mention_count", 0))
                st.write("**Sources:**", ", ".join(entity.get("source_code_locations", [])))
            with id_col2:
                st.write("**Aliases:**")
                for alias in entity.get("aliases", []):
                    st.write(f"  - {alias}")
                if entity.get("description"):
                    st.write("**Description:**", entity.get("description", ""))

            # Navigation to Entity Concordance
            if st.button("View in Entity Concordance", key="nav_concordance"):
                navigate_to(
                    "pages/8_Entity_Concordance.py",
                    entity_text=entity.get("canonical_name", ""),
                    entity_label=entity.get("entity_type", ""),
                )

        with tab2:
            entity_id = entity.get("canonical_id", "")
            related_alignments = [
                a for a in alignments
                if a.get("source_entity_id") == entity_id
                or a.get("target_entity_id") == entity_id
            ]
            if related_alignments:
                # Resolve names for the "other" entity
                entity_name_map = {
                    e.get("canonical_id", ""): e.get("canonical_name", e.get("canonical_id", ""))
                    for e in canonical_entities
                }
                align_df = pd.DataFrame([{
                    "Other Entity": entity_name_map.get(
                        a.get("target_entity_id") if a.get("source_entity_id") == entity_id else a.get("source_entity_id"),
                        a.get("target_entity_id") if a.get("source_entity_id") == entity_id else a.get("source_entity_id"),
                    ),
                    "Alignment": a.get("alignment_type", ""),
                    "Score": f"{a.get('score', 0):.3f}",
                    "Method": a.get("method", ""),
                    "Evidence": str(a.get("evidence", ""))[:100],
                } for a in related_alignments])
                st.dataframe(align_df, use_container_width=True, hide_index=True)
            else:
                st.info("No alignment edges for this entity")

        with tab3:
            candidate_ids = entity.get("source_candidate_ids", [])
            if candidate_ids:
                st.write("**Source Candidate IDs:**")
                for cid in candidate_ids:
                    st.code(cid)
            else:
                st.info("No source candidate IDs recorded")
else:
    st.info("No entities match the current filters.")

# ---------------------------------------------------------------------------
# Alignment distribution
# ---------------------------------------------------------------------------

st.divider()
st.subheader("Alignment Distribution")

if alignments:
    chart_col1, chart_col2 = st.columns(2)

    with chart_col1:
        st.markdown("**By Alignment Type**")
        type_counts: dict[str, int] = {}
        for a in alignments:
            t = a.get("alignment_type", "UNKNOWN")
            type_counts[t] = type_counts.get(t, 0) + 1
        type_df = pd.DataFrame(
            [{"Type": k, "Count": v} for k, v in sorted(type_counts.items())],
        )
        fig = go.Figure(
            go.Bar(
                x=type_df["Type"],
                y=type_df["Count"],
                marker_color="#00fcd6",
                text=type_df["Count"],
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
        st.plotly_chart(fig, use_container_width=True, key="align_type_dist")

    with chart_col2:
        st.markdown("**Score Distribution**")
        scores = [a.get("score", 0) for a in alignments if a.get("score") is not None]
        if scores:
            import numpy as np
            bins = np.histogram(scores, bins=20)
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
                xaxis_title="Score",
                yaxis_title="Count",
                margin=dict(t=10, b=40, l=40, r=10),
                showlegend=False,
            )
            st.plotly_chart(fig, use_container_width=True, key="align_score_dist")
        else:
            st.info("No alignment scores available")

    # Entity type breakdown
    st.divider()
    st.markdown("**Entity Type Distribution**")
    etype_counts: dict[str, int] = {}
    for e in canonical_entities:
        t = e.get("entity_type", "UNKNOWN")
        etype_counts[t] = etype_counts.get(t, 0) + 1
    if etype_counts:
        etype_df = pd.DataFrame(
            [{"Type": k, "Count": v} for k, v in sorted(etype_counts.items(), key=lambda x: x[1], reverse=True)],
        )
        type_colors = [ENTITY_COLORS.get(t, "#a1a1aa") for t in etype_df["Type"]]
        fig = go.Figure(
            go.Bar(
                x=etype_df["Type"],
                y=etype_df["Count"],
                marker_color=type_colors,
                text=etype_df["Count"],
                textposition="auto",
                textfont=dict(color="#e4e4e7", family="Space Mono, monospace", size=11),
            )
        )
        fig.update_layout(template=get_plotly_template())
        fig.update_layout(
            height=350,
            xaxis_title="",
            yaxis_title="Count",
            margin=dict(t=10, b=40, l=60, r=10),
            showlegend=False,
        )
        st.plotly_chart(fig, use_container_width=True, key="etype_dist")
else:
    st.info("No alignment edges found.")
