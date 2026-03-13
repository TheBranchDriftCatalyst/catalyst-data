"""Media Player — Video/audio player with transcripts."""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import streamlit as st

from data_explorer.streamlit.config import get_media_config, get_s3_config
from data_explorer.streamlit.data_client import DataClient

st.set_page_config(page_title="Media Player", page_icon=":material/play_circle:", layout="wide")
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

# --- Transcript sidebar ---
st.divider()
transcriptions = _load_transcriptions()
if transcriptions:
    fname = selected_file.stem
    matching_tx = [t for t in transcriptions if fname.lower() in str(t.get("source", "")).lower() or fname.lower() in str(t.get("filename", "")).lower()]
    if matching_tx:
        st.subheader("Transcript")
        tx = matching_tx[0]
        text = tx.get("text") or tx.get("transcript") or tx.get("content", "")
        if text:
            st.text_area("Transcript", text, height=400, disabled=True)
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
