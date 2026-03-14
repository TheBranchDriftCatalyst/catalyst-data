"""Reusable 2D embedding scatter visualization with dimensionality reduction."""

from __future__ import annotations

import hashlib
from typing import Any

import numpy as np
import plotly.graph_objects as go
import streamlit as st
from sklearn.cluster import DBSCAN
from sklearn.decomposition import PCA
from sklearn.manifold import TSNE

from data_explorer.streamlit.theme import get_plotly_template

try:
    import umap

    _HAS_UMAP = True
except ImportError:
    _HAS_UMAP = False

COLORS = [
    "#00fcd6", "#c026d3", "#ff6ec7", "#00d4ff",
    "#fbbf24", "#ff2975", "#22c55e", "#6366f1",
]


# ---------------------------------------------------------------------------
# Dimensionality reduction
# ---------------------------------------------------------------------------

def _embedding_cache_key(embeddings: np.ndarray, method: str) -> str:
    h = hashlib.sha256()
    h.update(f"{embeddings.shape}|{method}".encode())
    h.update(embeddings.tobytes()[:4096])
    return h.hexdigest()


@st.cache_data(show_spinner="Reducing dimensions...")
def _reduce_dimensions(
    _embeddings: np.ndarray,
    method: str,
    _cache_key: str,
    **kwargs: Any,
) -> np.ndarray:
    n_samples = _embeddings.shape[0]

    if n_samples < 5:
        if _embeddings.shape[1] >= 2:
            return PCA(n_components=2, random_state=42).fit_transform(_embeddings)
        return np.column_stack([_embeddings[:, 0], np.zeros(n_samples)])

    if method == "umap":
        if not _HAS_UMAP:
            st.warning("umap-learn not installed — falling back to t-SNE.")
            method = "tsne"
        else:
            n_neighbors = min(kwargs.get("n_neighbors", 15), n_samples - 1)
            reducer = umap.UMAP(
                n_components=2,
                n_neighbors=n_neighbors,
                min_dist=kwargs.get("min_dist", 0.1),
                metric=kwargs.get("metric", "cosine"),
                random_state=42,
            )
            return reducer.fit_transform(_embeddings)

    if method == "tsne":
        perplexity = min(kwargs.get("perplexity", 30), max(1.0, n_samples - 1.0))
        return TSNE(n_components=2, perplexity=perplexity, random_state=42).fit_transform(_embeddings)

    raise ValueError(f"Unknown method: {method!r}")


# ---------------------------------------------------------------------------
# Sidebar controls
# ---------------------------------------------------------------------------

def render_reduction_controls() -> tuple[str, dict]:
    """Render sidebar widgets for reduction settings. Returns (method, params)."""
    with st.sidebar:
        st.subheader("Reduction Settings")
        methods = ["umap", "tsne"] if _HAS_UMAP else ["tsne"]
        method = st.selectbox("Method", methods, index=0)
        params: dict[str, Any] = {}

        if method == "umap":
            params["n_neighbors"] = st.slider("n_neighbors", 2, 200, 15)
            params["min_dist"] = st.slider("min_dist", 0.0, 1.0, 0.1, step=0.05)
            params["metric"] = st.selectbox("Metric", ["cosine", "euclidean", "manhattan"])
        else:
            params["perplexity"] = st.slider("Perplexity", 2, 100, 30)

    return method, params


# ---------------------------------------------------------------------------
# Main scatter
# ---------------------------------------------------------------------------

def render_embedding_scatter(
    embeddings: np.ndarray,
    labels: list[str] | None = None,
    metadata: list[dict] | None = None,
    highlight_indices: list[int] | None = None,
    method: str = "umap",
    title: str = "Embedding Space",
    show_clusters: bool = True,
    **reduction_kwargs: Any,
) -> None:
    """Render an interactive 2D scatter plot of embedding vectors."""
    n_points = embeddings.shape[0]
    if n_points == 0:
        st.info("No embeddings to display.")
        return

    cache_key = _embedding_cache_key(embeddings, method)
    coords_2d = _reduce_dimensions(embeddings, method, _cache_key=cache_key, **reduction_kwargs)
    x, y = coords_2d[:, 0], coords_2d[:, 1]

    cluster_labels = DBSCAN(eps=0.5, min_samples=5).fit_predict(coords_2d)

    if labels is not None:
        colour_key = labels
        colour_label = "Label"
    else:
        colour_key = [f"Cluster {c}" if c >= 0 else "Noise" for c in cluster_labels]
        colour_label = "Cluster"

    unique_keys = sorted(set(colour_key))
    key_to_colour = {k: COLORS[i % len(COLORS)] for i, k in enumerate(unique_keys)}
    point_colours = [key_to_colour[k] for k in colour_key]

    # Hover text
    hover_texts: list[str] = []
    for i in range(n_points):
        parts: list[str] = [f"<b>{colour_label}:</b> {colour_key[i]}"]
        if metadata and i < len(metadata):
            meta = metadata[i]
            snippet = str(meta.get("text", ""))[:100]
            if snippet:
                parts.append(f"<b>Text:</b> {snippet}")
            doc_id = meta.get("document_id")
            if doc_id:
                parts.append(f"<b>Doc:</b> {doc_id}")
        hover_texts.append("<br>".join(parts))

    # Build figure
    fig = go.Figure()
    fig.update_layout(template=get_plotly_template())
    highlight_set = set(highlight_indices) if highlight_indices else set()

    for key in unique_keys:
        idx = [i for i in range(n_points) if colour_key[i] == key and i not in highlight_set]
        if not idx:
            continue
        fig.add_trace(go.Scatter(
            x=x[idx], y=y[idx], mode="markers",
            marker=dict(size=6, color=key_to_colour[key], opacity=0.75),
            name=key, text=[hover_texts[i] for i in idx],
            hovertemplate="%{text}<extra></extra>", legendgroup=key,
        ))

    if highlight_set:
        hi_idx = sorted(highlight_set)
        fig.add_trace(go.Scatter(
            x=x[hi_idx], y=y[hi_idx], mode="markers",
            marker=dict(size=12, color=[point_colours[i] for i in hi_idx],
                        opacity=1.0, line=dict(width=2, color="white")),
            name="Highlighted", text=[hover_texts[i] for i in hi_idx],
            hovertemplate="%{text}<extra></extra>",
        ))

    # Cluster convex hulls
    if show_clusters:
        try:
            from scipy.spatial import ConvexHull
        except ImportError:
            ConvexHull = None  # type: ignore[assignment,misc]

        if ConvexHull is not None:
            for cid in sorted(set(cluster_labels)):
                if cid < 0:
                    continue
                members = np.where(cluster_labels == cid)[0]
                if len(members) < 3:
                    continue
                try:
                    hull = ConvexHull(coords_2d[members])
                except Exception:
                    continue
                hp = members[hull.vertices]
                hx = np.append(x[hp], x[hp[0]])
                hy = np.append(y[hp], y[hp[0]])
                hc = COLORS[cid % len(COLORS)]
                r, g, b = int(hc[1:3], 16), int(hc[3:5], 16), int(hc[5:7], 16)
                fig.add_trace(go.Scatter(
                    x=hx, y=hy, mode="lines",
                    line=dict(color=hc, width=1, dash="dot"),
                    fill="toself", fillcolor=f"rgba({r},{g},{b},0.06)",
                    opacity=0.3, showlegend=False, hoverinfo="skip",
                ))

    fig.update_layout(
        title=dict(text=title),
        xaxis=dict(title="", showticklabels=False, showgrid=False),
        yaxis=dict(title="", showticklabels=False, showgrid=False),
        legend=dict(orientation="h", yanchor="bottom", y=-0.15, xanchor="center", x=0.5),
        margin=dict(l=20, r=20, t=50, b=60),
        hovermode="closest",
    )
    st.plotly_chart(fig, use_container_width=True)
