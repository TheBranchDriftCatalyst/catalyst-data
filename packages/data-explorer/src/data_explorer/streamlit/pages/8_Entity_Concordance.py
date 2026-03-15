"""Entity Concordance — mentions, assertions, and cross-source entity profile."""

from __future__ import annotations

from collections import Counter

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dagster_io.logging import get_logger
from data_explorer.streamlit.components.concordance_view import (
    render_assertion_table,
    render_concordance,
    render_mention_stats,
)
from data_explorer.streamlit.components.entity_chip import (
    render_entity_chip,
    render_entity_chip_list,
)
from data_explorer.streamlit.config import get_s3_config
from data_explorer.streamlit.data_client import DataClient
from data_explorer.streamlit.navigation import (
    ENTITY_COLORS,
    get_nav_params,
    render_breadcrumbs,
)
from data_explorer.streamlit.theme import apply_theme, get_plotly_template

logger = get_logger(__name__)

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
# Nav params
# ---------------------------------------------------------------------------

nav = get_nav_params()
nav_entity_text = nav.get("entity_text", "")
nav_entity_label = nav.get("entity_label", "")

# ---------------------------------------------------------------------------
# Data client
# ---------------------------------------------------------------------------

MENTION_TYPE_OPTIONS = ["PERSON", "ORG", "GPE", "DATE", "LAW", "EVENT", "LOC", "MONEY", "NORP", "FACILITY"]


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


@st.cache_data(ttl=300, show_spinner="Loading mentions...")
def _load_mentions(endpoint_url: str, access_key: str, secret_key: str, bucket: str, source: str) -> list[dict]:
    client = DataClient(endpoint_url=endpoint_url, access_key=access_key, secret_key=secret_key, bucket=bucket)
    return client.load_mentions(source)


@st.cache_data(ttl=300, show_spinner="Loading assertions...")
def _load_assertions(endpoint_url: str, access_key: str, secret_key: str, bucket: str, source: str) -> list[dict]:
    client = DataClient(endpoint_url=endpoint_url, access_key=access_key, secret_key=secret_key, bucket=bucket)
    return client.load_assertions(source)


@st.cache_data(ttl=300, show_spinner="Loading canonical entities...")
def _load_canonical_entities(endpoint_url: str, access_key: str, secret_key: str, bucket: str) -> list[dict]:
    client = DataClient(endpoint_url=endpoint_url, access_key=access_key, secret_key=secret_key, bucket=bucket)
    return client.load_canonical_entities()


client = _get_client()
conn = (client._endpoint_url, client._access_key, client._secret_key, client._bucket)
sources = _list_sources(*conn)

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
    default_types = [nav_entity_label] if nav_entity_label in MENTION_TYPE_OPTIONS else MENTION_TYPE_OPTIONS
    selected_types = st.multiselect(
        "Mention types",
        options=MENTION_TYPE_OPTIONS,
        default=default_types,
        key="concordance_types",
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
# Load data from all selected sources
# ---------------------------------------------------------------------------

if not selected_sources:
    st.warning("Select at least one source in the sidebar.")
    st.stop()

all_mentions: list[dict] = []
all_assertions: list[dict] = []
for src in selected_sources:
    mentions = _load_mentions(*conn, source=src)
    for m in mentions:
        m.setdefault("_source", src)
    all_mentions.extend(mentions)

    assertions = _load_assertions(*conn, source=src)
    for a in assertions:
        a.setdefault("_source", src)
    all_assertions.extend(assertions)

# Load canonical entities for cross-source context
canonical_entities = _load_canonical_entities(*conn)
logger.info(
    "Loaded %d mentions, %d assertions, %d canonical entities",
    len(all_mentions), len(all_assertions), len(canonical_entities),
)

if not all_mentions and not all_assertions:
    st.info("No mention or assertion data found in the selected sources.")
    st.stop()

# ---------------------------------------------------------------------------
# Filter mentions
# ---------------------------------------------------------------------------

filtered_mentions = all_mentions
if selected_types:
    filtered_mentions = [m for m in filtered_mentions if m.get("mention_type", "") in selected_types]
if search_text:
    needle = search_text.lower()
    filtered_mentions = [m for m in filtered_mentions if needle in m.get("text", "").lower()]

# Filter assertions by search text (match on subject or object)
filtered_assertions = all_assertions
if search_text:
    needle = search_text.lower()
    filtered_assertions = [
        a for a in filtered_assertions
        if needle in a.get("subject_text", "").lower()
        or needle in a.get("object_text", "").lower()
    ]

# ---------------------------------------------------------------------------
# Summary stats
# ---------------------------------------------------------------------------

stat1, stat2, stat3, stat4 = st.columns(4)
stat1.metric("Mentions", len(filtered_mentions))
stat2.metric("Assertions", len(filtered_assertions))
unique_entities = len(set(m.get("text", "") for m in filtered_mentions))
stat3.metric("Unique Entities", unique_entities)
stat4.metric("Canonical Entities", len(canonical_entities))

st.divider()

# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------

tab_mentions, tab_assertions, tab_profile = st.tabs([
    "Mentions",
    "Assertions",
    "Entity Profile",
])

# ===== Tab 1: Mentions =====================================================

with tab_mentions:
    st.subheader("Entity Mentions")

    if not filtered_mentions:
        st.info("No mentions match the current filters.")
    else:
        render_mention_stats(filtered_mentions)
        st.divider()
        render_concordance(filtered_mentions)

# ===== Tab 2: Assertions ===================================================

with tab_assertions:
    st.subheader("Assertions (S-P-O Triples)")

    if not filtered_assertions:
        st.info("No assertions match the current filters.")
    else:
        # Assertion overview stats
        a_col1, a_col2, a_col3, a_col4 = st.columns(4)
        negated_count = sum(1 for a in filtered_assertions if a.get("negated"))
        hedged_count = sum(1 for a in filtered_assertions if a.get("hedged"))
        confident_count = len(filtered_assertions) - negated_count - hedged_count
        a_col1.metric("Total Assertions", len(filtered_assertions))
        a_col2.metric("Confident", confident_count)
        a_col3.metric("Negated", negated_count)
        a_col4.metric("Hedged", hedged_count)

        st.divider()

        # Top predicates by predicate_canonical
        st.markdown("**Top Predicates**")
        pred_counts: Counter[str] = Counter(
            a.get("predicate_canonical") or a.get("predicate", "unknown")
            for a in filtered_assertions
        )
        pred_df = pd.DataFrame(
            [{"Predicate": p, "Count": c} for p, c in pred_counts.most_common(15)],
        )
        st.dataframe(pred_df, use_container_width=True, hide_index=True)

        # Qualifier breakdown
        st.divider()
        st.markdown("**Qualifier Coverage**")
        qualifier_keys = ["time", "location", "condition", "manner", "source_attribution"]
        qual_counts: dict[str, int] = {}
        for qk in qualifier_keys:
            qual_counts[qk] = sum(
                1 for a in filtered_assertions
                if a.get("qualifiers", {}).get(qk)
            )
        qual_df = pd.DataFrame(
            [{"Qualifier": k, "Count": v} for k, v in qual_counts.items()],
        )
        template = get_plotly_template()
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
        fig.update_layout(template=template)
        fig.update_layout(
            height=300,
            xaxis_title="",
            yaxis_title="Assertions with qualifier",
            margin=dict(t=10, b=40, l=60, r=10),
            showlegend=False,
        )
        st.plotly_chart(fig, use_container_width=True, key="qual_dist")

        # Full assertion table
        st.divider()
        render_assertion_table(filtered_assertions)

# ===== Tab 3: Entity Profile ================================================

with tab_profile:
    st.subheader("Entity Profile")

    # Build entity list from mentions
    entity_freq: Counter[str] = Counter(m.get("text", "") for m in filtered_mentions)
    entity_freq_items = entity_freq.most_common()
    if sort_by == "Alphabetical":
        entity_freq_items = sorted(entity_freq_items, key=lambda x: x[0].lower())

    entity_names = [text for text, _c in entity_freq_items]

    if not entity_names:
        st.info("No entities to profile. Adjust filters above.")
        st.stop()

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
        entity_mentions = [m for m in filtered_mentions if m.get("text", "") == selected_entity]
        entity_type = entity_mentions[0].get("mention_type", "") if entity_mentions else ""

        # --- Entity chip + frequency ---
        col_chip, col_freq = st.columns([1, 2])
        with col_chip:
            render_entity_chip(
                text=selected_entity,
                label=entity_type,
                count=len(entity_mentions),
                key_suffix="profile-main",
            )
        with col_freq:
            st.metric("Total Mentions", len(entity_mentions))

        st.divider()

        # --- Profile tabs ---
        ptab_identity, ptab_mentions, ptab_assertions, ptab_cross = st.tabs([
            "Identity", "Mentions", "Assertions", "Cross-Source",
        ])

        with ptab_identity:
            # Check for canonical entity match
            canonical_match = None
            for ce in canonical_entities:
                if (
                    ce.get("canonical_name", "").lower() == selected_entity.lower()
                    or selected_entity.lower() in [a.lower() for a in ce.get("aliases", [])]
                ):
                    canonical_match = ce
                    break

            if canonical_match:
                id_c1, id_c2 = st.columns(2)
                with id_c1:
                    st.write("**Canonical Name:**", canonical_match.get("canonical_name", ""))
                    st.write("**Type:**", canonical_match.get("entity_type", ""))
                    st.write("**Mention Count:**", canonical_match.get("mention_count", 0))
                    st.write("**Sources:**", ", ".join(canonical_match.get("source_code_locations", [])))
                with id_c2:
                    aliases = canonical_match.get("aliases", [])
                    if aliases:
                        st.write("**Aliases:**")
                        for alias in aliases:
                            st.write(f"  - {alias}")
                    if canonical_match.get("description"):
                        st.write("**Description:**", canonical_match.get("description", ""))
            else:
                st.write("**Entity:**", selected_entity)
                st.write("**Type:**", entity_type)
                st.caption("No canonical entity match found in the platinum layer.")

            # Per-source frequency
            st.divider()
            st.markdown("**Frequency by Source**")
            source_counts: Counter[str] = Counter(
                m.get("_source", "unknown") for m in entity_mentions
            )
            source_df = pd.DataFrame(
                [{"Source": src, "Count": cnt} for src, cnt in source_counts.most_common()],
            )
            st.dataframe(source_df, use_container_width=True, hide_index=True)

        with ptab_mentions:
            if entity_mentions:
                mention_df = pd.DataFrame([{
                    "Text": m.get("text", ""),
                    "Type": m.get("mention_type", ""),
                    "Document": m.get("document_id", ""),
                    "Chunk": m.get("chunk_id", ""),
                    "Span": f"{m.get('span_start', '?')}-{m.get('span_end', '?')}",
                    "Context": (m.get("context", "") or "")[:120],
                    "Method": (m.get("provenance", {}) or {}).get("extraction_method", ""),
                    "Confidence": (m.get("provenance", {}) or {}).get("confidence", ""),
                    "Source": m.get("_source", ""),
                } for m in entity_mentions])
                st.dataframe(mention_df, use_container_width=True, hide_index=True, height=400)
            else:
                st.info("No mentions found for this entity.")

        with ptab_assertions:
            # Find assertions where entity is subject or object
            entity_assertions = [
                a for a in all_assertions
                if a.get("subject_text", "").lower() == selected_entity.lower()
                or a.get("object_text", "").lower() == selected_entity.lower()
            ]
            if entity_assertions:
                render_assertion_table(entity_assertions, key_suffix="_profile")
            else:
                st.info("No assertions found for this entity.")

        with ptab_cross:
            if canonical_match:
                st.write("**Canonical Entity ID:**", canonical_match.get("canonical_id", ""))
                st.write("**Source Candidate IDs:**")
                for cid in canonical_match.get("source_candidate_ids", []):
                    st.code(cid)

                # Show other entities in the same aliases group
                other_aliases = [
                    a for a in canonical_match.get("aliases", [])
                    if a.lower() != selected_entity.lower()
                ]
                if other_aliases:
                    st.divider()
                    st.markdown("**Related Aliases**")
                    alias_chips = [
                        {"text": a, "label": entity_type, "count": entity_freq.get(a, 0)}
                        for a in other_aliases
                    ]
                    render_entity_chip_list(alias_chips, max_display=12, columns=4)
            else:
                st.info(
                    "No canonical entity match found. This entity has not been resolved "
                    "in the platinum layer yet."
                )

        # --- Source documents ---
        st.divider()
        st.markdown("**Source Documents**")
        doc_counts: Counter[str] = Counter(
            m.get("document_id", "unknown") for m in entity_mentions
            if m.get("document_id")
        )
        if doc_counts:
            doc_df = pd.DataFrame(
                [{"Document ID": doc_id, "Mentions": cnt} for doc_id, cnt in doc_counts.most_common()],
            )
            st.dataframe(doc_df, use_container_width=True, hide_index=True)
        else:
            st.caption("No source document IDs available.")
