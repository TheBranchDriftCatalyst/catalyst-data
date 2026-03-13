"""Entity Viewer — NER entities, SPO propositions, graph visualization."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from data_explorer.streamlit.config import get_s3_config
from data_explorer.streamlit.data_client import DataClient

st.set_page_config(page_title="Entity Viewer", page_icon=":material/hub:", layout="wide")
st.header("Entity & Proposition Viewer")


@st.cache_resource
def _get_client() -> DataClient:
    cfg = get_s3_config()
    return DataClient(
        endpoint_url=cfg.endpoint_url,
        access_key=cfg.access_key,
        secret_key=cfg.secret_key,
        bucket=cfg.bucket,
    )


@st.cache_data(ttl=300)
def _find_entity_assets() -> tuple[list[dict], list[dict]]:
    client = _get_client()
    assets = client.list_assets()
    entities = [a for a in assets if "entit" in a["asset"].lower() and a["layer"] == "gold"]
    propositions = [a for a in assets if "proposition" in a["asset"].lower() and a["layer"] == "gold"]
    return entities, propositions


@st.cache_data(ttl=300)
def _load_rows(root: str, limit: int) -> list[dict]:
    return _get_client().load_data(root, limit=limit)


entity_assets, prop_assets = _find_entity_assets()

if not entity_assets and not prop_assets:
    st.info("No entity or proposition assets found (looking for gold/*entities*, gold/*propositions*).")
    st.stop()

tab_entities, tab_propositions, tab_graph = st.tabs(["Entities", "Propositions", "Graph"])

# === Tab 1: Entities ===
with tab_entities:
    if not entity_assets:
        st.info("No entity assets found.")
    else:
        with st.sidebar:
            st.subheader("Entity Source")
            ent_options = {f"{a['code_location']}/{a['asset']}": a for a in entity_assets}
            sel_ent = st.selectbox("Entity asset", list(ent_options.keys()), key="ent_sel")
            ent_limit = st.slider("Max entities", 100, 2000, 500, step=100, key="ent_limit")

        rows = _load_rows(ent_options[sel_ent]["root"], ent_limit)
        if rows:
            df = pd.DataFrame(rows)
            st.caption(f"{len(df)} entities from **{sel_ent}**")

            # Filter by label if available
            if "label" in df.columns:
                labels = ["All"] + sorted(df["label"].dropna().unique().tolist())
                sel_label = st.selectbox("Filter by label", labels)
                if sel_label != "All":
                    df = df[df["label"] == sel_label]

            st.dataframe(df, use_container_width=True, height=500)
        else:
            st.warning("No entity data found.")

# === Tab 2: Propositions ===
with tab_propositions:
    if not prop_assets:
        st.info("No proposition assets found.")
    else:
        prop_options = {f"{a['code_location']}/{a['asset']}": a for a in prop_assets}
        sel_prop = st.selectbox("Proposition asset", list(prop_options.keys()), key="prop_sel")
        prop_limit = st.slider("Max propositions", 100, 2000, 500, step=100, key="prop_limit")

        rows = _load_rows(prop_options[sel_prop]["root"], prop_limit)
        if rows:
            df = pd.DataFrame(rows)
            st.caption(f"{len(df)} propositions from **{sel_prop}**")

            # Confidence slider
            if "confidence" in df.columns:
                min_conf = st.slider("Min confidence", 0.0, 1.0, 0.5, step=0.05)
                df = df[df["confidence"] >= min_conf]

            st.dataframe(df, use_container_width=True, height=500)
        else:
            st.warning("No proposition data found.")

# === Tab 3: Graph Visualization ===
with tab_graph:
    st.subheader("SPO Triple Graph")

    # Use propositions for graph if available
    if not prop_assets:
        st.info("No proposition data available for graph visualization.")
    else:
        if "sel_prop" not in dir():
            prop_options = {f"{a['code_location']}/{a['asset']}": a for a in prop_assets}
            sel_prop = list(prop_options.keys())[0]

        graph_limit = st.slider("Max triples for graph", 50, 500, 100, step=50, key="graph_limit")
        rows = _load_rows(prop_options[sel_prop]["root"], graph_limit)

        if rows:
            df = pd.DataFrame(rows)
            # Need subject, predicate, object columns
            subj_col = next((c for c in df.columns if c in ("subject", "head", "source")), None)
            pred_col = next((c for c in df.columns if c in ("predicate", "relation", "rel_type")), None)
            obj_col = next((c for c in df.columns if c in ("object", "tail", "target")), None)

            if subj_col and obj_col:
                # Build node/edge lists
                nodes = list(set(df[subj_col].tolist() + df[obj_col].tolist()))
                node_idx = {n: i for i, n in enumerate(nodes)}

                import math
                # Circular layout
                n = len(nodes)
                angles = [2 * math.pi * i / n for i in range(n)]
                node_x = [math.cos(a) for a in angles]
                node_y = [math.sin(a) for a in angles]

                edge_x, edge_y = [], []
                for _, row in df.iterrows():
                    s, o = node_idx.get(row[subj_col]), node_idx.get(row[obj_col])
                    if s is not None and o is not None:
                        edge_x.extend([node_x[s], node_x[o], None])
                        edge_y.extend([node_y[s], node_y[o], None])

                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=edge_x, y=edge_y, mode="lines",
                    line=dict(width=0.5, color="#888"), hoverinfo="none",
                ))
                fig.add_trace(go.Scatter(
                    x=node_x, y=node_y, mode="markers+text",
                    text=[str(n)[:20] for n in nodes],
                    textposition="top center",
                    marker=dict(size=8, color="#1f77b4"),
                    hovertext=nodes,
                ))
                fig.update_layout(
                    showlegend=False, height=600,
                    xaxis=dict(visible=False), yaxis=dict(visible=False),
                    margin=dict(t=20, b=20, l=20, r=20),
                )
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.warning(f"Could not identify subject/object columns. Available: {list(df.columns)}")
        else:
            st.warning("No proposition data found for graph.")
