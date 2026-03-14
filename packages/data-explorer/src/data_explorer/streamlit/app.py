"""Data Explorer — Corpus dashboard home page."""

from __future__ import annotations

from collections import Counter

import plotly.graph_objects as go
import streamlit as st

from data_explorer.streamlit.config import get_s3_config
from data_explorer.streamlit.data_client import DataClient
from data_explorer.streamlit.navigation import PAGE_LABELS, navigate_to
from data_explorer.streamlit.theme import apply_theme, get_plotly_template

st.set_page_config(
    page_title="Data Explorer",
    page_icon=":material/database:",
    layout="wide",
    initial_sidebar_state="expanded",
)
apply_theme()

# ---------------------------------------------------------------------------
# Page descriptions for quick-access cards
# ---------------------------------------------------------------------------

PAGE_DESCRIPTIONS: dict[str, str] = {
    "Asset Browser": "Browse materialized Dagster assets",
    "Document Explorer": "Explore documents by source",
    "Knowledge Graph": "Interactive SPO graph visualization",
    "Document Lens": "Deep-read view with NER overlays",
    "Media Player": "Video/audio playback with transcripts",
    "Semantic Explorer": "Embedding-powered semantic search",
    "Cross-Source Linker": "Entity resolution across sources",
    "Data Chat": "RAG-powered Q&A over corpus",
    "Entity Concordance": "KWIC concordance and co-occurrence",
}

# ---------------------------------------------------------------------------
# S3 client (cached resource)
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
# Dashboard
# ---------------------------------------------------------------------------


def main() -> None:
    client = _get_client()

    # -- Sidebar: connection status -------------------------------------------
    with st.sidebar:
        st.title("Data Explorer")
        st.caption(f"S3: `{client.s3.bucket}`")

        try:
            keys = client.s3.list_objects("")
            st.success(f"Connected — {len(keys)} top-level keys")
        except Exception as e:
            st.error(f"S3 connection failed: {e}")

        st.divider()

        if st.button("Refresh catalog"):
            st.cache_data.clear()

    # -- Load asset catalog ---------------------------------------------------
    st.header("Catalyst Data Explorer")

    assets = client.list_assets()

    if not assets:
        st.info("No assets found. Check S3 connection and bucket contents.")
        return

    # Pre-compute breakdowns
    sources = client.list_sources()
    layers: dict[str, int] = Counter(a["layer"] for a in assets)
    code_locations: set[str] = {a["code_location"] for a in assets}

    # ── Row 1: Metric cards ─────────────────────────────────────────────────
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total Assets", len(assets))
    m2.metric("Total Sources", len(sources))
    m3.metric("Layers", len(layers))
    m4.metric("Code Locations", len(code_locations))

    st.divider()

    # ── Row 2: Charts ───────────────────────────────────────────────────────
    tpl = get_plotly_template()
    chart_left, chart_right = st.columns(2)

    # -- Left: Source breakdown donut chart -----------------------------------
    with chart_left:
        st.subheader("Source Breakdown")
        loc_counts = Counter(a["code_location"] for a in assets)
        labels = list(loc_counts.keys())
        values = list(loc_counts.values())

        fig_donut = go.Figure(
            data=[
                go.Pie(
                    labels=labels,
                    values=values,
                    hole=0.5,
                    textinfo="label+value",
                    textposition="outside",
                    marker=dict(
                        colors=tpl["layout"]["colorway"][: len(labels)],
                        line=dict(color="#0a0a0f", width=2),
                    ),
                    hovertemplate="<b>%{label}</b><br>Assets: %{value}<br>%{percent}<extra></extra>",
                )
            ]
        )
        fig_donut.update_layout(template=get_plotly_template())
        fig_donut.update_layout(
            showlegend=False,
            height=380,
            margin=dict(t=20, b=20, l=20, r=20),
        )
        st.plotly_chart(fig_donut, use_container_width=True)

    # -- Right: Layer distribution horizontal bar chart -----------------------
    with chart_right:
        st.subheader("Layer Distribution")
        layer_order = ["bronze", "silver", "gold"]
        sorted_layers = sorted(layers.keys(), key=lambda x: layer_order.index(x) if x in layer_order else 99)
        layer_names = list(sorted_layers)
        layer_values = [layers[l] for l in sorted_layers]
        colorway = tpl["layout"]["colorway"]

        fig_bar = go.Figure(
            data=[
                go.Bar(
                    y=layer_names,
                    x=layer_values,
                    orientation="h",
                    marker=dict(
                        color=[colorway[i % len(colorway)] for i in range(len(layer_names))],
                        line=dict(color="#0a0a0f", width=1),
                    ),
                    text=layer_values,
                    textposition="auto",
                    hovertemplate="<b>%{y}</b><br>Assets: %{x}<extra></extra>",
                )
            ]
        )
        fig_bar.update_layout(template=get_plotly_template())
        fig_bar.update_layout(
            height=380,
            margin=dict(t=20, b=20, l=20, r=20),
            xaxis_title="Asset Count",
            yaxis=dict(
                categoryorder="array",
                categoryarray=list(reversed(layer_names)),
            ),
        )
        st.plotly_chart(fig_bar, use_container_width=True)

    st.divider()

    # ── Row 3: Quick-access page cards ──────────────────────────────────────
    st.subheader("Quick Access")

    page_items = list(PAGE_LABELS.items())
    # Render in rows of 3
    for row_start in range(0, len(page_items), 3):
        row_items = page_items[row_start : row_start + 3]
        cols = st.columns(3)
        for col, (page_path, page_name) in zip(cols, row_items):
            with col:
                with st.container(border=True):
                    st.subheader(page_name, anchor=False)
                    description = PAGE_DESCRIPTIONS.get(page_name, "")
                    if description:
                        st.caption(description)
                    if st.button(
                        f"Open {page_name}",
                        key=f"nav_{page_path}",
                        use_container_width=True,
                    ):
                        navigate_to(page_path)


# Streamlit pages are discovered from the pages/ directory alongside this file.
# This file serves as the home page.
main()
