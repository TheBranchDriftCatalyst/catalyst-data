"""Knowledge Graph Explorer — interactive SPO graph using streamlit-agraph."""

from __future__ import annotations

import logging
from collections import Counter, defaultdict

import pandas as pd
import streamlit as st
from streamlit_agraph import Config, Edge, Node, agraph

from data_explorer.streamlit.components.entity_chip import render_entity_chip_list
from data_explorer.streamlit.config import get_s3_config
from data_explorer.streamlit.data_client import DataClient
from data_explorer.streamlit.navigation import get_nav_params, render_breadcrumbs
from data_explorer.streamlit.theme import apply_theme

logger = logging.getLogger(__name__)

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

    max_assertions = st.slider(
        "Max assertions",
        min_value=50,
        max_value=1000,
        value=200,
        step=50,
        key="kg_max_assertions",
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
# Load data — assertions (with fallback to propositions)
# ---------------------------------------------------------------------------

assertions_raw: list[dict] = []
using_assertions = False

try:
    assertions_raw = client.load_assertions(source, limit=max_assertions)
    using_assertions = True
    logger.info("Loaded %d assertions for source %s", len(assertions_raw), source)
except Exception:
    logger.warning("load_assertions failed for %s, falling back to load_propositions", source)
    try:
        assertions_raw = client.load_propositions(source, limit=max_assertions)
    except Exception:
        assertions_raw = []

# Load mentions for entity label lookup
mentions_raw: list[dict] = []
try:
    mentions_raw = client.load_mentions(source)
except Exception:
    logger.debug("load_mentions not available, falling back to load_entities")
    try:
        mentions_raw = client.load_entities(source, limit=5000)
    except Exception:
        pass

if not assertions_raw:
    st.info("No assertions found for this source. Try selecting a different source.")
    st.stop()

# ---------------------------------------------------------------------------
# Build entity -> label lookup (case-insensitive)
# ---------------------------------------------------------------------------

entity_label_lookup: dict[str, str] = {}
for ent in mentions_raw:
    text = ent.get("text", "").strip()
    label = ent.get("mention_type") or ent.get("label", "")
    if isinstance(label, str):
        pass
    elif hasattr(label, "value"):
        label = label.value
    else:
        label = str(label)
    if text:
        entity_label_lookup[text.lower()] = label

# ---------------------------------------------------------------------------
# Field accessors (assertions vs legacy propositions)
# ---------------------------------------------------------------------------


def _subj(p: dict) -> str:
    return (p.get("subject_text") or p.get("subject", "")).strip()


def _obj(p: dict) -> str:
    return (p.get("object_text") or p.get("object", "")).strip()


def _pred(p: dict) -> str:
    return (p.get("predicate_canonical") or p.get("predicate", "")).strip()


def _pred_raw(p: dict) -> str:
    return (p.get("predicate") or p.get("predicate_canonical", "")).strip()


# ---------------------------------------------------------------------------
# Filter assertions
# ---------------------------------------------------------------------------

assertions: list[dict] = []
for p in assertions_raw:
    # Confidence filter
    conf = p.get("confidence")
    if conf is not None and conf < min_confidence:
        continue

    subj = _subj(p)
    obj = _obj(p)
    if not subj or not obj:
        continue

    # Focus entity filter (ego-centric view)
    if focus_entity:
        needle = focus_entity.strip().lower()
        if needle not in subj.lower() and needle not in obj.lower():
            continue

    # Entity label filter — include assertion if either endpoint matches
    # a selected label (or has no known label, treated as passthrough)
    subj_label = entity_label_lookup.get(subj.lower(), "UNKNOWN")
    obj_label = entity_label_lookup.get(obj.lower(), "UNKNOWN")
    if selected_labels != ALL_LABELS:
        allowed = set(selected_labels) | {"UNKNOWN"}
        if subj_label not in allowed and obj_label not in allowed:
            continue

    assertions.append(p)

if not assertions:
    st.warning(
        "No assertions match the current filters. "
        "Try lowering min confidence, broadening labels, or clearing the focus entity."
    )
    st.stop()

# ---------------------------------------------------------------------------
# Stats bar
# ---------------------------------------------------------------------------

total_count = len(assertions)
negated_count = sum(1 for a in assertions if a.get("negated"))
hedged_count = sum(1 for a in assertions if a.get("hedged"))
confidences = [a.get("confidence", 1.0) for a in assertions if a.get("confidence") is not None]
avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

s1, s2, s3, s4 = st.columns(4)
s1.metric("Total Assertions", total_count)
s2.metric("Negated", negated_count)
s3.metric("Hedged", hedged_count)
s4.metric("Avg Confidence", f"{avg_confidence:.2f}")

# Qualifier distribution
qual_counts: Counter[str] = Counter()
for a in assertions:
    quals = a.get("qualifiers") or {}
    for qk in ("time", "location", "condition", "manner"):
        if quals.get(qk):
            qual_counts[qk] += 1

if qual_counts:
    q1, q2, q3, q4 = st.columns(4)
    q1.metric("Time qualifiers", qual_counts.get("time", 0))
    q2.metric("Location qualifiers", qual_counts.get("location", 0))
    q3.metric("Condition qualifiers", qual_counts.get("condition", 0))
    q4.metric("Manner qualifiers", qual_counts.get("manner", 0))

st.divider()

# ---------------------------------------------------------------------------
# Build graph structures
# ---------------------------------------------------------------------------

# Compute node degrees for sizing
degree: Counter[str] = Counter()
for p in assertions:
    degree[_subj(p)] += 1
    degree[_obj(p)] += 1

max_degree = max(degree.values()) if degree else 1

# Unique node set
node_names: set[str] = set()
for p in assertions:
    node_names.add(_subj(p))
    node_names.add(_obj(p))


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
for idx, p in enumerate(assertions):
    conf = p.get("confidence")
    width = 1.0
    if conf is not None:
        width = 1.0 + conf * 2.0  # range 1-3

    predicate = _pred(p)

    # Edge color: red for negated, yellow for hedged, default otherwise
    edge_color = "#27272a"
    if p.get("negated"):
        edge_color = "#ef4444"
    elif p.get("hedged"):
        edge_color = "#fbbf24"

    title_parts = [predicate]
    if conf is not None:
        title_parts.append(f"conf={conf:.2f}")
    if p.get("negated"):
        title_parts.append("NEGATED")
    if p.get("hedged"):
        title_parts.append("HEDGED")

    edges.append(
        Edge(
            source=_subj(p),
            target=_obj(p),
            label=predicate[:25] if predicate else "",
            color=edge_color,
            width=width,
            title="  ".join(title_parts),
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

tab_assertions, tab_predicates, tab_summary = st.tabs(
    ["Assertions Table", "Top Predicates", "Entity Summary"]
)

# --- Assertions Table ---
with tab_assertions:
    for i, a in enumerate(assertions[:100]):
        subj = _subj(a)
        pred = _pred(a)
        obj = _obj(a)
        conf = a.get("confidence")
        negated = a.get("negated", False)
        hedged = a.get("hedged", False)
        quals = a.get("qualifiers") or {}

        # Build label with badges
        label_parts = [f"**{subj}** -- {pred} --> **{obj}**"]
        if conf is not None:
            label_parts.append(f"  (conf: {conf:.2f})")

        badge_html = ""
        if negated:
            badge_html += (
                '<span style="background:#ef4444;color:#fff;padding:1px 6px;'
                'border-radius:3px;font-size:0.75em;margin-left:6px;">NEGATED</span>'
            )
        if hedged:
            badge_html += (
                '<span style="background:#fbbf24;color:#000;padding:1px 6px;'
                'border-radius:3px;font-size:0.75em;margin-left:6px;">HEDGED</span>'
            )

        with st.expander("".join(label_parts)):
            if badge_html:
                st.markdown(badge_html, unsafe_allow_html=True)

            # Qualifier chips
            qual_items = []
            if quals.get("time"):
                qual_items.append(f":material/schedule: **Time:** {quals['time']}")
            if quals.get("location"):
                qual_items.append(f":material/pin_drop: **Location:** {quals['location']}")
            if quals.get("condition"):
                qual_items.append(f":material/help: **Condition:** {quals['condition']}")
            if quals.get("manner"):
                qual_items.append(f":material/arrow_forward: **Manner:** {quals['manner']}")
            if quals.get("source_attribution"):
                qual_items.append(f":material/format_quote: **Source:** {quals['source_attribution']}")

            if qual_items:
                for qi in qual_items:
                    st.markdown(qi)

            # Raw assertion data
            detail_cols = {
                k: v for k, v in a.items()
                if k not in ("qualifiers",) and v is not None
            }
            st.json(detail_cols)

# --- Top Predicates ---
with tab_predicates:
    pred_counter: Counter[str] = Counter()
    for a in assertions:
        pred_counter[_pred(a)] += 1

    if pred_counter:
        pred_df = pd.DataFrame(
            pred_counter.most_common(30),
            columns=["Predicate (Canonical)", "Count"],
        )
        st.dataframe(pred_df, use_container_width=True, height=400)
    else:
        st.info("No predicates found.")

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
