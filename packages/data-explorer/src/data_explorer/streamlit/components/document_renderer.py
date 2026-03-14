"""Document renderer — entity highlighting and chunk boundary visualization."""

from __future__ import annotations

import html
import re

import streamlit as st

# Entity color palette — semi-transparent backgrounds for dark theme
ENTITY_COLORS: dict[str, dict[str, str]] = {
    "PERSON": {"bg": "rgba(0,252,214,0.2)", "border": "#00fcd6"},
    "ORG":    {"bg": "rgba(192,38,211,0.2)", "border": "#c026d3"},
    "GPE":    {"bg": "rgba(245,158,11,0.2)", "border": "#f59e0b"},
    "DATE":   {"bg": "rgba(99,102,241,0.2)", "border": "#6366f1"},
    "LAW":    {"bg": "rgba(239,68,68,0.2)", "border": "#ef4444"},
    "EVENT":  {"bg": "rgba(34,197,94,0.2)", "border": "#22c55e"},
}

_DEFAULT_COLORS = {"bg": "rgba(161,161,170,0.2)", "border": "#a1a1aa"}


def _entity_span(escaped_text: str, label: str) -> str:
    """Build an inline HTML span for a highlighted entity."""
    colors = ENTITY_COLORS.get(label, _DEFAULT_COLORS)
    return (
        f'<span title="{html.escape(label)}" style="'
        f"background:{colors['bg']};"
        f"border:1px solid {colors['border']};"
        "border-radius:3px;"
        "padding:1px 4px;"
        "font-weight:600;"
        f"color:{colors['border']};"
        f'">{escaped_text}</span>'
    )


def _annotate_text(escaped_text: str, entities: list[dict]) -> str:
    """Find entity mentions in escaped text and wrap them with highlight spans.

    Uses longest-match-first to handle overlapping entities.
    """
    if not entities:
        return escaped_text

    # Deduplicate entity texts and sort by length descending (longest match first)
    seen: set[tuple[str, str]] = set()
    unique_entities: list[dict] = []
    for e in entities:
        key = (e.get("text", ""), e.get("label", ""))
        if key not in seen and key[0]:
            seen.add(key)
            unique_entities.append(e)
    unique_entities.sort(key=lambda e: len(e.get("text", "")), reverse=True)

    # Find all match positions
    replacements: list[tuple[int, int, str, str]] = []
    for e in unique_entities:
        raw_text = e.get("text", "")
        label = e.get("label", "")
        if not raw_text:
            continue
        pattern = re.escape(html.escape(raw_text))
        for m in re.finditer(pattern, escaped_text, re.IGNORECASE):
            replacements.append((m.start(), m.end(), m.group(), label))

    if not replacements:
        return escaped_text

    # Sort by start position, then longest match first for overlaps
    replacements.sort(key=lambda r: (r[0], -(r[1] - r[0])))

    # Remove overlapping matches (keep first/longest)
    filtered: list[tuple[int, int, str, str]] = []
    last_end = 0
    for start, end, matched, label in replacements:
        if start >= last_end:
            filtered.append((start, end, matched, label))
            last_end = end

    # Apply replacements right-to-left to preserve positions
    result = escaped_text
    for start, end, matched, label in reversed(filtered):
        result = result[:start] + _entity_span(matched, label) + result[end:]

    return result


def _chunk_boundary_html(index: int, total: int) -> str:
    """Render a styled chunk boundary divider."""
    return (
        '<div style="'
        "display:flex;align-items:center;gap:0.5rem;"
        "margin:0.75rem 0;padding:0.25rem 0;"
        '">'
        '<div style="flex:1;height:1px;'
        "background:linear-gradient(90deg,transparent,#27272a,#00fcd6,#27272a,transparent);"
        '"></div>'
        '<span style="'
        "font-family:'Space Mono',monospace;"
        "font-size:0.65rem;"
        "color:#a1a1aa;"
        "letter-spacing:0.08em;"
        "text-transform:uppercase;"
        "white-space:nowrap;"
        f'">Chunk {index + 1} of {total}</span>'
        '<div style="flex:1;height:1px;'
        "background:linear-gradient(90deg,transparent,#27272a,#c026d3,#27272a,transparent);"
        '"></div>'
        "</div>"
    )


def render_document(
    text: str,
    entities: list[dict] | None = None,
    chunks: list[dict] | None = None,
    show_chunk_boundaries: bool = True,
) -> None:
    """Render document text with entity highlights and chunk boundaries."""
    container_style = (
        "font-family:'Space Mono',monospace;"
        "font-size:0.82rem;"
        "line-height:1.7;"
        "color:#e4e4e7;"
        "background:#16161d;"
        "border:1px solid #27272a;"
        "border-radius:0.25rem;"
        "padding:1rem 1.25rem;"
        "white-space:pre-wrap;"
        "word-wrap:break-word;"
    )

    if chunks and show_chunk_boundaries:
        sorted_chunks = sorted(chunks, key=lambda c: c.get("index", 0))
        total = len(sorted_chunks)
        parts: list[str] = []

        for chunk in sorted_chunks:
            chunk_text = html.escape(chunk.get("text", ""))
            # Filter entities belonging to this chunk
            chunk_entities = []
            if entities:
                chunk_id = chunk.get("chunk_id", "")
                chunk_entities = [
                    e for e in entities
                    if e.get("chunk_id") == chunk_id
                ] if chunk_id else entities

            annotated = _annotate_text(chunk_text, chunk_entities)
            idx = chunk.get("index", 0)
            parts.append(_chunk_boundary_html(idx, total))
            parts.append(f'<div style="{container_style}">{annotated}</div>')

        st.markdown("\n".join(parts), unsafe_allow_html=True)
    else:
        escaped = html.escape(text)
        annotated = _annotate_text(escaped, entities or [])
        st.markdown(f'<div style="{container_style}">{annotated}</div>', unsafe_allow_html=True)


def render_entity_legend() -> None:
    """Render a horizontal legend of entity label colors."""
    items = []
    for label, colors in ENTITY_COLORS.items():
        items.append(
            f'<span style="'
            f"display:inline-flex;align-items:center;gap:0.3rem;"
            f"margin-right:1rem;"
            f"font-family:'Rajdhani',sans-serif;"
            f"font-size:0.8rem;"
            f"color:{colors['border']};"
            f'">'
            f'<span style="'
            f"display:inline-block;width:10px;height:10px;"
            f"background:{colors['bg']};"
            f"border:1px solid {colors['border']};"
            f"border-radius:2px;"
            f'"></span>'
            f"{label}</span>"
        )
    st.markdown(
        '<div style="display:flex;flex-wrap:wrap;padding:0.5rem 0;">'
        + "".join(items)
        + "</div>",
        unsafe_allow_html=True,
    )
