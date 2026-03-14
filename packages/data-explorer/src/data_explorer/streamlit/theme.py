"""Catalyst brand theme — CSS injection for Streamlit."""

from __future__ import annotations

import streamlit as st

# Catalyst dark palette (from catalyst-ui/lib/contexts/Theme/styles/catalyst.css)
CATALYST_CSS = """
<style>
/* ===== Google Fonts ===== */
@import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@400;500;600;700;800;900&family=Rajdhani:wght@300;400;500;600;700&family=Space+Mono:wght@400;700&display=swap');

/* ===== Catalyst Brand Tokens ===== */
:root {
    --cat-bg:         #0a0a0f;
    --cat-fg:         #e4e4e7;
    --cat-card:       #16161d;
    --cat-primary:    #00fcd6;
    --cat-secondary:  #c026d3;
    --cat-muted:      #27272a;
    --cat-muted-fg:   #a1a1aa;
    --cat-accent:     #1e1e24;
    --cat-destructive:#ff2975;
    --cat-border:     #27272a;
    --cat-neon-cyan:  #00fcd6;
    --cat-neon-pink:  #ff6ec7;
    --cat-neon-purple:#c026d3;
    --cat-neon-blue:  #00d4ff;
    --cat-neon-red:   #ff2975;
    --cat-neon-yellow:#fbbf24;

    --glow-primary:   0 0 15px rgba(0,252,214,.4), 0 0 30px rgba(0,252,214,.2);
    --glow-secondary: 0 0 15px rgba(192,38,211,.35), 0 0 30px rgba(192,38,211,.15);
    --shadow-neon-sm: 0 2px 10px rgba(0,0,0,.3), 0 0 10px rgba(0,252,214,.15);
    --shadow-neon-md: 0 4px 20px rgba(0,0,0,.4), 0 0 20px rgba(0,252,214,.2), 0 0 40px rgba(192,38,211,.1);
}

/* ===== Global Typography ===== */
html, body,
.stApp, .stApp p, .stApp span, .stApp div, .stApp label, .stApp li,
[data-testid="stSidebar"], [data-testid="stSidebar"] p,
[data-testid="stSidebar"] span, [data-testid="stSidebar"] label {
    font-family: "Rajdhani", "Inter", ui-sans-serif, system-ui, sans-serif !important;
}

h1, h2, h3, h4, h5, h6,
[data-testid="stHeading"] h1,
[data-testid="stHeading"] h2,
[data-testid="stHeading"] h3 {
    font-family: "Orbitron", "Arial Black", ui-sans-serif, system-ui, sans-serif !important;
    letter-spacing: 0.05em;
    text-transform: uppercase;
}

code, pre, [data-testid="stCode"] *,
.stCodeBlock, .stDataFrame th {
    font-family: "Space Mono", ui-monospace, monospace !important;
}

/* ===== Fix: Restore Material Symbols for Streamlit icon ligatures ===== */
/* Expander toggle icons */
[data-testid="stExpander"] summary > div > div:first-child,
[data-testid="stExpander"] summary > span > span:first-child,
/* Sidebar collapse/expand button */
[data-testid="collapsedControl"] span,
[data-testid="stSidebarCollapsedControl"] span,
button[kind="headerNoPadding"] span,
[data-testid="stBaseButton-headerNoPadding"] span,
/* Broad catch: any element Streamlit renders as an icon */
[data-testid*="Icon"] span,
[data-testid*="icon"] span {
    font-family: "Material Symbols Rounded" !important;
    font-variation-settings: "FILL" 0, "wght" 400, "GRAD" 0, "opsz" 24 !important;
    -webkit-text-fill-color: initial !important;
    background: none !important;
    -webkit-background-clip: initial !important;
    background-clip: initial !important;
    text-transform: none !important;
    letter-spacing: normal !important;
}

/* ===== Header — neon gradient text ===== */
[data-testid="stHeading"] h1 {
    background: linear-gradient(135deg, var(--cat-neon-cyan), var(--cat-neon-purple), var(--cat-neon-cyan));
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    filter: drop-shadow(0 0 12px rgba(0,252,214,.4));
}

[data-testid="stHeading"] h2 {
    background: linear-gradient(135deg, var(--cat-neon-purple), var(--cat-neon-cyan));
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    filter: drop-shadow(0 0 8px rgba(192,38,211,.3));
}

/* ===== Sidebar ===== */
[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #0d0d14 0%, #0a0a0f 100%) !important;
    border-right: 1px solid var(--cat-border) !important;
    position: relative;
}

[data-testid="stSidebar"]::after {
    content: "";
    position: absolute;
    top: 0;
    right: 0;
    width: 1px;
    height: 100%;
    background: linear-gradient(180deg, var(--cat-neon-cyan), var(--cat-neon-purple), transparent);
    opacity: 0.5;
    pointer-events: none;
    z-index: 10;
}

[data-testid="stSidebar"] [data-testid="stHeading"] * {
    font-size: 0.85rem;
}

/* Hide deploy button for cleaner look */
[data-testid="stAppDeployButton"],
button[data-testid="stAppDeployButton"],
.stDeployButton,
header[data-testid="stHeader"] button:has(> div:only-child) {
    display: none !important;
}

/* Hide the top-right menu dots */
#MainMenu, button[kind="header"] {
    visibility: hidden;
}

/* ===== Main content area ===== */
.stApp {
    background: var(--cat-bg) !important;
}

/* Subtle scanline overlay */
.stApp::before {
    content: "";
    position: fixed;
    top: 0; left: 0; right: 0; bottom: 0;
    background: repeating-linear-gradient(
        0deg,
        transparent,
        transparent 2px,
        rgba(0,252,214,0.015) 2px,
        rgba(0,252,214,0.015) 4px
    );
    pointer-events: none;
    z-index: 999;
}

/* ===== Metrics ===== */
[data-testid="stMetric"] {
    background: var(--cat-card) !important;
    border: 1px solid var(--cat-border) !important;
    border-radius: 0.25rem !important;
    padding: 1rem !important;
    box-shadow: var(--shadow-neon-sm);
    transition: box-shadow 0.3s ease;
}

[data-testid="stMetric"]:hover {
    box-shadow: var(--shadow-neon-md);
    border-color: rgba(0,252,214,0.3) !important;
}

[data-testid="stMetricValue"] {
    color: var(--cat-primary) !important;
    font-family: "Orbitron", sans-serif !important;
    font-weight: 700 !important;
}

[data-testid="stMetricLabel"] {
    color: var(--cat-muted-fg) !important;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    font-size: 0.7rem !important;
}

/* ===== Containers / Cards ===== */
[data-testid="stExpander"] {
    background: var(--cat-card) !important;
    border: 1px solid var(--cat-border) !important;
    border-radius: 0.25rem !important;
    box-shadow: var(--shadow-neon-sm);
}

[data-testid="stExpander"]:hover {
    border-color: rgba(0,252,214,0.25) !important;
}

[data-testid="stExpander"] summary p,
[data-testid="stExpander"] summary span p {
    font-family: "Rajdhani", sans-serif !important;
    font-weight: 600 !important;
}

div[data-testid="stVerticalBlockBorderWrapper"]:has(> div[data-testid="stVerticalBlock"] > div.element-container) {
    border-color: var(--cat-border) !important;
    border-radius: 0.25rem !important;
}

/* ===== Buttons ===== */
.stButton > button {
    background: transparent !important;
    color: var(--cat-primary) !important;
    border: 1px solid var(--cat-primary) !important;
    border-radius: 0.25rem !important;
    font-family: "Rajdhani", sans-serif !important;
    font-weight: 600 !important;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    transition: all 0.3s ease !important;
}

.stButton > button:hover {
    background: rgba(0,252,214,0.1) !important;
    box-shadow: var(--glow-primary);
}

.stButton > button:active {
    background: rgba(0,252,214,0.2) !important;
}

/* Primary buttons */
.stButton > button[kind="primary"] {
    background: var(--cat-primary) !important;
    color: var(--cat-bg) !important;
}

.stButton > button[kind="primary"]:hover {
    box-shadow: var(--glow-primary);
}

/* ===== Selectbox / Inputs ===== */
[data-testid="stSelectbox"] > div > div,
.stSelectbox > div > div {
    background: var(--cat-accent) !important;
    border-color: var(--cat-border) !important;
    border-radius: 0.25rem !important;
    color: var(--cat-fg) !important;
}

[data-testid="stTextInput"] input,
.stTextInput input {
    background: var(--cat-accent) !important;
    border-color: var(--cat-border) !important;
    border-radius: 0.25rem !important;
    color: var(--cat-fg) !important;
    font-family: "Rajdhani", sans-serif !important;
}

[data-testid="stTextInput"] input:focus,
.stTextInput input:focus {
    border-color: var(--cat-primary) !important;
    box-shadow: 0 0 8px rgba(0,252,214,0.3) !important;
}

/* ===== Slider ===== */
[data-testid="stSlider"] [role="slider"] {
    background: var(--cat-primary) !important;
}

[data-testid="stSlider"] [data-testid="stThumbValue"] {
    color: var(--cat-primary) !important;
    font-family: "Space Mono", monospace !important;
}

/* ===== Dataframe / Table ===== */
[data-testid="stDataFrame"] {
    border: 1px solid var(--cat-border) !important;
    border-radius: 0.25rem !important;
}

/* ===== Tabs ===== */
.stTabs [data-baseweb="tab-list"] {
    gap: 1rem !important;
    border-bottom: 1px solid var(--cat-border);
}

.stTabs [data-baseweb="tab"] {
    font-family: "Rajdhani", sans-serif !important;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-size: 0.9rem;
    color: var(--cat-muted-fg) !important;
    border-bottom: 2px solid transparent;
    padding: 0.6rem 1rem !important;
    transition: all 0.2s ease;
}

.stTabs [data-baseweb="tab"]:hover {
    color: var(--cat-fg) !important;
}

.stTabs [aria-selected="true"] {
    color: var(--cat-primary) !important;
    border-bottom-color: var(--cat-primary) !important;
}

/* ===== Success / Warning / Error / Info alerts ===== */
[data-testid="stAlert"] {
    border-radius: 0.25rem !important;
    font-family: "Rajdhani", sans-serif !important;
}

div[data-testid="stAlert"][data-baseweb="notification"]:has(div[role="alert"]) {
    border-left: 3px solid var(--cat-primary);
}

/* ===== Divider ===== */
hr {
    border-color: var(--cat-border) !important;
    opacity: 0.5;
}

/* ===== Scrollbar ===== */
::-webkit-scrollbar {
    width: 6px;
    height: 6px;
}

::-webkit-scrollbar-track {
    background: var(--cat-bg);
}

::-webkit-scrollbar-thumb {
    background: var(--cat-muted);
    border-radius: 3px;
}

::-webkit-scrollbar-thumb:hover {
    background: var(--cat-muted-fg);
}

/* ===== Code blocks ===== */
[data-testid="stCode"],
.stCodeBlock {
    background: var(--cat-accent) !important;
    border: 1px solid var(--cat-border) !important;
    border-radius: 0.25rem !important;
}

/* ===== Captions ===== */
[data-testid="stCaptionContainer"] {
    color: var(--cat-muted-fg) !important;
    font-family: "Space Mono", monospace !important;
    font-size: 0.7rem !important;
    letter-spacing: 0.05em;
}

/* ===== Plotly chart frame ===== */
.stPlotlyChart {
    border: 1px solid var(--cat-border) !important;
    border-radius: 0.25rem !important;
    overflow: hidden;
}

/* ===== Text area ===== */
.stTextArea textarea {
    background: var(--cat-accent) !important;
    border-color: var(--cat-border) !important;
    color: var(--cat-fg) !important;
    font-family: "Space Mono", monospace !important;
    font-size: 0.8rem !important;
}

/* ===== Multiselect / Radio ===== */
[data-testid="stRadio"] label {
    font-family: "Rajdhani", sans-serif !important;
    font-weight: 500;
}

/* ===== Spinner ===== */
[data-testid="stSpinner"] {
    color: var(--cat-primary) !important;
}

/* ===== JSON viewer ===== */
[data-testid="stJson"] {
    background: var(--cat-accent) !important;
    border: 1px solid var(--cat-border) !important;
    border-radius: 0.25rem !important;
}

/* ===== Status widget (top-right) ===== */
[data-testid="stStatusWidget"] {
    background: var(--cat-card) !important;
    border: 1px solid var(--cat-border) !important;
}

/* ===== Page nav links ===== */
[data-testid="stSidebar"] ul li a,
[data-testid="stSidebarNav"] a {
    font-family: "Rajdhani", sans-serif !important;
    font-weight: 600 !important;
    letter-spacing: 0.04em;
    color: var(--cat-muted-fg) !important;
    transition: all 0.2s ease !important;
    border-left: 2px solid transparent !important;
    padding-left: 0.5rem !important;
}

[data-testid="stSidebar"] ul li a:hover,
[data-testid="stSidebarNav"] a:hover {
    color: var(--cat-primary) !important;
    background: rgba(0,252,214,0.05) !important;
    border-left-color: rgba(0,252,214,0.4) !important;
}

[data-testid="stSidebar"] ul li a[aria-current="page"],
[data-testid="stSidebarNav"] a[aria-current="page"] {
    color: var(--cat-primary) !important;
    background: rgba(0,252,214,0.08) !important;
    border-left: 2px solid var(--cat-primary) !important;
    font-weight: 700 !important;
}

/* Active page nav link p text */
[data-testid="stSidebar"] ul li a[aria-current="page"] p {
    color: var(--cat-primary) !important;
}
</style>
"""

# Plotly template matching catalyst dark theme
PLOTLY_TEMPLATE = {
    "layout": {
        "paper_bgcolor": "#0a0a0f",
        "plot_bgcolor": "#16161d",
        "font": {
            "family": "Rajdhani, sans-serif",
            "color": "#e4e4e7",
        },
        "title": {
            "font": {
                "family": "Orbitron, sans-serif",
                "color": "#00fcd6",
                "size": 16,
            }
        },
        "colorway": [
            "#00fcd6",  # neon cyan
            "#c026d3",  # neon purple
            "#ff6ec7",  # neon pink
            "#00d4ff",  # neon blue
            "#fbbf24",  # neon yellow
            "#ff2975",  # neon red
        ],
        "xaxis": {
            "gridcolor": "#27272a",
            "linecolor": "#27272a",
            "zerolinecolor": "#27272a",
            "tickfont": {"family": "Space Mono, monospace", "size": 10},
        },
        "yaxis": {
            "gridcolor": "#27272a",
            "linecolor": "#27272a",
            "zerolinecolor": "#27272a",
            "tickfont": {"family": "Space Mono, monospace", "size": 10},
        },
        "legend": {
            "bgcolor": "rgba(0,0,0,0)",
            "font": {"color": "#a1a1aa"},
        },
        "hoverlabel": {
            "bgcolor": "#16161d",
            "bordercolor": "#00fcd6",
            "font": {"family": "Rajdhani, sans-serif", "color": "#e4e4e7"},
        },
    }
}


def apply_theme() -> None:
    """Inject Catalyst brand CSS into the current Streamlit page."""
    st.markdown(CATALYST_CSS, unsafe_allow_html=True)


def get_plotly_template() -> dict:
    """Return a Plotly template dict matching the Catalyst dark theme."""
    return PLOTLY_TEMPLATE
