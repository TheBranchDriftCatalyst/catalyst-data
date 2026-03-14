"""Cross-page navigation helpers and session state management."""

from __future__ import annotations

import streamlit as st

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PAGE_LABELS: dict[str, str] = {
    "pages/1_Asset_Browser.py": "Asset Browser",
    "pages/2_Document_Explorer.py": "Document Explorer",
    "pages/3_Knowledge_Graph.py": "Knowledge Graph",
    "pages/4_Document_Lens.py": "Document Lens",
    "pages/4_Media_Player.py": "Media Player",
    "pages/5_Semantic_Explorer.py": "Semantic Explorer",
    "pages/6_Cross_Source_Linker.py": "Cross-Source Linker",
    "pages/7_Data_Chat.py": "Data Chat",
    "pages/8_Entity_Concordance.py": "Entity Concordance",
    "pages/9_Config_Comparison.py": "Config Comparison",
}

ENTITY_COLORS: dict[str, str] = {
    "PERSON": "#00fcd6",
    "ORG": "#c026d3",
    "GPE": "#f59e0b",
    "DATE": "#6366f1",
    "LAW": "#ef4444",
    "EVENT": "#22c55e",
}

# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------


def navigate_to(page: str, **params: object) -> None:
    """Navigate to *page* (e.g. ``"pages/3_Knowledge_Graph.py"``) with optional
    context parameters that the target page can consume via
    :func:`get_nav_params`.
    """
    st.session_state["nav_target"] = page
    st.session_state["nav_params"] = params
    st.switch_page(page)


def get_nav_params() -> dict:
    """Return and clear any parameters set by a prior :func:`navigate_to` call.

    Parameters are consumed on first read so they do not persist across
    subsequent reruns of the target page.
    """
    params: dict = st.session_state.get("nav_params", {})
    st.session_state["nav_params"] = {}
    return params


# ---------------------------------------------------------------------------
# Breadcrumbs
# ---------------------------------------------------------------------------


def render_breadcrumbs(crumbs: list[tuple[str, str | None]]) -> None:
    """Render a horizontal breadcrumb trail.

    Parameters
    ----------
    crumbs:
        A list of ``(label, page_path_or_None)`` tuples.  The last entry is
        treated as the current page (highlighted, no link).
    """
    parts: list[str] = []
    for idx, (label, page) in enumerate(crumbs):
        is_last = idx == len(crumbs) - 1
        if is_last or page is None:
            parts.append(
                f'<span style="color:#00fcd6;font-weight:600;">{label}</span>'
            )
        else:
            parts.append(
                f'<span style="color:#a1a1aa;">{label}</span>'
            )

    separator = '<span style="color:#a1a1aa;margin:0 0.4em;">&#47;</span>'
    html = (
        '<div style="'
        "font-family:'Rajdhani',sans-serif;"
        "font-size:0.85rem;"
        "letter-spacing:0.04em;"
        "padding:0.25rem 0 0.5rem 0;"
        '">'
        f"{separator.join(parts)}"
        "</div>"
    )
    st.markdown(html, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Entity link chip
# ---------------------------------------------------------------------------


def render_entity_link(
    text: str,
    label: str,
    entity_id: str | None = None,
) -> None:
    """Render a clickable entity chip that navigates to Entity Concordance."""
    color = ENTITY_COLORS.get(label, "#a1a1aa")
    key = f"entity_link__{text}__{label}"

    if st.button(f"{label}: {text}", key=key):
        navigate_to(
            "pages/8_Entity_Concordance.py",
            entity_text=text,
            entity_label=label,
            entity_id=entity_id,
        )

    st.markdown(
        f"""<style>
        div[data-testid="stButton"]:has(button[key="{key}"]) button {{
            color: {color} !important;
            border-color: {color} !important;
        }}
        div[data-testid="stButton"]:has(button[key="{key}"]) button:hover {{
            background: {color}1a !important;
            box-shadow: 0 0 15px {color}66, 0 0 30px {color}33 !important;
        }}
        </style>""",
        unsafe_allow_html=True,
    )
