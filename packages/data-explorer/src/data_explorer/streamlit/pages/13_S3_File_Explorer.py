"""S3 File Explorer — browse any path in MinIO and view JSON/JSONL inline."""

from __future__ import annotations

import json

import streamlit as st

from dagster_io.s3_client import S3Client
from data_explorer.streamlit.config import get_s3_config
from data_explorer.streamlit.theme import apply_theme

st.set_page_config(page_title="S3 File Explorer", page_icon=":material/folder:", layout="wide")
apply_theme()
st.header("S3 File Explorer")


@st.cache_resource
def _get_s3() -> S3Client:
    cfg = get_s3_config()
    return S3Client(
        endpoint_url=cfg.endpoint_url,
        access_key=cfg.access_key,
        secret_key=cfg.secret_key,
        bucket=cfg.bucket,
    )


@st.cache_data(ttl=120)
def _list_prefixes(prefix: str) -> tuple[list[str], list[str]]:
    """List sub-folders and files under a prefix.

    Returns (folders, files) where folders are common prefixes and
    files are leaf keys.
    """
    s3 = _get_s3()
    # Use the low-level paginator to get CommonPrefixes (folders)
    paginator = s3._client.get_paginator("list_objects_v2")
    folders: list[str] = []
    files: list[str] = []

    for page in paginator.paginate(
        Bucket=s3.bucket,
        Prefix=prefix,
        Delimiter="/",
    ):
        for cp in page.get("CommonPrefixes", []):
            folders.append(cp["Prefix"])
        for obj in page.get("Contents", []):
            key = obj["Key"]
            # Skip the prefix itself
            if key != prefix:
                files.append(key)

    return sorted(folders), sorted(files)


@st.cache_data(ttl=60)
def _load_file(key: str) -> bytes:
    return _get_s3().get_object(key)


def _render_breadcrumbs(path: str) -> str | None:
    """Render clickable breadcrumb path. Returns new path if clicked."""
    parts = path.rstrip("/").split("/")
    cols = st.columns(len(parts) + 1)
    with cols[0]:
        if st.button("🏠", key="root"):
            return ""
    for i, part in enumerate(parts):
        if not part:
            continue
        with cols[i + 1]:
            if st.button(f"📁 {part}", key=f"bc_{i}"):
                return "/".join(parts[: i + 1]) + "/"
    return None


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"


def _get_file_icon(key: str) -> str:
    if key.endswith(".json"):
        return "📄"
    elif key.endswith(".jsonl"):
        return "📋"
    elif key.endswith(".parquet"):
        return "📊"
    elif key.endswith(".csv"):
        return "📈"
    elif key.endswith(".txt"):
        return "📝"
    return "📎"


# ── Session state ────────────────────────────────────────────────────────────

if "s3_path" not in st.session_state:
    st.session_state.s3_path = ""
if "s3_selected_file" not in st.session_state:
    st.session_state.s3_selected_file = None

# ── Sidebar ──────────────────────────────────────────────────────────────────

with st.sidebar:
    st.subheader("Navigation")

    if st.button("🔄 Refresh"):
        st.cache_data.clear()
        st.rerun()

    manual_path = st.text_input(
        "Jump to path",
        value=st.session_state.s3_path,
        placeholder="silver/congress_data/manifests/",
    )
    if manual_path != st.session_state.s3_path:
        st.session_state.s3_path = manual_path
        st.session_state.s3_selected_file = None
        st.rerun()

    # Quick links
    st.markdown("**Quick links:**")
    quick_paths = [
        ("Bronze / Congress", "bronze/congress_data/"),
        ("Silver / Congress", "silver/congress_data/"),
        ("Gold / Congress", "gold/congress_data/"),
        ("Bronze / Media", "bronze/media_ingest/"),
        ("Silver / Media", "silver/media_ingest/"),
        ("Gold / Media", "gold/media_ingest/"),
        ("Manifests", "silver/congress_data/manifests/"),
        ("State / Watermarks", "silver/congress_data/state/"),
    ]
    for label, path in quick_paths:
        if st.button(label, key=f"quick_{path}"):
            st.session_state.s3_path = path
            st.session_state.s3_selected_file = None
            st.rerun()

# ── Breadcrumbs ──────────────────────────────────────────────────────────────

new_path = _render_breadcrumbs(st.session_state.s3_path)
if new_path is not None:
    st.session_state.s3_path = new_path
    st.session_state.s3_selected_file = None
    st.rerun()

st.divider()

# ── List current directory ───────────────────────────────────────────────────

current = st.session_state.s3_path
folders, files = _list_prefixes(current)

# Folders
if folders:
    st.subheader(f"📂 Folders ({len(folders)})")
    folder_cols = st.columns(min(len(folders), 4))
    for i, folder in enumerate(folders):
        name = folder.rstrip("/").split("/")[-1]
        with folder_cols[i % len(folder_cols)]:
            if st.button(f"📁 {name}", key=f"folder_{folder}", use_container_width=True):
                st.session_state.s3_path = folder
                st.session_state.s3_selected_file = None
                st.rerun()

# Files
if files:
    st.subheader(f"📄 Files ({len(files)})")
    for fkey in files:
        fname = fkey.split("/")[-1]
        icon = _get_file_icon(fkey)
        col1, col2 = st.columns([5, 1])
        with col1:
            if st.button(f"{icon} {fname}", key=f"file_{fkey}", use_container_width=True):
                st.session_state.s3_selected_file = fkey
                st.rerun()
        with col2:
            try:
                head = _get_s3().head_object(fkey)
                if head:
                    size = head.get("ContentLength", 0)
                    st.caption(_format_size(size))
            except Exception:
                pass

if not folders and not files:
    st.info(f"Empty directory: `{current or '(root)'}`")

# ── File viewer ──────────────────────────────────────────────────────────────

if st.session_state.s3_selected_file:
    st.divider()
    fkey = st.session_state.s3_selected_file
    fname = fkey.split("/")[-1]
    st.subheader(f"Viewing: `{fkey}`")

    try:
        raw = _load_file(fkey)

        # Size info
        st.caption(f"Size: {_format_size(len(raw))}")

        # JSON
        if fname.endswith(".json"):
            try:
                data = json.loads(raw.decode("utf-8"))
                tab_pretty, tab_raw, tab_table = st.tabs(["Pretty", "Raw", "Table"])
                with tab_pretty:
                    st.json(data, expanded=2)
                with tab_raw:
                    st.code(json.dumps(data, indent=2, default=str)[:50000], language="json")
                with tab_table:
                    if isinstance(data, list):
                        import pandas as pd

                        st.dataframe(pd.DataFrame(data), use_container_width=True)
                    elif isinstance(data, dict):
                        import pandas as pd

                        # Flatten single-level dict into table
                        st.dataframe(pd.DataFrame([data]), use_container_width=True)
            except json.JSONDecodeError as e:
                st.error(f"Invalid JSON: {e}")
                st.code(raw.decode("utf-8", errors="replace")[:10000])

        # JSONL
        elif fname.endswith(".jsonl"):
            try:
                text = raw.decode("utf-8")
                lines = [json.loads(line) for line in text.strip().split("\n") if line.strip()]
                st.caption(f"{len(lines)} records")

                tab_table, tab_pretty, tab_raw = st.tabs(["Table", "Pretty", "Raw"])
                with tab_table:
                    import pandas as pd

                    df = pd.DataFrame(lines)
                    st.dataframe(df, use_container_width=True, height=500)

                    # Column filter
                    if len(df.columns) > 5:
                        selected_cols = st.multiselect("Columns", df.columns.tolist(), default=df.columns.tolist()[:8])
                        if selected_cols:
                            st.dataframe(df[selected_cols], use_container_width=True, height=400)
                with tab_pretty:
                    # Show first N records expanded
                    show_n = st.slider("Records to show", 1, min(len(lines), 50), 5, key="jsonl_n")
                    for i, record in enumerate(lines[:show_n]):
                        with st.expander(f"Record {i}", expanded=(i < 3)):
                            st.json(record, expanded=2)
                with tab_raw:
                    st.code(text[:50000], language="json")
            except Exception as e:
                st.error(f"Error parsing JSONL: {e}")
                st.code(raw.decode("utf-8", errors="replace")[:10000])

        # CSV
        elif fname.endswith(".csv"):
            from io import StringIO

            import pandas as pd

            df = pd.read_csv(StringIO(raw.decode("utf-8")))
            st.dataframe(df, use_container_width=True, height=500)

        # Parquet
        elif fname.endswith(".parquet"):
            from io import BytesIO

            import pandas as pd

            df = pd.read_parquet(BytesIO(raw))
            st.caption(f"{len(df)} rows × {len(df.columns)} columns")
            st.dataframe(df, use_container_width=True, height=500)

        # Plain text
        elif fname.endswith((".txt", ".md", ".yaml", ".yml", ".toml")):
            st.code(raw.decode("utf-8", errors="replace")[:50000])

        # Binary / unknown
        else:
            st.warning(f"No preview for `{fname}`. Download to inspect.")
            st.download_button("Download", raw, file_name=fname)

    except Exception as e:
        st.error(f"Error loading file: {e}")
