"""KWIC (Key Word In Context) concordance view component."""

from __future__ import annotations

import html
from collections import Counter

import plotly.graph_objects as go
import streamlit as st

from data_explorer.streamlit.components.entity_chip import (
    ENTITY_COLORS,
    _color_for_label,
    _hex_to_rgba,
)
from data_explorer.streamlit.theme import get_plotly_template

_DEFAULT_COLOR = "#a1a1aa"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _kwic_split(
    context: str,
    entity_text: str,
    window: int,
) -> tuple[str, str, str]:
    """Split *context* around the first occurrence of *entity_text*.

    Returns ``(left, match, right)`` where *left* and *right* are trimmed to
    at most *window* characters.  If *entity_text* is not found the entire
    context is placed in *left* and *match* / *right* are empty.
    """
    lower_ctx = context.lower()
    lower_ent = entity_text.lower()
    pos = lower_ctx.find(lower_ent)

    if pos == -1:
        # Entity not found verbatim — fall back gracefully.
        return (context[-window:], entity_text, "")

    start = pos
    end = pos + len(entity_text)

    left = context[max(0, start - window) : start]
    match = context[start:end]
    right = context[end : end + window]

    return (left, match, right)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def render_concordance(
    matches: list[dict],
    context_window: int = 60,
) -> None:
    """Render a KWIC concordance table for a list of entity matches.

    Each element of *matches* is a dict with keys:

    - ``text`` -- the entity surface form
    - ``label`` -- NER label (``PERSON``, ``ORG``, ...)
    - ``context`` -- surrounding text passage
    - ``source_doc_id`` -- document identifier
    - ``chunk_id`` -- chunk identifier within the document

    The concordance is rendered as an HTML table injected via
    ``st.markdown(unsafe_allow_html=True)`` so that left/right context
    alignment and per-label colouring are preserved.
    """
    if not matches:
        st.info("No concordance matches to display.")
        return

    # Build HTML rows -------------------------------------------------------
    rows: list[str] = []
    for m in matches:
        entity_text: str = m["text"]
        label: str = m["label"]
        context: str = m.get("context", "")
        doc_id: str = m.get("source_doc_id", "")
        chunk_id: str = m.get("chunk_id", "")

        left, match_text, right = _kwic_split(context, entity_text, context_window)

        color = _color_for_label(label)
        bg = _hex_to_rgba(color, 0.20)

        safe_left = html.escape(left)
        safe_match = html.escape(match_text)
        safe_right = html.escape(right)
        safe_label = html.escape(label)
        safe_doc = html.escape(doc_id)
        safe_chunk = html.escape(chunk_id)

        rows.append(
            "<tr>"
            # Left context — right-aligned
            f'<td class="kwic-left">{safe_left}</td>'
            # Entity — centre-highlighted
            f'<td class="kwic-entity" style="background:{bg};color:{color};">'
            f"{safe_match}"
            f'<span class="kwic-label" style="color:{color};opacity:0.6;">'
            f" [{safe_label}]</span></td>"
            # Right context — left-aligned
            f'<td class="kwic-right">{safe_right}</td>'
            # Source caption
            f'<td class="kwic-source" title="chunk: {safe_chunk}">{safe_doc}</td>'
            "</tr>"
        )

    table_html = f"""
    <style>
    table.kwic-table {{
        width: 100%;
        border-collapse: collapse;
        font-family: "Space Mono", ui-monospace, monospace;
        font-size: 0.78rem;
        line-height: 1.6;
    }}
    table.kwic-table td {{
        padding: 0.25rem 0.4rem;
        border-bottom: 1px solid #27272a;
        vertical-align: middle;
    }}
    table.kwic-table tr:hover {{
        background: rgba(0,252,214,0.04);
    }}
    td.kwic-left {{
        text-align: right;
        color: #a1a1aa;
        white-space: pre;
        max-width: 40%;
        overflow: hidden;
        text-overflow: ellipsis;
    }}
    td.kwic-entity {{
        text-align: center;
        font-weight: 700;
        white-space: nowrap;
        border-left: 1px solid #27272a;
        border-right: 1px solid #27272a;
        padding-left: 0.6rem;
        padding-right: 0.6rem;
    }}
    .kwic-label {{
        font-size: 0.65rem;
        font-weight: 400;
    }}
    td.kwic-right {{
        text-align: left;
        color: #a1a1aa;
        white-space: pre;
        max-width: 40%;
        overflow: hidden;
        text-overflow: ellipsis;
    }}
    td.kwic-source {{
        text-align: right;
        color: #52525b;
        font-size: 0.65rem;
        max-width: 12%;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }}
    </style>
    <table class="kwic-table">
    <tbody>
    {"".join(rows)}
    </tbody>
    </table>
    """

    st.markdown(table_html, unsafe_allow_html=True)


def render_concordance_stats(matches: list[dict]) -> None:
    """Show summary statistics and a label distribution chart for concordance *matches*.

    Displays:
    - Metric cards: total matches, unique documents, unique entity strings.
    - A horizontal Plotly bar chart showing the count per NER label, coloured
      with the shared ``ENTITY_COLORS`` palette.
    """
    if not matches:
        return

    total = len(matches)
    unique_docs = len({m.get("source_doc_id", "") for m in matches})
    unique_entities = len({m.get("text", "") for m in matches})

    cols = st.columns(3)
    cols[0].metric("Total Matches", total)
    cols[1].metric("Unique Documents", unique_docs)
    cols[2].metric("Unique Entities", unique_entities)

    # Label distribution ----------------------------------------------------
    label_counts: Counter[str] = Counter(m.get("label", "UNKNOWN") for m in matches)
    labels_sorted = sorted(label_counts.keys(), key=lambda k: label_counts[k])

    colors = [_color_for_label(lbl) for lbl in labels_sorted]
    counts = [label_counts[lbl] for lbl in labels_sorted]

    template = get_plotly_template()
    fig = go.Figure(
        go.Bar(
            x=counts,
            y=labels_sorted,
            orientation="h",
            marker_color=colors,
            text=counts,
            textposition="outside",
            textfont=dict(
                family="Space Mono, monospace",
                size=11,
                color="#e4e4e7",
            ),
        )
    )
    fig.update_layout(
        template=template,
        title="Label Distribution",
        xaxis_title=None,
        yaxis_title=None,
        height=max(200, len(labels_sorted) * 40 + 80),
        margin=dict(l=10, r=30, t=40, b=20),
        showlegend=False,
    )
    fig.update_yaxes(
        tickfont=dict(family="Rajdhani, sans-serif", size=13, color="#e4e4e7"),
    )

    st.plotly_chart(fig, use_container_width=True)
