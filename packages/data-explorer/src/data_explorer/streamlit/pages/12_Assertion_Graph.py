"""Assertion Graph — platinum-layer cross-source assertion graph viewer."""

from __future__ import annotations

from collections import Counter

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from streamlit_agraph import Config, Edge, Node, agraph

from dagster_io.logging import get_logger
from data_explorer.streamlit.components.concordance_view import render_assertion_table
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
    page_title="Assertion Graph",
    page_icon=":material/account_tree:",
    layout="wide",
)
apply_theme()
render_breadcrumbs([("Home", "app.py"), ("Assertion Graph", None)])
st.header("Assertion Graph Viewer")
st.caption("Platinum-layer assertions linked to canonical entities across all sources")


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


@st.cache_data(ttl=300, show_spinner="Loading assertion graph...")
def _load_assertion_graph(
    endpoint_url: str, access_key: str, secret_key: str, bucket: str,
) -> list[dict]:
    client = DataClient(endpoint_url=endpoint_url, access_key=access_key, secret_key=secret_key, bucket=bucket)
    return client.load_assertion_graph()


@st.cache_data(ttl=300, show_spinner="Loading canonical entities...")
def _load_canonical_entities(
    endpoint_url: str, access_key: str, secret_key: str, bucket: str,
) -> list[dict]:
    client = DataClient(endpoint_url=endpoint_url, access_key=access_key, secret_key=secret_key, bucket=bucket)
    return client.load_canonical_entities()


client = _get_client()
conn = (client._endpoint_url, client._access_key, client._secret_key, client._bucket)

assertion_graph = _load_assertion_graph(*conn)
canonical_entities = _load_canonical_entities(*conn)

if not assertion_graph:
    st.info(
        "No assertion graph data found. "
        "Ensure the knowledge-graph pipeline has materialized the assertion_graph asset."
    )
    st.stop()

# Build canonical entity lookup
entity_lookup: dict[str, dict] = {}
for e in canonical_entities:
    cid = e.get("canonical_id", "")
    if cid:
        entity_lookup[cid] = e

logger.info(
    "Loaded %d assertion graph records, %d canonical entities",
    len(assertion_graph), len(canonical_entities),
)


# ---------------------------------------------------------------------------
# Sidebar controls
# ---------------------------------------------------------------------------

with st.sidebar:
    st.subheader("Graph Controls")

    max_assertions = st.slider(
        "Max assertions",
        min_value=50,
        max_value=1000,
        value=200,
        step=50,
        key="ag_max",
    )

    # Filter by linkage status
    linkage_filter = st.radio(
        "Linkage status",
        options=["All", "Fully Linked", "Partially Linked", "Unlinked"],
        index=1,
        key="ag_linkage",
    )

    # Filter by code_location
    all_locations = sorted(set(a.get("code_location", "") for a in assertion_graph if a.get("code_location")))
    if all_locations:
        selected_locations = st.multiselect(
            "Source code locations",
            options=all_locations,
            default=all_locations,
            key="ag_locations",
        )
    else:
        selected_locations = []

    focus_entity = st.text_input(
        "Focus entity",
        placeholder="e.g. Jeffrey Epstein",
        key="ag_focus",
    )


# ---------------------------------------------------------------------------
# Filter assertions
# ---------------------------------------------------------------------------

filtered = assertion_graph

# Linkage filter
if linkage_filter == "Fully Linked":
    filtered = [a for a in filtered if a.get("subject_canonical_id") and a.get("object_canonical_id")]
elif linkage_filter == "Partially Linked":
    filtered = [
        a for a in filtered
        if (a.get("subject_canonical_id") or a.get("object_canonical_id"))
        and not (a.get("subject_canonical_id") and a.get("object_canonical_id"))
    ]
elif linkage_filter == "Unlinked":
    filtered = [a for a in filtered if not a.get("subject_canonical_id") and not a.get("object_canonical_id")]

# Code location filter
if selected_locations:
    filtered = [a for a in filtered if a.get("code_location", "") in selected_locations or not a.get("code_location")]

# Focus entity filter
if focus_entity:
    needle = focus_entity.strip().lower()
    focus_filtered = []
    for a in filtered:
        subj = a.get("subject_text", "").lower()
        obj = a.get("object_text", "").lower()
        # Also check canonical entity names
        subj_ce = entity_lookup.get(a.get("subject_canonical_id", ""), {})
        obj_ce = entity_lookup.get(a.get("object_canonical_id", ""), {})
        subj_name = subj_ce.get("canonical_name", "").lower()
        obj_name = obj_ce.get("canonical_name", "").lower()
        if needle in subj or needle in obj or needle in subj_name or needle in obj_name:
            focus_filtered.append(a)
    filtered = focus_filtered

# Apply limit
filtered = filtered[:max_assertions]


# ---------------------------------------------------------------------------
# Summary stats
# ---------------------------------------------------------------------------

total = len(assertion_graph)
fully_linked = sum(1 for a in assertion_graph if a.get("subject_canonical_id") and a.get("object_canonical_id"))
partially_linked = sum(
    1 for a in assertion_graph
    if (a.get("subject_canonical_id") or a.get("object_canonical_id"))
    and not (a.get("subject_canonical_id") and a.get("object_canonical_id"))
)
unlinked = total - fully_linked - partially_linked

s1, s2, s3, s4 = st.columns(4)
s1.metric("Total Assertions", total)
s2.metric("Fully Linked", fully_linked)
s3.metric("Partially Linked", partially_linked)
s4.metric("Unlinked", unlinked)

st.divider()


# ---------------------------------------------------------------------------
# Graph visualization (fully linked assertions only)
# ---------------------------------------------------------------------------

graph_assertions = [a for a in filtered if a.get("subject_canonical_id") and a.get("object_canonical_id")]

if graph_assertions:
    st.subheader("Assertion Graph")
    st.caption(f"Showing {len(graph_assertions)} fully-linked assertions as a graph")

    # Build node and edge sets
    degree: Counter[str] = Counter()
    node_ids: set[str] = set()
    for a in graph_assertions:
        subj_id = a["subject_canonical_id"]
        obj_id = a["object_canonical_id"]
        degree[subj_id] += 1
        degree[obj_id] += 1
        node_ids.add(subj_id)
        node_ids.add(obj_id)

    max_degree = max(degree.values()) if degree else 1

    nodes: list[Node] = []
    for nid in sorted(node_ids):
        ce = entity_lookup.get(nid, {})
        name = ce.get("canonical_name", nid[:20])
        etype = ce.get("entity_type", "UNKNOWN")
        color = ENTITY_COLORS.get(etype, "#a1a1aa")
        d = degree.get(nid, 1)
        size = int(15 + (d / max_degree) * 25)
        nodes.append(
            Node(
                id=nid,
                label=name[:25],
                size=size,
                color=color,
                title=f"{name}\n[{etype}]  degree={d}",
                font={"color": "#e4e4e7", "size": 10},
            )
        )

    edges: list[Edge] = []
    for a in graph_assertions:
        pred = a.get("predicate_canonical") or a.get("predicate", "")
        edge_color = "#27272a"
        if a.get("negated"):
            edge_color = "#ef4444"
        elif a.get("hedged"):
            edge_color = "#fbbf24"

        conf = a.get("confidence")
        width = 1.0 + (conf * 2.0 if conf is not None else 0)

        edges.append(
            Edge(
                source=a["subject_canonical_id"],
                target=a["object_canonical_id"],
                label=pred[:20] if pred else "",
                color=edge_color,
                width=width,
                title=f"{pred}  conf={conf:.2f}" if conf is not None else pred,
            )
        )

    st.caption(f"{len(nodes)} nodes, {len(edges)} edges")

    config = Config(
        directed=True,
        physics=True,
        hierarchical=False,
        height=600,
        width=1200,
    )

    agraph(nodes=nodes, edges=edges, config=config)

    st.divider()
else:
    st.info("No fully-linked assertions to visualize as a graph.")


# ---------------------------------------------------------------------------
# Assertion table
# ---------------------------------------------------------------------------

st.subheader(f"Assertion Details ({len(filtered)})")

if not filtered:
    st.warning("No assertions match the current filters.")
    st.stop()

tab_table, tab_provenance, tab_distribution = st.tabs([
    "Assertion Table", "Provenance", "Distribution",
])

with tab_table:
    # Enrich assertions with canonical entity names for display
    display_assertions = []
    for a in filtered:
        enriched = dict(a)
        subj_ce = entity_lookup.get(a.get("subject_canonical_id", ""), {})
        obj_ce = entity_lookup.get(a.get("object_canonical_id", ""), {})
        if subj_ce:
            enriched["subject_canonical_name"] = subj_ce.get("canonical_name", "")
        if obj_ce:
            enriched["object_canonical_name"] = obj_ce.get("canonical_name", "")
        display_assertions.append(enriched)

    render_assertion_table(display_assertions, key_suffix="_ag")

with tab_provenance:
    st.markdown("**Source Document Provenance**")
    doc_counts: Counter[str] = Counter(
        a.get("source_document_id", "unknown") for a in filtered
        if a.get("source_document_id")
    )
    if doc_counts:
        doc_df = pd.DataFrame(
            [{"Document": d, "Assertions": c} for d, c in doc_counts.most_common(20)],
        )
        st.dataframe(doc_df, use_container_width=True, hide_index=True)
    else:
        st.caption("No source document provenance available")

    st.divider()
    st.markdown("**Code Location Provenance**")
    loc_counts: Counter[str] = Counter(
        a.get("code_location", "unknown") for a in filtered
        if a.get("code_location")
    )
    if loc_counts:
        loc_df = pd.DataFrame(
            [{"Code Location": loc, "Assertions": c} for loc, c in loc_counts.most_common()],
        )
        st.dataframe(loc_df, use_container_width=True, hide_index=True)

with tab_distribution:
    dist_col1, dist_col2 = st.columns(2)

    with dist_col1:
        st.markdown("**Top Predicates**")
        pred_counts: Counter[str] = Counter(
            a.get("predicate_canonical") or a.get("predicate", "unknown")
            for a in filtered
        )
        if pred_counts:
            pred_df = pd.DataFrame(
                [{"Predicate": p, "Count": c} for p, c in pred_counts.most_common(15)],
            )
            st.dataframe(pred_df, use_container_width=True, hide_index=True)

    with dist_col2:
        st.markdown("**Assertion Flags**")
        negated = sum(1 for a in filtered if a.get("negated"))
        hedged = sum(1 for a in filtered if a.get("hedged"))
        confident = len(filtered) - negated - hedged
        flag_df = pd.DataFrame([
            {"Flag": "Confident", "Count": confident},
            {"Flag": "Negated", "Count": negated},
            {"Flag": "Hedged", "Count": hedged},
        ])
        colors = ["#00fcd6", "#ef4444", "#fbbf24"]
        fig = go.Figure(
            go.Bar(
                x=flag_df["Flag"],
                y=flag_df["Count"],
                marker_color=colors,
                text=flag_df["Count"],
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
        st.plotly_chart(fig, use_container_width=True, key="ag_flags")

    # Qualifier coverage
    st.divider()
    st.markdown("**Qualifier Coverage**")
    qualifier_keys = ["time", "location", "condition", "manner", "source_attribution"]
    qual_counts: dict[str, int] = {}
    for qk in qualifier_keys:
        qual_counts[qk] = sum(
            1 for a in filtered
            if (a.get("qualifiers") or {}).get(qk)
        )
    qual_df = pd.DataFrame(
        [{"Qualifier": k, "Count": v} for k, v in qual_counts.items()],
    )
    fig = go.Figure(
        go.Bar(
            x=qual_df["Qualifier"],
            y=qual_df["Count"],
            marker_color="#c026d3",
            text=qual_df["Count"],
            textposition="auto",
            textfont=dict(color="#e4e4e7", family="Space Mono, monospace", size=11),
        )
    )
    fig.update_layout(template=get_plotly_template())
    fig.update_layout(
        height=300,
        xaxis_title="",
        yaxis_title="Assertions with qualifier",
        margin=dict(t=10, b=40, l=60, r=10),
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True, key="ag_qual_dist")
