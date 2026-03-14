"""Media Player — Video/audio player with transcripts and entity highlighting."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import streamlit as st

from data_explorer.streamlit.config import get_media_config, get_s3_config
from data_explorer.streamlit.data_client import DataClient
from data_explorer.streamlit.theme import apply_theme
from data_explorer.streamlit.navigation import navigate_to, render_breadcrumbs
from data_explorer.streamlit.components.entity_chip import render_entity_chip_list
from data_explorer.streamlit.components.document_renderer import render_document, render_entity_legend

st.set_page_config(page_title="Media Player", page_icon=":material/play_circle:", layout="wide")
apply_theme()

# --- Breadcrumbs ---
render_breadcrumbs([
    ("Home", "app.py"),
    ("Media Player", None),
])

st.header("Media Player")

VIDEO_EXTENSIONS = {".mp4", ".webm", ".mkv", ".avi"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg", ".flac"}


@st.cache_resource
def _get_client() -> DataClient:
    cfg = get_s3_config()
    return DataClient(
        endpoint_url=cfg.endpoint_url,
        access_key=cfg.access_key,
        secret_key=cfg.secret_key,
        bucket=cfg.bucket,
    )


def _scan_media_dir(path: str) -> list[Path]:
    """Scan a directory for media files."""
    p = Path(path)
    if not p.exists():
        return []
    files = []
    for f in sorted(p.rglob("*")):
        if f.suffix.lower() in VIDEO_EXTENSIONS | AUDIO_EXTENSIONS:
            files.append(f)
    return files


@st.cache_data(ttl=300)
def _load_media_metadata() -> list[dict]:
    """Load media_metadata asset from S3 if available."""
    client = _get_client()
    assets = client.list_assets()
    meta_assets = [a for a in assets if "media_metadata" in a["asset"].lower()]
    if not meta_assets:
        return []
    return client.load_data(meta_assets[0]["root"], limit=5000)


@st.cache_data(ttl=300)
def _load_transcriptions() -> list[dict]:
    """Load media_transcriptions asset from S3 if available."""
    client = _get_client()
    assets = client.list_assets()
    tx_assets = [a for a in assets if "transcription" in a["asset"].lower()]
    if not tx_assets:
        return []
    return client.load_data(tx_assets[0]["root"], limit=5000)


@st.cache_data(ttl=300)
def _load_entities_for_source(source: str) -> list[dict]:
    """Load NER entities for a given source via the DataClient."""
    client = _get_client()
    try:
        return client.load_entities(source)
    except Exception:
        return []


@st.cache_data(ttl=300)
def _discover_entity_sources() -> list[str]:
    """Discover all sources that have entity assets."""
    client = _get_client()
    assets = client.list_assets()
    return sorted({
        a["code_location"]
        for a in assets
        if "entit" in a["asset"].lower() and a["layer"] == "silver"
    })


def _find_entities_for_file(filename_stem: str) -> list[dict]:
    """Search across all entity sources for entities matching a media file.

    Matches entities where ``source_doc_id`` contains the filename stem.
    """
    sources = _discover_entity_sources()
    all_entities: list[dict] = []
    for src in sources:
        entities = _load_entities_for_source(src)
        for e in entities:
            doc_id = str(e.get("source_doc_id", ""))
            if filename_stem.lower() in doc_id.lower():
                all_entities.append(e)
    return all_entities


def _aggregate_entities(entities: list[dict]) -> list[dict]:
    """Aggregate entity occurrences and return sorted by count descending."""
    counter: Counter = Counter()
    label_map: dict[str, str] = {}
    for e in entities:
        text = e.get("text", "").strip()
        label = e.get("label", "UNKNOWN")
        if text:
            counter[(text, label)] += 1
            label_map[text] = label
    return [
        {"text": text, "label": label, "count": count}
        for (text, label), count in counter.most_common()
    ]


media_cfg = get_media_config()

# --- Sidebar: file browser ---
with st.sidebar:
    st.subheader("Media Sources")
    source = st.radio("Source directory", ["metube", "tubesync"])
    media_path = media_cfg.metube_path if source == "metube" else media_cfg.tubesync_path

    files = _scan_media_dir(media_path)
    if not files:
        st.warning(f"No media files found in {media_path}")
        st.stop()

    file_names = [str(f.relative_to(media_path)) for f in files]
    selected_idx = st.selectbox("File", range(len(file_names)), format_func=lambda i: file_names[i])

selected_file = files[selected_idx]
st.subheader(selected_file.name)

# --- Player ---
col_player, col_info = st.columns([2, 1])

with col_player:
    if selected_file.suffix.lower() in VIDEO_EXTENSIONS:
        st.video(str(selected_file))
    elif selected_file.suffix.lower() in AUDIO_EXTENSIONS:
        st.audio(str(selected_file))

with col_info:
    # File info
    stat = selected_file.stat()
    st.metric("Size", f"{stat.st_size / (1024*1024):.1f} MB")
    st.caption(f"Path: `{selected_file}`")

    # S3 metadata (if available)
    meta_rows = _load_media_metadata()
    if meta_rows:
        fname = selected_file.name
        matching = [m for m in meta_rows if m.get("filename") == fname or m.get("title", "").lower() in fname.lower()]
        if matching:
            m = matching[0]
            if "duration" in m:
                mins = float(m["duration"]) / 60
                st.metric("Duration", f"{mins:.1f} min")
            for field in ["codec", "resolution", "fps", "channels"]:
                if field in m:
                    st.caption(f"{field}: {m[field]}")

# --- Transcript section ---
st.divider()
transcriptions = _load_transcriptions()
transcript_text: str = ""
transcript_entities: list[dict] = []

if transcriptions:
    fname = selected_file.stem
    matching_tx = [t for t in transcriptions if fname.lower() in str(t.get("source", "")).lower() or fname.lower() in str(t.get("filename", "")).lower()]
    if matching_tx:
        st.subheader("Transcript")
        tx = matching_tx[0]
        transcript_text = tx.get("text") or tx.get("transcript") or tx.get("content", "")

        # --- Entity overlay: try to find entities for this transcript ---
        transcript_entities = _find_entities_for_file(fname)

        if transcript_text and transcript_entities:
            # Show entity legend above the annotated transcript
            render_entity_legend()
            render_document(text=transcript_text, entities=transcript_entities)
        elif transcript_text:
            # Fallback: plain text display when no entities are available
            st.text_area("Transcript", transcript_text, height=400, disabled=True)

        # Segments if available
        segments = tx.get("segments", [])
        if segments and isinstance(segments, list):
            with st.expander(f"Segments ({len(segments)})"):
                for seg in segments[:100]:
                    start = seg.get("start", 0)
                    st.markdown(f"**[{start:.1f}s]** {seg.get('text', '')}")
    else:
        st.info("No transcript available for this file.")
else:
    st.info("No transcription data loaded from S3.")

# --- "Find Similar" button ---
if transcript_text:
    st.divider()
    if st.button("Find Similar", icon=":material/search:", use_container_width=False):
        # Truncate to a reasonable query length for embedding search
        query_snippet = transcript_text[:1000]
        navigate_to(
            "pages/5_Semantic_Explorer.py",
            query_text=query_snippet,
        )

# --- Sidebar: Entity Summary ---
if transcript_entities:
    aggregated = _aggregate_entities(transcript_entities)
    with st.sidebar:
        st.divider()
        st.subheader("Entity Summary")
        st.caption(f"{len(aggregated)} unique entities found in transcript")

        clicked_entity = render_entity_chip_list(
            aggregated[:30],
            max_display=15,
            columns=2,
        )

        if clicked_entity:
            # Find the label for the clicked entity
            clicked_label = "UNKNOWN"
            for ent in aggregated:
                if ent["text"] == clicked_entity:
                    clicked_label = ent["label"]
                    break
            navigate_to(
                "pages/8_Entity_Concordance.py",
                entity_text=clicked_entity,
                entity_label=clicked_label,
            )
