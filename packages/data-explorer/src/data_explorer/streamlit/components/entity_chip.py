"""Inline entity chip/badge component."""

from __future__ import annotations

import html

import streamlit as st

# Shared entity color palette — consistent across all components.
ENTITY_COLORS: dict[str, str] = {
    "PERSON": "#00fcd6",
    "ORG": "#c026d3",
    "GPE": "#f59e0b",
    "DATE": "#6366f1",
    "LAW": "#ef4444",
    "EVENT": "#22c55e",
}

_DEFAULT_COLOR = "#a1a1aa"


def _color_for_label(label: str) -> str:
    """Return the hex color for *label*, falling back to muted grey."""
    return ENTITY_COLORS.get(label.upper(), _DEFAULT_COLOR)


def _hex_to_rgba(hex_color: str, alpha: float) -> str:
    """Convert ``#rrggbb`` to ``rgba(r, g, b, alpha)``."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def render_entity_chip(
    text: str,
    label: str,
    count: int | None = None,
    key_suffix: str = "",
) -> bool:
    """Render an inline styled entity chip and return ``True`` if clicked.

    The chip is implemented as a Streamlit ``st.button`` with custom CSS
    applied via a preceding ``st.markdown`` block so it visually matches the
    Catalyst brand palette.

    Parameters
    ----------
    text:
        The entity surface text (e.g. ``"Jeffrey Epstein"``).
    label:
        The NER label (e.g. ``"PERSON"``).  Used to select the colour.
    count:
        Optional occurrence count shown as a small superscript badge.
    key_suffix:
        Extra string appended to the widget key to guarantee uniqueness when
        many chips are rendered in the same page.
    """
    color = _color_for_label(label)
    bg = _hex_to_rgba(color, 0.15)

    display_text = text
    if count is not None:
        display_text = f"{text}  ({count})"

    # Inject per-chip CSS that targets the next button via a wrapper class.
    chip_id = f"chip-{label}-{text}-{key_suffix}".replace(" ", "-")
    chip_css = f"""
    <style>
    div[data-testid="stHorizontalBlock"]:has(button[key="{chip_id}"]) button,
    div.entity-chip-{chip_id} + div .stButton > button {{
        background: {bg} !important;
        color: {color} !important;
        border: 1px solid {color} !important;
        border-radius: 999px !important;
        font-family: "Rajdhani", sans-serif !important;
        font-size: 0.8rem !important;
        font-weight: 600 !important;
        padding: 0.15rem 0.65rem !important;
        text-transform: none !important;
        letter-spacing: normal !important;
        line-height: 1.4 !important;
        min-height: 0 !important;
    }}
    </style>
    <div class="entity-chip-{chip_id}" style="display:none"></div>
    """
    st.markdown(chip_css, unsafe_allow_html=True)
    return st.button(display_text, key=chip_id)


def render_entity_chip_list(
    entities: list[dict],
    max_display: int = 20,
    columns: int = 4,
) -> str | None:
    """Render a grid of entity chips and return the clicked entity text (or ``None``).

    Parameters
    ----------
    entities:
        List of dicts, each with at minimum ``text`` and ``label`` keys.
        An optional ``count`` key is forwarded to :func:`render_entity_chip`.
    max_display:
        Maximum number of chips shown before the rest are hidden behind a
        *Show more* expander.
    columns:
        Number of ``st.columns`` used for the grid layout.

    Returns
    -------
    str | None
        The ``text`` value of the chip that was clicked, or ``None`` if
        nothing was clicked.
    """
    clicked: str | None = None

    visible = entities[:max_display]
    overflow = entities[max_display:]

    def _render_batch(batch: list[dict], offset: int = 0) -> None:
        nonlocal clicked
        for idx in range(0, len(batch), columns):
            row = batch[idx : idx + columns]
            cols = st.columns(len(row))
            for col, entity in zip(cols, row):
                with col:
                    was_clicked = render_entity_chip(
                        text=entity["text"],
                        label=entity["label"],
                        count=entity.get("count"),
                        key_suffix=f"list-{offset + idx + row.index(entity)}",
                    )
                    if was_clicked:
                        clicked = entity["text"]

    _render_batch(visible)

    if overflow:
        with st.expander(f"Show {len(overflow)} more entities"):
            _render_batch(overflow, offset=max_display)

    return clicked


def render_entity_chip_html(
    text: str,
    label: str,
    count: int | None = None,
) -> str:
    """Return a raw HTML ``<span>`` for an entity chip.

    This is intended for embedding inside other HTML contexts (e.g. the
    concordance view table) where Streamlit widgets cannot be used.

    The ``text`` content is HTML-escaped to prevent injection.
    """
    color = _color_for_label(label)
    bg = _hex_to_rgba(color, 0.15)
    safe_text = html.escape(text)

    count_badge = ""
    if count is not None:
        count_badge = (
            f'<sup style="margin-left:3px;font-size:0.7em;'
            f'opacity:0.8;">{count}</sup>'
        )

    return (
        f'<span style="'
        f"display:inline-block;"
        f"background:{bg};"
        f"color:{color};"
        f"border:1px solid {color};"
        f"border-radius:999px;"
        f"padding:0.1em 0.55em;"
        f"font-family:'Rajdhani',sans-serif;"
        f"font-size:0.8rem;"
        f"font-weight:600;"
        f"line-height:1.4;"
        f"white-space:nowrap;"
        f'">{safe_text}{count_badge}</span>'
    )
