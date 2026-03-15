"""Data Chat — RAG-powered Q&A chat over the corpus."""

from __future__ import annotations

import streamlit as st

from data_explorer.streamlit.config import get_s3_config
from data_explorer.streamlit.data_client import DataClient
from data_explorer.streamlit.llm_client import (
    LLMClient,
    build_rag_context,
    get_llm_client,
)
from data_explorer.streamlit.components.model_selector import chat_model_selector
from data_explorer.streamlit.navigation import render_breadcrumbs
from data_explorer.streamlit.prompt_registry import get_prompt
from data_explorer.streamlit.theme import apply_theme

st.set_page_config(page_title="Data Chat", page_icon=":material/chat:", layout="wide")
apply_theme()
render_breadcrumbs([("Home", "app.py"), ("Data Chat", None)])
st.header("Data Chat")


# ------------------------------------------------------------------ #
# Cached helpers
# ------------------------------------------------------------------ #


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
def _find_embedding_assets() -> list[dict]:
    client = _get_client()
    assets = client.list_assets()
    return [a for a in assets if "embedding" in a["asset"].lower() and a["layer"] == "gold"]


# ------------------------------------------------------------------ #
# Embedding assets
# ------------------------------------------------------------------ #

embedding_assets = _find_embedding_assets()

if not embedding_assets:
    st.info("No embedding assets found (looking for gold/*embedding*).")
    st.stop()

# ------------------------------------------------------------------ #
# Sidebar — settings
# ------------------------------------------------------------------ #

with st.sidebar:
    st.subheader("Chat Settings")

    asset_options = {f"{a['code_location']}/{a['asset']}": a for a in embedding_assets}
    selected_key = st.selectbox("Embedding source", list(asset_options.keys()))
    selected_asset = asset_options[selected_key]

    model = chat_model_selector(key="data_chat_model")

    temperature = st.slider("Temperature", 0.0, 1.0, 0.3, step=0.05)

    max_chunks = st.slider("Max context chunks", 3, 20, 8, step=1)

# ------------------------------------------------------------------ #
# Initialise chat history
# ------------------------------------------------------------------ #

if "chat_messages" not in st.session_state:
    st.session_state["chat_messages"] = []

# ------------------------------------------------------------------ #
# Render existing chat history
# ------------------------------------------------------------------ #

for msg in st.session_state["chat_messages"]:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        # Re-render sources expander for assistant messages
        if msg["role"] == "assistant" and msg.get("sources"):
            with st.expander("Sources"):
                for src in msg["sources"]:
                    score = src.get("score", 0)
                    doc_id = src.get("document_id", "unknown")
                    preview = src.get("text_preview", "")
                    st.markdown(
                        f"**{doc_id}** &mdash; similarity: `{score:.4f}`"
                    )
                    if preview:
                        st.caption(preview)
                    st.divider()

# ------------------------------------------------------------------ #
# Chat input
# ------------------------------------------------------------------ #

query = st.chat_input("Ask a question about the corpus...")

if query:
    # Show user message
    st.session_state["chat_messages"].append({"role": "user", "content": query})
    with st.chat_message("user"):
        st.markdown(query)

    # --- Retrieve relevant chunks --- #
    llm_client = get_llm_client()

    with st.spinner("Embedding query..."):
        try:
            query_vec = llm_client.embed(query)
        except Exception as exc:
            st.error(f"Embedding failed: {exc}")
            st.stop()

    with st.spinner("Searching for relevant chunks..."):
        results = _get_client().search_embeddings(
            query_vec, selected_asset["root"], top_k=max_chunks
        )

    if not results:
        with st.chat_message("assistant"):
            no_results_msg = "I could not find any relevant chunks in the selected embedding source to answer your question."
            st.markdown(no_results_msg)
        st.session_state["chat_messages"].append(
            {"role": "assistant", "content": no_results_msg, "sources": []}
        )
        st.stop()

    # --- Build RAG context and stream response --- #
    rag_context = build_rag_context(results)

    _DEFAULT_RAG_SYSTEM_PROMPT = (
        "You are a research assistant. Answer the question based ONLY on the "
        "provided context. If the context doesn't contain enough information, "
        "say so. Cite sources using [Source: document_id] format."
    )
    rag_prompt = get_prompt("rag/research-assistant")
    system_prompt = rag_prompt.system_content if rag_prompt else _DEFAULT_RAG_SYSTEM_PROMPT

    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": f"Context:\n{rag_context}\n\nQuestion: {query}",
        },
    ]

    with st.chat_message("assistant"):
        response_text = st.write_stream(
            llm_client.stream_chat(
                messages,
                model=model,
                temperature=temperature,
            )
        )

        # Build source metadata for display and persistence
        source_records = []
        for r in results:
            text_val = r.get("text") or r.get("content") or r.get("chunk_text", "")
            preview = str(text_val)[:200] + "..." if len(str(text_val)) > 200 else str(text_val)
            source_records.append({
                "document_id": r.get("document_id") or r.get("id", "unknown"),
                "score": r.get("score", 0),
                "text_preview": preview,
            })

        # Show sources expander
        with st.expander("Sources"):
            for src in source_records:
                st.markdown(
                    f"**{src['document_id']}** &mdash; similarity: `{src['score']:.4f}`"
                )
                if src["text_preview"]:
                    st.caption(src["text_preview"])
                st.divider()

    # Persist to session state
    st.session_state["chat_messages"].append({
        "role": "assistant",
        "content": response_text,
        "sources": source_records,
    })
