"""KWIC (Key Word In Context) concordance view and assertion rendering components."""

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

# Colors for mention types beyond the standard entity colors
MENTION_TYPE_COLORS: dict[str, str] = {
    **ENTITY_COLORS,
    "LOC": "#06b6d4",
    "MONEY": "#eab308",
    "NORP": "#8b5cf6",
    "FACILITY": "#f97316",
    "OTHER": "#a1a1aa",
}


def _mention_color(mention_type: str) -> str:
    """Return color for a mention type."""
    return MENTION_TYPE_COLORS.get(mention_type.upper(), _DEFAULT_COLOR)


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
        # Entity not found verbatim -- fall back gracefully.
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

    Supports both legacy entity dicts (text, label, context, source_doc_id, chunk_id)
    and new mention dicts (text, mention_type, context, document_id, chunk_id).
    """
    if not matches:
        st.info("No concordance matches to display.")
        return

    # Build HTML rows
    rows: list[str] = []
    for m in matches:
        entity_text: str = m.get("text", "")
        # Support both "label" (legacy) and "mention_type" (new)
        label: str = m.get("label", "") or m.get("mention_type", "")
        context: str = m.get("context", "")
        doc_id: str = m.get("source_doc_id", "") or m.get("document_id", "")
        chunk_id: str = m.get("chunk_id", "")

        left, match_text, right = _kwic_split(context, entity_text, context_window)

        color = _mention_color(label) if label else _color_for_label(label)
        bg = _hex_to_rgba(color, 0.20)

        safe_left = html.escape(left)
        safe_match = html.escape(match_text)
        safe_right = html.escape(right)
        safe_label = html.escape(label)
        safe_doc = html.escape(doc_id)
        safe_chunk = html.escape(chunk_id)

        rows.append(
            "<tr>"
            f'<td class="kwic-left">{safe_left}</td>'
            f'<td class="kwic-entity" style="background:{bg};color:{color};">'
            f"{safe_match}"
            f'<span class="kwic-label" style="color:{color};opacity:0.6;">'
            f" [{safe_label}]</span></td>"
            f'<td class="kwic-right">{safe_right}</td>'
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

    Works with both legacy entity dicts and new mention dicts.
    """
    if not matches:
        return

    total = len(matches)
    unique_docs = len({m.get("source_doc_id", "") or m.get("document_id", "") for m in matches})
    unique_entities = len({m.get("text", "") for m in matches})

    cols = st.columns(3)
    cols[0].metric("Total Matches", total)
    cols[1].metric("Unique Documents", unique_docs)
    cols[2].metric("Unique Entities", unique_entities)

    # Label distribution
    label_counts: Counter[str] = Counter(
        m.get("label", "") or m.get("mention_type", "UNKNOWN") for m in matches
    )
    labels_sorted = sorted(label_counts.keys(), key=lambda k: label_counts[k])

    colors = [_mention_color(lbl) for lbl in labels_sorted]
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


def render_mention_stats(mentions: list[dict]) -> None:
    """Show summary statistics for mention-based data.

    Displays mention type distribution with colored badges.
    """
    if not mentions:
        return

    total = len(mentions)
    unique_docs = len({m.get("document_id", "") for m in mentions})
    unique_texts = len({m.get("text", "") for m in mentions})

    cols = st.columns(3)
    cols[0].metric("Total Mentions", total)
    cols[1].metric("Unique Documents", unique_docs)
    cols[2].metric("Unique Entity Texts", unique_texts)

    # Mention type distribution
    type_counts: Counter[str] = Counter(m.get("mention_type", "UNKNOWN") for m in mentions)
    types_sorted = sorted(type_counts.keys(), key=lambda k: type_counts[k])

    colors = [_mention_color(t) for t in types_sorted]
    counts = [type_counts[t] for t in types_sorted]

    template = get_plotly_template()
    fig = go.Figure(
        go.Bar(
            x=counts,
            y=types_sorted,
            orientation="h",
            marker_color=colors,
            text=counts,
            textposition="outside",
            textfont=dict(family="Space Mono, monospace", size=11, color="#e4e4e7"),
        )
    )
    fig.update_layout(
        template=template,
        title="Mention Type Distribution",
        xaxis_title=None,
        yaxis_title=None,
        height=max(200, len(types_sorted) * 40 + 80),
        margin=dict(l=10, r=30, t=40, b=20),
        showlegend=False,
    )
    fig.update_yaxes(
        tickfont=dict(family="Rajdhani, sans-serif", size=13, color="#e4e4e7"),
    )
    st.plotly_chart(fig, use_container_width=True)


def render_assertion_table(assertions: list[dict]) -> None:
    """Render an assertion table with qualifier chips, negation, and hedging indicators.

    Each assertion is displayed as a row with:
    - Subject and object text
    - Predicate (canonical preferred)
    - Qualifier chips (time, location, etc.)
    - Negation indicator (strikethrough styling)
    - Hedging indicator (italic styling)
    - Confidence score
    """
    if not assertions:
        st.info("No assertions to display.")
        return

    rows: list[str] = []
    for a in assertions:
        subject = html.escape(a.get("subject_text", ""))
        predicate = html.escape(a.get("predicate_canonical", "") or a.get("predicate", ""))
        obj = html.escape(a.get("object_text", ""))
        confidence = a.get("confidence", 1.0)
        negated = a.get("negated", False)
        hedged = a.get("hedged", False)
        qualifiers = a.get("qualifiers", {}) or {}

        # Style modifiers for negation / hedging
        subj_style = "text-decoration:line-through;color:#ff2975;" if negated else "color:#00fcd6;"
        pred_style = "font-style:italic;border-bottom:1px dashed #a1a1aa;" if hedged else ""

        # Build qualifier chips
        qual_chips: list[str] = []
        for qkey in ["time", "location", "condition", "manner", "source_attribution"]:
            val = qualifiers.get(qkey)
            if val:
                safe_val = html.escape(str(val))[:50]
                qual_chips.append(
                    f'<span class="qual-chip qual-{qkey}" title="{qkey}: {safe_val}">'
                    f"{qkey[:3]}</span>"
                )
        qual_html = " ".join(qual_chips) if qual_chips else '<span style="color:#52525b;">--</span>'

        # Indicators
        indicators: list[str] = []
        if negated:
            indicators.append('<span style="color:#ff2975;font-weight:700;">NEG</span>')
        if hedged:
            indicators.append('<span style="color:#fbbf24;font-style:italic;">HEDGE</span>')
        indicator_html = " ".join(indicators) if indicators else ""

        conf_color = "#00fcd6" if confidence >= 0.8 else "#f59e0b" if confidence >= 0.5 else "#ff2975"

        rows.append(
            "<tr>"
            f'<td class="assert-subject" style="{subj_style}">{subject}</td>'
            f'<td class="assert-predicate" style="{pred_style}">{predicate}</td>'
            f'<td class="assert-object">{obj}</td>'
            f'<td class="assert-quals">{qual_html}</td>'
            f'<td class="assert-indicators">{indicator_html}</td>'
            f'<td class="assert-conf" style="color:{conf_color};">{confidence:.2f}</td>'
            "</tr>"
        )

    table_html = f"""
    <style>
    table.assert-table {{
        width: 100%;
        border-collapse: collapse;
        font-family: "Rajdhani", sans-serif;
        font-size: 0.82rem;
        line-height: 1.6;
    }}
    table.assert-table th {{
        font-family: "Rajdhani", sans-serif;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        font-size: 0.72rem;
        color: #a1a1aa;
        padding: 0.4rem 0.5rem;
        border-bottom: 2px solid #27272a;
        text-align: left;
    }}
    table.assert-table td {{
        padding: 0.3rem 0.5rem;
        border-bottom: 1px solid #1e1e24;
        vertical-align: middle;
        color: #e4e4e7;
    }}
    table.assert-table tr:hover {{
        background: rgba(0,252,214,0.04);
    }}
    td.assert-subject {{
        font-weight: 600;
        white-space: nowrap;
        max-width: 20%;
        overflow: hidden;
        text-overflow: ellipsis;
    }}
    td.assert-predicate {{
        color: #c026d3;
        font-weight: 600;
        font-family: "Space Mono", monospace;
        font-size: 0.75rem;
    }}
    td.assert-object {{
        max-width: 20%;
        overflow: hidden;
        text-overflow: ellipsis;
    }}
    td.assert-quals {{
        white-space: nowrap;
    }}
    td.assert-indicators {{
        white-space: nowrap;
        font-size: 0.7rem;
    }}
    td.assert-conf {{
        font-family: "Space Mono", monospace;
        font-size: 0.75rem;
        text-align: right;
    }}
    .qual-chip {{
        display: inline-block;
        padding: 0.05em 0.35em;
        border-radius: 3px;
        font-size: 0.65rem;
        font-weight: 600;
        text-transform: uppercase;
        margin-right: 2px;
        cursor: default;
    }}
    .qual-time {{ background: rgba(99,102,241,0.2); color: #6366f1; border: 1px solid #6366f144; }}
    .qual-location {{ background: rgba(245,158,11,0.2); color: #f59e0b; border: 1px solid #f59e0b44; }}
    .qual-condition {{ background: rgba(192,38,211,0.2); color: #c026d3; border: 1px solid #c026d344; }}
    .qual-manner {{ background: rgba(34,197,94,0.2); color: #22c55e; border: 1px solid #22c55e44; }}
    .qual-source_attribution {{ background: rgba(0,252,214,0.2); color: #00fcd6; border: 1px solid #00fcd644; }}
    </style>
    <table class="assert-table">
    <thead>
    <tr>
        <th>Subject</th>
        <th>Predicate</th>
        <th>Object</th>
        <th>Qualifiers</th>
        <th>Flags</th>
        <th style="text-align:right;">Conf</th>
    </tr>
    </thead>
    <tbody>
    {"".join(rows)}
    </tbody>
    </table>
    """

    st.markdown(table_html, unsafe_allow_html=True)
