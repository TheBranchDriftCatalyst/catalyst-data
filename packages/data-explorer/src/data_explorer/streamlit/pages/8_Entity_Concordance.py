"""Entity Concordance — KWIC view and co-occurrence analysis."""

from __future__ import annotations

from collections import Counter, defaultdict

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from data_explorer.streamlit.config import get_s3_config
from data_explorer.streamlit.data_client import DataClient
from data_explorer.streamlit.entity_resolution import EntityResolver
from data_explorer.streamlit.navigation import (
    ENTITY_COLORS,
    get_nav_params,
    render_breadcrumbs,
)
from data_explorer.streamlit.theme import apply_theme, get_plotly_template

from data_explorer.streamlit.components.concordance_view import (
    render_concordance,
    render_concordance_stats,
)
from data_explorer.streamlit.components.entity_chip import (
    render_entity_chip,
    render_entity_chip_list,
)

st.set_page_config(
    page_title="Entity Concordance",
    page_icon=":material/format_list_bulleted:",
    layout="wide",
)
apply_theme()
render_breadcrumbs([
    ("Home", "app.py"),
    ("Entity Concordance", None),
])
st.header("Entity Concordance")

# ---------------------------------------------------------------------------
# Nav params — pre-populate search from cross-page navigation
# ---------------------------------------------------------------------------

nav = get_nav_params()
nav_entity_text = nav.get("entity_text", "")
nav_entity_label = nav.get("entity_label", "")

# ---------------------------------------------------------------------------
# Data client
# ---------------------------------------------------------------------------

LABEL_OPTIONS = ["PERSON", "ORG", "GPE", "DATE", "LAW", "EVENT"]


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
def _list_sources() -> list[str]:
    return _get_client().list_sources()


@st.cache_data(ttl=300, show_spinner="Loading entities...")
def _load_entities(source: str) -> list[dict]:
    return _get_client().load_entities(source)


client = _get_client()
sources = _list_sources()

if not sources:
    st.info("No data sources found. Ensure pipelines have produced entity assets.")
    st.stop()

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.subheader("Search")
    search_text = st.text_input(
        "Entity search",
        value=nav_entity_text,
        placeholder="Type an entity name...",
        key="concordance_search",
    )

    st.subheader("Filters")

    default_labels = [nav_entity_label] if nav_entity_label in LABEL_OPTIONS else LABEL_OPTIONS
    selected_labels = st.multiselect(
        "Entity labels",
        options=LABEL_OPTIONS,
        default=default_labels,
        key="concordance_labels",
    )

    st.subheader("Sources")
    selected_sources: list[str] = []
    for src in sources:
        if st.checkbox(src, value=True, key=f"src_{src}"):
            selected_sources.append(src)

    st.subheader("Sort")
    sort_by = st.radio(
        "Sort entities by",
        options=["Frequency", "Alphabetical"],
        index=0,
        key="concordance_sort",
    )

# ---------------------------------------------------------------------------
# Load and filter entities from selected sources
# ---------------------------------------------------------------------------

if not selected_sources:
    st.warning("Select at least one source in the sidebar.")
    st.stop()

all_entities: list[dict] = []
for src in selected_sources:
    rows = _load_entities(src)
    # Tag each row with its source for later grouping
    for r in rows:
        r.setdefault("_source", src)
    all_entities.extend(rows)

if not all_entities:
    st.info("No entity data found in the selected sources.")
    st.stop()

# Filter by labels
if selected_labels:
    all_entities = [e for e in all_entities if e.get("label", "") in selected_labels]

# Filter by search text (case-insensitive substring)
if search_text:
    needle = search_text.lower()
    all_entities = [e for e in all_entities if needle in e.get("text", "").lower()]

if not all_entities:
    st.info("No entities match the current filters.")
    st.stop()

# ---------------------------------------------------------------------------
# Entity resolution (for alias detection)
# ---------------------------------------------------------------------------

resolver = EntityResolver(all_entities)
resolver.resolve()

# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------

tab_kwic, tab_cooccurrence, tab_profile = st.tabs([
    "KWIC Concordance",
    "Co-occurrence",
    "Entity Profile",
])

# ===== Tab 1: KWIC Concordance =============================================

with tab_kwic:
    st.subheader("Key Word In Context")
    render_concordance_stats(all_entities)
    st.divider()
    render_concordance(all_entities)

# ===== Tab 2: Co-occurrence ================================================

with tab_cooccurrence:
    st.subheader("Entity Co-occurrence Matrix")

    # Get top entities by frequency for the heatmap
    entity_freq: Counter[str] = Counter(e.get("text", "") for e in all_entities)
    top_n = st.slider("Top N entities", 5, 50, 20, step=5, key="cooc_top_n")
    top_entities = [text for text, _count in entity_freq.most_common(top_n)]

    if len(top_entities) < 2:
        st.info("Need at least 2 distinct entities for co-occurrence analysis.")
    else:
        # Build co-occurrence across all selected sources
        cooc_combined: dict[tuple[str, str], int] = defaultdict(int)
        for src in selected_sources:
            cooc = client.build_entity_cooccurrence(top_entities, src)
            for pair, count in cooc.items():
                cooc_combined[pair] += count

        # Build symmetric matrix
        labels_sorted = sorted(top_entities, key=lambda t: entity_freq[t], reverse=True)
        n = len(labels_sorted)
        idx_map = {t: i for i, t in enumerate(labels_sorted)}
        matrix = [[0] * n for _ in range(n)]

        for (a, b), count in cooc_combined.items():
            ia, ib = idx_map.get(a), idx_map.get(b)
            if ia is not None and ib is not None:
                matrix[ia][ib] = count
                matrix[ib][ia] = count

        template = get_plotly_template()
        fig = go.Figure(
            go.Heatmap(
                z=matrix,
                x=labels_sorted,
                y=labels_sorted,
                colorscale=[
                    [0, "#16161d"],
                    [0.5, "#c026d3"],
                    [1, "#00fcd6"],
                ],
                hoverongaps=False,
                hovertemplate="%{y} + %{x}<br>Count: %{z}<extra></extra>",
            )
        )
        fig.update_layout(
            template=template,
            title="Co-occurrence Heatmap",
            height=max(400, n * 28 + 120),
            margin=dict(l=10, r=10, t=50, b=10),
            xaxis=dict(
                tickangle=45,
                tickfont=dict(family="Rajdhani, sans-serif", size=10, color="#a1a1aa"),
                side="bottom",
            ),
            yaxis=dict(
                tickfont=dict(family="Rajdhani, sans-serif", size=10, color="#a1a1aa"),
                autorange="reversed",
            ),
        )
        st.plotly_chart(fig, use_container_width=True)

# ===== Tab 3: Entity Profile ===============================================

with tab_profile:
    st.subheader("Entity Profile")

    # Build entity list for profile selection
    entity_freq_items = entity_freq.most_common()
    if sort_by == "Alphabetical":
        entity_freq_items = sorted(entity_freq_items, key=lambda x: x[0].lower())

    entity_names = [text for text, _c in entity_freq_items]
    # Pre-select from search or nav param
    default_idx = 0
    if search_text and search_text in entity_names:
        default_idx = entity_names.index(search_text)

    selected_entity = st.selectbox(
        "Select entity",
        options=entity_names,
        index=default_idx,
        key="profile_entity_select",
    )

    if selected_entity:
        # Gather matching rows for this entity
        entity_matches = [e for e in all_entities if e.get("text", "") == selected_entity]
        entity_label = entity_matches[0].get("label", "") if entity_matches else ""

        # --- Entity chip + overall frequency ---
        col_chip, col_freq = st.columns([1, 2])
        with col_chip:
            render_entity_chip(
                text=selected_entity,
                label=entity_label,
                count=len(entity_matches),
                key_suffix="profile-main",
            )
        with col_freq:
            st.metric("Total Frequency", len(entity_matches))

        st.divider()

        # --- Per-source frequency breakdown ---
        st.markdown("**Frequency by Source**")
        source_counts: Counter[str] = Counter(
            e.get("_source", "unknown") for e in entity_matches
        )
        source_df = pd.DataFrame(
            [{"Source": src, "Count": cnt} for src, cnt in source_counts.most_common()],
        )
        st.dataframe(source_df, use_container_width=True, hide_index=True)

        # --- Aliases ---
        aliases = resolver.get_aliases(resolver.get_canonical(selected_entity))
        # Filter out the entity itself
        other_aliases = [a for a in aliases if a != selected_entity]
        if other_aliases:
            st.markdown("**Aliases (Resolved Variants)**")
            alias_chips = [
                {"text": a, "label": entity_label, "count": entity_freq.get(a, 0)}
                for a in other_aliases
            ]
            render_entity_chip_list(alias_chips, max_display=12, columns=4)

        # --- Top predicates from SPO triples ---
        st.markdown("**Top Predicates (SPO Triples)**")
        all_propositions: list[dict] = []
        for src in selected_sources:
            props = client.get_entity_propositions(selected_entity, src)
            all_propositions.extend(props)

        if all_propositions:
            pred_counts: Counter[str] = Counter(
                p.get("predicate", p.get("relation", "unknown"))
                for p in all_propositions
            )
            pred_df = pd.DataFrame(
                [
                    {"Predicate": pred, "Count": cnt}
                    for pred, cnt in pred_counts.most_common(15)
                ],
            )
            st.dataframe(pred_df, use_container_width=True, hide_index=True)

            with st.expander(f"All {len(all_propositions)} triples"):
                triples_df = pd.DataFrame(all_propositions)
                display_cols = [
                    c for c in ["subject", "predicate", "relation", "object", "confidence", "source_doc_id"]
                    if c in triples_df.columns
                ]
                st.dataframe(
                    triples_df[display_cols] if display_cols else triples_df,
                    use_container_width=True,
                    height=400,
                )
        else:
            st.caption("No SPO triples found for this entity.")

        # --- Source documents with match counts ---
        st.markdown("**Source Documents**")
        doc_counts: Counter[str] = Counter(
            e.get("source_doc_id", "unknown") for e in entity_matches
            if e.get("source_doc_id")
        )
        if doc_counts:
            doc_df = pd.DataFrame(
                [
                    {"Document ID": doc_id, "Mentions": cnt}
                    for doc_id, cnt in doc_counts.most_common()
                ],
            )
            st.dataframe(doc_df, use_container_width=True, hide_index=True)
        else:
            st.caption("No source document IDs available.")
