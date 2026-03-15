"""Prompt Catalog — Browse and test registered LLM prompts."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from data_explorer.streamlit.components.model_selector import chat_model_selector
from data_explorer.streamlit.llm_client import get_llm_client
from data_explorer.streamlit.navigation import render_breadcrumbs
from data_explorer.streamlit.prompt_registry import PromptEntry, list_prompts
from data_explorer.streamlit.theme import apply_theme

st.set_page_config(
    page_title="Prompt Catalog", page_icon=":material/menu_book:", layout="wide"
)
apply_theme()
render_breadcrumbs([("Home", "app.py"), ("Prompt Catalog", None)])
st.header("Prompt Catalog")

# ------------------------------------------------------------------ #
# Load prompts
# ------------------------------------------------------------------ #

prompts = list_prompts()

if not prompts:
    st.info(
        "No prompts found. Set the `PROMPT_REGISTRY_DIR` environment variable "
        "to the directory containing `.prompt` files."
    )
    st.stop()

# ------------------------------------------------------------------ #
# Inventory table
# ------------------------------------------------------------------ #

st.subheader("Prompt Inventory")

table_data = [
    {
        "ID": p.prompt_id,
        "Domain": p.domain,
        "Task": p.task,
        "Model": p.model,
        "Temp": p.temperature,
        "Description": p.description,
    }
    for p in prompts
]

df = pd.DataFrame(table_data)
st.dataframe(df, use_container_width=True, hide_index=True)

st.divider()

# ------------------------------------------------------------------ #
# Detail panel
# ------------------------------------------------------------------ #

st.subheader("Prompt Detail")

prompt_ids = [p.prompt_id for p in prompts]
prompt_map: dict[str, PromptEntry] = {p.prompt_id: p for p in prompts}

selected_id = st.selectbox("Select prompt", prompt_ids, key="prompt_detail_select")
selected = prompt_map[selected_id]

col_meta, col_content = st.columns([1, 2])

with col_meta:
    st.markdown(f"**Domain:** `{selected.domain}`")
    st.markdown(f"**Task:** `{selected.task}`")
    st.markdown(f"**Model:** `{selected.model}`")
    st.markdown(f"**Temperature:** `{selected.temperature}`")
    st.markdown(f"**Max Tokens:** `{selected.max_tokens}`")

    if selected.used_by:
        st.markdown("**Used by:**")
        for ref in selected.used_by:
            st.markdown(f"- `{ref}`")

with col_content:
    st.markdown("**System Prompt:**")
    st.code(selected.system_content, language="text")

st.divider()

# ------------------------------------------------------------------ #
# Test playground
# ------------------------------------------------------------------ #

st.subheader("Test Playground")

with st.sidebar:
    st.subheader("Playground Settings")
    playground_model = chat_model_selector(key="playground_model")
    playground_temp = st.slider(
        "Temperature", 0.0, 1.0, selected.temperature, step=0.05, key="playground_temp"
    )

user_input = st.text_area(
    "User message",
    placeholder="Enter text to test the prompt with...",
    height=150,
    key="playground_input",
)

if st.button("Run", type="primary", disabled=not user_input):
    messages = [
        {"role": "system", "content": selected.system_content},
        {"role": "user", "content": user_input},
    ]

    with st.chat_message("assistant"):
        response_text = st.write_stream(
            get_llm_client().stream_chat(
                messages,
                model=playground_model,
                temperature=playground_temp,
            )
        )
