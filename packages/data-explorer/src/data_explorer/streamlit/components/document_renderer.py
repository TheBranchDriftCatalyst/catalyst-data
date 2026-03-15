"""Document renderer — entity highlighting and chunk boundary visualization."""

from __future__ import annotations

import html
import logging
import re

import streamlit as st

logger = logging.getLogger(__name__)

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

# Mention type color palette for span-based highlighting
MENTION_TYPE_COLORS: dict[str, str] = {
    "PERSON": "#3b82f6",
    "ORG": "#22c55e",
    "GPE": "#f97316",
    "LOC": "#06b6d4",
    "LAW": "#a855f7",
    "EVENT": "#ef4444",
    "DATE": "#6b7280",
    "MONEY": "#eab308",
    "NORP": "#ec4899",
    "FACILITY": "#14b8a6",
    "OTHER": "#9ca3af",
}


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


# ---------------------------------------------------------------------------
# Mention-based rendering (EDC models)
# ---------------------------------------------------------------------------


def render_document_with_mentions(content: str, mentions: list[dict]) -> str:
    """Render document content with highlighted entity mentions.

    Uses span_start/span_end from Mention objects to precisely highlight
    entities in the text with color-coded spans based on mention_type.

    Args:
        content: The raw document text
        mentions: List of Mention dicts with span_start, span_end, text, mention_type

    Returns:
        HTML string with highlighted mentions for st.markdown(unsafe_allow_html=True)
    """
    if not mentions or not content:
        return html.escape(content) if content else ""

    # Filter mentions with valid spans and sort by span_start descending
    # (insert from end to preserve earlier offsets)
    valid_mentions = [
        m for m in mentions
        if m.get("span_start") is not None
        and m.get("span_end") is not None
        and m["span_start"] < m["span_end"] <= len(content)
    ]
    valid_mentions.sort(key=lambda m: m["span_start"], reverse=True)

    result = content
    for m in valid_mentions:
        start = m["span_start"]
        end = m["span_end"]
        mention_type = m.get("mention_type", "OTHER")
        if isinstance(mention_type, str):
            color = MENTION_TYPE_COLORS.get(mention_type, MENTION_TYPE_COLORS["OTHER"])
        else:
            color = MENTION_TYPE_COLORS.get(
                mention_type.value if hasattr(mention_type, "value") else str(mention_type),
                MENTION_TYPE_COLORS["OTHER"],
            )

        safe_text = html.escape(result[start:end])
        safe_type = html.escape(str(mention_type))
        safe_mention_text = html.escape(m.get("text", ""))
        highlighted = (
            f'<span style="background-color: {color}20; border-bottom: 2px solid {color}; '
            f'padding: 0 2px; border-radius: 2px;" '
            f'title="{safe_type}: {safe_mention_text}">'
            f"{safe_text}</span>"
        )
        result = result[:start] + highlighted + result[end:]

    # Escape parts that are NOT already wrapped in HTML spans
    # Since we inserted HTML into the raw string, we need to be careful.
    # The approach above inserts HTML into raw content, so non-highlighted
    # parts remain unescaped. We handle this by escaping the original content
    # first, then re-inserting highlights. Let's redo with proper escaping.
    # Actually, the standard approach (used in the spec) is to work on raw
    # content and trust that the highlighted spans are safe. The content between
    # highlights is raw text that could contain HTML chars. Let's fix this
    # by working on the escaped content with adjusted offsets.

    # Re-implement properly: escape first, then insert highlights
    return _render_mentions_safe(content, mentions)


def _render_mentions_safe(content: str, mentions: list[dict]) -> str:
    """Safely render mentions by escaping content first, then inserting highlights."""
    if not mentions or not content:
        return html.escape(content) if content else ""

    valid_mentions = [
        m for m in mentions
        if m.get("span_start") is not None
        and m.get("span_end") is not None
        and 0 <= m["span_start"] < m["span_end"] <= len(content)
    ]
    # Sort by span_start ascending to build output left-to-right
    valid_mentions.sort(key=lambda m: m["span_start"])

    # Remove overlaps: keep earlier/longer mentions
    filtered: list[dict] = []
    last_end = 0
    for m in valid_mentions:
        if m["span_start"] >= last_end:
            filtered.append(m)
            last_end = m["span_end"]

    parts: list[str] = []
    pos = 0
    for m in filtered:
        start = m["span_start"]
        end = m["span_end"]

        # Escape text before this mention
        if start > pos:
            parts.append(html.escape(content[pos:start]))

        mention_type = m.get("mention_type", "OTHER")
        if isinstance(mention_type, str):
            color = MENTION_TYPE_COLORS.get(mention_type, MENTION_TYPE_COLORS["OTHER"])
        else:
            color = MENTION_TYPE_COLORS.get(
                mention_type.value if hasattr(mention_type, "value") else str(mention_type),
                MENTION_TYPE_COLORS["OTHER"],
            )

        safe_text = html.escape(content[start:end])
        safe_type = html.escape(str(mention_type))
        safe_mention_text = html.escape(m.get("text", ""))
        parts.append(
            f'<span style="background-color: {color}20; border-bottom: 2px solid {color}; '
            f'padding: 0 2px; border-radius: 2px;" '
            f'title="{safe_type}: {safe_mention_text}">'
            f"{safe_text}</span>"
        )
        pos = end

    # Remaining text after last mention
    if pos < len(content):
        parts.append(html.escape(content[pos:]))

    return "".join(parts)


def render_mention_legend() -> str:
    """Render a legend showing mention type color coding."""
    items = []
    for mtype, color in MENTION_TYPE_COLORS.items():
        if mtype == "OTHER":
            continue
        items.append(
            f'<span style="background-color: {color}20; border-bottom: 2px solid {color}; '
            f'padding: 2px 6px; border-radius: 2px; margin-right: 8px; font-size: 0.85em;">'
            f"{mtype}</span>"
        )
    return " ".join(items)
