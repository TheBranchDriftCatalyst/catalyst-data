"""Cross-Source Linker — Entity resolution and linking across data sources."""

from __future__ import annotations

from collections import Counter

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from data_explorer.streamlit.components.entity_chip import render_entity_chip_html
from data_explorer.streamlit.config import get_s3_config
from data_explorer.streamlit.data_client import DataClient
from data_explorer.streamlit.entity_resolution import EntityResolver
from data_explorer.streamlit.navigation import (
    ENTITY_COLORS,
    get_nav_params,
    navigate_to,
    render_breadcrumbs,
)
from data_explorer.streamlit.theme import apply_theme, get_plotly_template

st.set_page_config(page_title="Cross-Source Linker", page_icon=":material/link:", layout="wide")
apply_theme()
render_breadcrumbs([
    ("Home", "app.py"),
    ("Cross-Source Linker", None),
])
st.header("Cross-Source Linker")


# ---------------------------------------------------------------------------
# Data access helpers
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


@st.cache_data(ttl=300, show_spinner="Loading entities...")
def _load_source_entities(source: str, limit: int) -> list[dict]:
    return _get_client().load_entities(source, limit=limit)


# ---------------------------------------------------------------------------
# Nav params for pre-filtering
# ---------------------------------------------------------------------------

nav_params = get_nav_params()
prefilter_label: str | None = nav_params.get("entity_label")
prefilter_sources: list[str] | None = nav_params.get("sources")

# ---------------------------------------------------------------------------
# Source discovery
# ---------------------------------------------------------------------------

client = _get_client()
all_sources = client.list_sources()

if not all_sources:
    st.warning("No data sources found.")
    st.stop()

# ---------------------------------------------------------------------------
# Sidebar controls
# ---------------------------------------------------------------------------

with st.sidebar:
    st.subheader("Sources & Filters")

    default_sources = prefilter_sources if prefilter_sources else all_sources
    selected_sources = st.multiselect(
        "Sources",
        options=all_sources,
        default=[s for s in default_sources if s in all_sources],
        key="xsl_sources",
    )

    # Entity label filter
    all_labels = sorted(ENTITY_COLORS.keys())
    default_label_idx = (
        all_labels.index(prefilter_label) + 1
        if prefilter_label and prefilter_label in all_labels
        else 0
    )
    label_options = ["All"] + all_labels
    selected_label = st.selectbox(
        "Entity label filter",
        label_options,
        index=default_label_idx,
        key="xsl_label",
    )

    max_per_source = st.slider(
        "Max entities per source",
        min_value=500,
        max_value=5000,
        value=2000,
        step=500,
        key="xsl_max",
    )

    sort_order = st.selectbox(
        "Sort entity groups by",
        ["Count (descending)", "Alphabetical"],
        key="xsl_sort",
    )

    if st.button("Refresh", key="xsl_refresh"):
        st.cache_data.clear()
        st.rerun()

if not selected_sources:
    st.info("Select at least one source from the sidebar.")
    st.stop()

# ---------------------------------------------------------------------------
# Load and tag entities from all selected sources
# ---------------------------------------------------------------------------

with st.spinner("Loading entities from selected sources..."):
    combined_entities: list[dict] = []
    source_counts: dict[str, int] = {}

    for source in selected_sources:
        rows = _load_source_entities(source, max_per_source)
        for r in rows:
            r["_source"] = source
        combined_entities.extend(rows)
        source_counts[source] = len(rows)

if not combined_entities:
    st.warning("No entities found in the selected sources.")
    st.stop()

st.caption(
    f"Loaded **{len(combined_entities):,}** entities across "
    f"**{len(selected_sources)}** source(s): "
    + ", ".join(f"{s} ({source_counts[s]:,})" for s in selected_sources)
)

# ---------------------------------------------------------------------------
# Apply label filter before resolution
# ---------------------------------------------------------------------------

if selected_label != "All":
    combined_entities = [
        e for e in combined_entities
        if e.get("label", "").upper() == selected_label
    ]
    if not combined_entities:
        st.warning(f"No **{selected_label}** entities found in the selected sources.")
        st.stop()

# ---------------------------------------------------------------------------
# Entity resolution
# ---------------------------------------------------------------------------

with st.spinner("Resolving entities..."):
    resolver = EntityResolver(combined_entities)
    resolver.resolve()
    entity_groups = resolver.get_entity_groups()

# Enrich groups with per-source frequency
for group in entity_groups:
    aliases_set = set(group["aliases"]) | {group["canonical"]}
    source_freq: Counter[str] = Counter()
    for e in combined_entities:
        if e.get("text", "").strip() in aliases_set:
            source_freq[e["_source"]] += 1
    group["source_freq"] = dict(source_freq)
    group["n_sources"] = len(source_freq)

# Apply sorting
if sort_order == "Alphabetical":
    entity_groups.sort(key=lambda g: g["canonical"].lower())
# else already sorted by count from get_entity_groups()

# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------

tab_groups, tab_matrix, tab_stats = st.tabs(
    ["Entity Groups", "Cross-Source Matrix", "Statistics"]
)

# === Tab 1: Entity Groups ===
with tab_groups:
    st.subheader("Resolved Entity Groups")
    st.caption(f"{len(entity_groups)} groups")

    if not entity_groups:
        st.info("No entity groups after resolution.")
    else:
        for idx, group in enumerate(entity_groups):
            canonical = group["canonical"]
            label = group["label"]
            aliases = group["aliases"]
            count = group["count"]
            source_freq = group["source_freq"]
            n_sources = group["n_sources"]

            chip_html = render_entity_chip_html(canonical, label, count=count)
            source_tag = (
                f'<span style="color:#a1a1aa;font-size:0.75rem;margin-left:0.5em;">'
                f'{n_sources} source{"s" if n_sources != 1 else ""}</span>'
            )

            with st.expander(f"{canonical}  ({label} | {count} mentions | {n_sources} sources)"):
                # Chip and basic info
                st.markdown(chip_html + source_tag, unsafe_allow_html=True)

                # Aliases
                if aliases:
                    st.markdown(
                        "**Aliases:** "
                        + ", ".join(f"`{a}`" for a in sorted(aliases))
                    )

                # Per-source frequency bar chart
                if source_freq:
                    freq_df = pd.DataFrame(
                        [{"Source": s, "Count": c} for s, c in sorted(source_freq.items())]
                    )
                    fig = go.Figure(
                        go.Bar(
                            x=freq_df["Source"],
                            y=freq_df["Count"],
                            marker_color="#00fcd6",
                            text=freq_df["Count"],
                            textposition="auto",
                        )
                    )
                    fig.update_layout(template=get_plotly_template())
                    fig.update_layout(
                        height=250,
                        xaxis_title="",
                        yaxis_title="Mentions",
                        margin=dict(t=10, b=40, l=40, r=10),
                        showlegend=False,
                    )
                    st.plotly_chart(fig, use_container_width=True, key=f"freq_{idx}")

                # Navigation links
                link_cols = st.columns(2)
                with link_cols[0]:
                    if st.button(
                        "View in Entity Concordance",
                        key=f"nav_ev_{idx}",
                    ):
                        navigate_to(
                            "pages/8_Entity_Concordance.py",
                            entity_text=canonical,
                            entity_label=label,
                        )
                with link_cols[1]:
                    if st.button(
                        "View in Entity Concordance",
                        key=f"nav_ec_{idx}",
                    ):
                        navigate_to(
                            "pages/8_Entity_Concordance.py",
                            entity_text=canonical,
                            entity_label=label,
                        )

# === Tab 2: Cross-Source Matrix ===
with tab_matrix:
    st.subheader("Entity-Source Heatmap")

    # Build matrix: rows = top entities by total count, columns = sources
    top_n = min(50, len(entity_groups))
    top_groups = sorted(entity_groups, key=lambda g: g["count"], reverse=True)[:top_n]

    if not top_groups or len(selected_sources) < 2:
        st.info(
            "Select at least 2 sources and ensure entities are available "
            "to display the cross-source matrix."
        )
    else:
        entity_names = [g["canonical"] for g in top_groups]
        matrix_data: list[list[int]] = []
        for g in top_groups:
            row = [g["source_freq"].get(s, 0) for s in selected_sources]
            matrix_data.append(row)

        fig = go.Figure(
            go.Heatmap(
                z=matrix_data,
                x=selected_sources,
                y=entity_names,
                colorscale=[
                    [0.0, "#0a0a0f"],
                    [0.2, "#16161d"],
                    [0.4, "#1a3a35"],
                    [0.6, "#0e6e5c"],
                    [0.8, "#00c9a7"],
                    [1.0, "#00fcd6"],
                ],
                hoverongaps=False,
                hovertemplate=(
                    "<b>%{y}</b><br>"
                    "Source: %{x}<br>"
                    "Mentions: %{z}<extra></extra>"
                ),
                colorbar=dict(
                    title="Mentions",
                    title_font=dict(color="#a1a1aa", family="Rajdhani, sans-serif"),
                    tickfont=dict(color="#a1a1aa", family="Space Mono, monospace", size=10),
                ),
            )
        )
        fig.update_layout(template=get_plotly_template())
        fig.update_layout(
            height=max(400, top_n * 22),
            xaxis=dict(
                side="top",
                tickfont=dict(family="Rajdhani, sans-serif", size=12, color="#a1a1aa"),
            ),
            yaxis=dict(
                autorange="reversed",
                tickfont=dict(family="Rajdhani, sans-serif", size=11, color="#e4e4e7"),
            ),
            margin=dict(t=60, b=20, l=200, r=40),
        )
        st.plotly_chart(fig, use_container_width=True, key="heatmap")

        st.caption(
            f"Showing top {top_n} entities by total mention count across "
            f"{len(selected_sources)} sources."
        )

# === Tab 3: Statistics ===
with tab_stats:
    st.subheader("Resolution Statistics")

    # Compute stats
    total_raw = len(combined_entities)
    unique_raw_texts = len({e.get("text", "").strip() for e in combined_entities if e.get("text")})
    unique_after = len(entity_groups)
    reduction_pct = (
        ((unique_raw_texts - unique_after) / unique_raw_texts * 100)
        if unique_raw_texts > 0
        else 0.0
    )

    # Metrics row
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total Mentions", f"{total_raw:,}")
    m2.metric("Unique Texts", f"{unique_raw_texts:,}")
    m3.metric("After Resolution", f"{unique_after:,}")
    m4.metric("Reduction", f"{reduction_pct:.1f}%")

    st.divider()

    # Label distribution chart
    st.markdown("**Entity Label Distribution**")

    label_counts: Counter[str] = Counter()
    for e in combined_entities:
        lbl = e.get("label", "UNKNOWN").upper()
        label_counts[lbl] += 1

    if label_counts:
        label_df = pd.DataFrame(
            [{"Label": lbl, "Count": cnt} for lbl, cnt in label_counts.most_common()]
        )
        label_colors = [
            ENTITY_COLORS.get(lbl, "#a1a1aa") for lbl in label_df["Label"]
        ]

        fig = go.Figure(
            go.Bar(
                x=label_df["Label"],
                y=label_df["Count"],
                marker_color=label_colors,
                text=label_df["Count"],
                textposition="auto",
                textfont=dict(color="#e4e4e7", family="Space Mono, monospace", size=11),
            )
        )
        fig.update_layout(template=get_plotly_template())
        fig.update_layout(
            height=350,
            xaxis_title="",
            yaxis_title="Mentions",
            margin=dict(t=10, b=40, l=60, r=10),
            showlegend=False,
        )
        st.plotly_chart(fig, use_container_width=True, key="label_dist")

    st.divider()

    # Per-source stats table
    st.markdown("**Per-Source Summary**")
    source_stats_rows: list[dict] = []
    for source in selected_sources:
        source_ents = [e for e in combined_entities if e["_source"] == source]
        unique_texts = len({e.get("text", "").strip() for e in source_ents if e.get("text")})
        labels_in_source = len({e.get("label", "") for e in source_ents})
        source_stats_rows.append({
            "Source": source,
            "Total Mentions": len(source_ents),
            "Unique Texts": unique_texts,
            "Labels": labels_in_source,
        })

    if source_stats_rows:
        stats_df = pd.DataFrame(source_stats_rows)
        st.dataframe(stats_df, use_container_width=True, hide_index=True)

    # Cross-source entity overlap
    st.divider()
    st.markdown("**Cross-Source Entity Overlap**")
    multi_source_groups = [g for g in entity_groups if g["n_sources"] > 1]
    single_source_groups = [g for g in entity_groups if g["n_sources"] == 1]

    ov1, ov2 = st.columns(2)
    ov1.metric("Entities in Multiple Sources", f"{len(multi_source_groups):,}")
    ov2.metric("Entities in Single Source", f"{len(single_source_groups):,}")

    if multi_source_groups:
        st.caption("Top cross-source entities:")
        top_cross = sorted(multi_source_groups, key=lambda g: g["count"], reverse=True)[:20]
        cross_html_parts: list[str] = []
        for g in top_cross:
            cross_html_parts.append(render_entity_chip_html(g["canonical"], g["label"], count=g["count"]))
        st.markdown("  ".join(cross_html_parts), unsafe_allow_html=True)
