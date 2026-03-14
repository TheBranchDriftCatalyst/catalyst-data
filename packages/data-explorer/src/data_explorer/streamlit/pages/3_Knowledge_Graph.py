"""Knowledge Graph Explorer — interactive SPO graph using streamlit-agraph."""

from __future__ import annotations

from collections import Counter, defaultdict

import pandas as pd
import streamlit as st
from streamlit_agraph import Config, Edge, Node, agraph

from data_explorer.streamlit.components.entity_chip import render_entity_chip_list
from data_explorer.streamlit.config import get_s3_config
from data_explorer.streamlit.data_client import DataClient
from data_explorer.streamlit.navigation import get_nav_params, render_breadcrumbs
from data_explorer.streamlit.theme import apply_theme

# ---------------------------------------------------------------------------
# Page setup
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Knowledge Graph",
    page_icon=":material/hub:",
    layout="wide",
)
apply_theme()
render_breadcrumbs([("Home", "app.py"), ("Knowledge Graph", None)])
st.header("Knowledge Graph Explorer")

# ---------------------------------------------------------------------------
# Nav params (e.g. from entity chip click on another page)
# ---------------------------------------------------------------------------

nav = get_nav_params()
initial_focus: str = nav.get("entity_text", "")

# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

LABEL_COLORS: dict[str, str] = {
    "PERSON": "#ff6ec7",
    "ORG": "#00fcd6",
    "GPE": "#00d4ff",
    "DATE": "#fbbf24",
    "LAW": "#c026d3",
    "EVENT": "#ff2975",
    "UNKNOWN": "#a1a1aa",
}

ALL_LABELS = ["PERSON", "ORG", "GPE", "DATE", "LAW", "EVENT"]


@st.cache_resource
def _get_client() -> DataClient:
    cfg = get_s3_config()
    return DataClient(
        endpoint_url=cfg.endpoint_url,
        access_key=cfg.access_key,
        secret_key=cfg.secret_key,
        bucket=cfg.bucket,
    )


client = _get_client()

# ---------------------------------------------------------------------------
# Sidebar controls
# ---------------------------------------------------------------------------

with st.sidebar:
    st.subheader("Graph Controls")

    sources = client.list_sources()
    if not sources:
        st.warning("No data sources found.")
        st.stop()

    source = st.selectbox("Source", sources, key="kg_source")

    max_propositions = st.slider(
        "Max propositions",
        min_value=50,
        max_value=1000,
        value=200,
        step=50,
        key="kg_max_props",
    )

    min_confidence = st.slider(
        "Min confidence",
        min_value=0.0,
        max_value=1.0,
        value=0.3,
        step=0.05,
        key="kg_min_conf",
    )

    selected_labels = st.multiselect(
        "Entity labels",
        options=ALL_LABELS,
        default=ALL_LABELS,
        key="kg_labels",
    )

    focus_entity = st.text_input(
        "Focus entity",
        value=initial_focus,
        placeholder="e.g. Jeffrey Epstein",
        key="kg_focus",
    )

# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------

propositions_raw = client.load_propositions(source, limit=max_propositions)
entities_raw = client.load_entities(source, limit=5000)

if not propositions_raw:
    st.info("No propositions found for this source. Try selecting a different source.")
    st.stop()

# ---------------------------------------------------------------------------
# Build entity -> label lookup (case-insensitive)
# ---------------------------------------------------------------------------

entity_label_lookup: dict[str, str] = {}
for ent in entities_raw:
    text = ent.get("text", "").strip()
    label = ent.get("label", "")
    if text:
        entity_label_lookup[text.lower()] = label

# ---------------------------------------------------------------------------
# Filter propositions
# ---------------------------------------------------------------------------

propositions: list[dict] = []
for p in propositions_raw:
    # Confidence filter
    conf = p.get("confidence")
    if conf is not None and conf < min_confidence:
        continue

    subj = p.get("subject", "").strip()
    obj = p.get("object", "").strip()
    if not subj or not obj:
        continue

    # Focus entity filter (ego-centric view)
    if focus_entity:
        needle = focus_entity.strip().lower()
        if needle not in subj.lower() and needle not in obj.lower():
            continue

    # Entity label filter — include proposition if either endpoint matches
    # a selected label (or has no known label, treated as passthrough)
    subj_label = entity_label_lookup.get(subj.lower(), "UNKNOWN")
    obj_label = entity_label_lookup.get(obj.lower(), "UNKNOWN")
    if selected_labels != ALL_LABELS:
        allowed = set(selected_labels) | {"UNKNOWN"}
        if subj_label not in allowed and obj_label not in allowed:
            continue

    propositions.append(p)

if not propositions:
    st.warning(
        "No propositions match the current filters. "
        "Try lowering min confidence, broadening labels, or clearing the focus entity."
    )
    st.stop()

# ---------------------------------------------------------------------------
# Build graph structures
# ---------------------------------------------------------------------------

# Compute node degrees for sizing
degree: Counter[str] = Counter()
for p in propositions:
    degree[p["subject"].strip()] += 1
    degree[p["object"].strip()] += 1

max_degree = max(degree.values()) if degree else 1

# Unique node set
node_names: set[str] = set()
for p in propositions:
    node_names.add(p["subject"].strip())
    node_names.add(p["object"].strip())


def _node_size(name: str) -> int:
    """Scale node size between 15 and 40 based on degree."""
    d = degree.get(name, 1)
    if max_degree <= 1:
        return 20
    ratio = d / max_degree
    return int(15 + ratio * 25)


def _node_color(name: str) -> str:
    """Return color based on NER label from the entity lookup."""
    label = entity_label_lookup.get(name.lower(), "UNKNOWN")
    return LABEL_COLORS.get(label, LABEL_COLORS["UNKNOWN"])


nodes: list[Node] = []
for name in sorted(node_names):
    label = entity_label_lookup.get(name.lower(), "UNKNOWN")
    nodes.append(
        Node(
            id=name,
            label=name[:30],
            size=_node_size(name),
            color=_node_color(name),
            title=f"{name}\n[{label}]  degree={degree.get(name, 0)}",
            font={"color": "#e4e4e7", "size": 10},
        )
    )

edges: list[Edge] = []
for idx, p in enumerate(propositions):
    conf = p.get("confidence")
    width = 1.0
    if conf is not None:
        width = 1.0 + conf * 2.0  # range 1-3

    predicate = p.get("predicate", "")
    edges.append(
        Edge(
            source=p["subject"].strip(),
            target=p["object"].strip(),
            label=predicate[:25] if predicate else "",
            color="#27272a",
            width=width,
            title=f"{predicate}  (conf={conf})" if conf is not None else predicate,
        )
    )

# ---------------------------------------------------------------------------
# Render graph
# ---------------------------------------------------------------------------

st.caption(f"{len(nodes)} nodes, {len(edges)} edges")

config = Config(
    directed=True,
    physics=True,
    hierarchical=False,
    height=600,
    width=1200,
    nodeHighlightBehavior=True,
    highlightColor="#00fcd6",
    collapsible=False,
    node={"highlightStrokeColor": "#00fcd6"},
    link={"highlightColor": "#00fcd6"},
    bgcolor="#0a0a0f",
    font_color="#e4e4e7",
)

agraph(nodes=nodes, edges=edges, config=config)

# ---------------------------------------------------------------------------
# Detail tabs below the graph
# ---------------------------------------------------------------------------

tab_props, tab_summary = st.tabs(["Propositions Table", "Entity Summary"])

# --- Propositions Table ---
with tab_props:
    prop_df = pd.DataFrame(propositions)
    display_cols = [c for c in ["subject", "predicate", "object", "confidence", "source_doc_id", "chunk_id"] if c in prop_df.columns]
    if display_cols:
        st.dataframe(
            prop_df[display_cols],
            use_container_width=True,
            height=400,
        )
    else:
        st.dataframe(prop_df, use_container_width=True, height=400)

# --- Entity Summary ---
with tab_summary:
    # Group entities by label with counts
    label_entities: dict[str, Counter[str]] = defaultdict(Counter)
    for name in node_names:
        label = entity_label_lookup.get(name.lower(), "UNKNOWN")
        label_entities[label][name] += degree.get(name, 1)

    for label in ALL_LABELS + ["UNKNOWN"]:
        if label not in label_entities:
            continue
        entities_for_label = label_entities[label]
        st.subheader(f"{label} ({len(entities_for_label)})")

        # Build chip list sorted by count descending
        chip_data = [
            {"text": text, "label": label, "count": count}
            for text, count in entities_for_label.most_common()
        ]
        render_entity_chip_list(chip_data, max_display=20, columns=5)
