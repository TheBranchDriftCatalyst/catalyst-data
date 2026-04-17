"""S3 File Explorer — tree-based MinIO browser with inline JSON/JSONL viewer."""

from __future__ import annotations

import json

import streamlit as st

from dagster_io.s3_client import S3Client
from data_explorer.streamlit.config import get_s3_config
from data_explorer.streamlit.theme import apply_theme

st.set_page_config(page_title="S3 File Explorer", page_icon=":material/folder:", layout="wide")
apply_theme()

# ── Extra CSS for explorer-specific styling ──────────────────────────────────

st.markdown(
    """
<style>
/* Tighter sidebar for tree view */
[data-testid="stSidebar"] .block-container { padding-top: 1rem; }

/* File viewer header */
.file-header {
    background: #16161d;
    border: 1px solid #27272a;
    border-radius: 0.25rem;
    padding: 0.8rem 1.2rem;
    margin-bottom: 1rem;
    display: flex;
    align-items: center;
    gap: 1rem;
    font-family: 'Space Mono', monospace;
    font-size: 0.8rem;
}
.file-header .path {
    color: #a1a1aa;
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
.file-header .path span { color: #00fcd6; }
.file-header .meta {
    color: #a1a1aa;
    font-size: 0.7rem;
    white-space: nowrap;
}
.file-header .meta b { color: #e4e4e7; }

/* Breadcrumb bar */
.breadcrumbs {
    display: flex;
    align-items: center;
    gap: 0.3rem;
    padding: 0.5rem 0;
    font-family: 'Rajdhani', sans-serif;
    font-size: 0.85rem;
    flex-wrap: wrap;
}
.breadcrumbs .sep { color: #27272a; }
.breadcrumbs .crumb {
    color: #a1a1aa;
    cursor: pointer;
    padding: 0.15rem 0.4rem;
    border-radius: 0.15rem;
    transition: all 0.15s;
}
.breadcrumbs .crumb:hover {
    color: #00fcd6;
    background: rgba(0,252,214,0.08);
}
.breadcrumbs .crumb.active {
    color: #00fcd6;
    font-weight: 600;
}

/* Record count badge */
.record-badge {
    display: inline-block;
    background: rgba(0,252,214,0.1);
    color: #00fcd6;
    border: 1px solid rgba(0,252,214,0.3);
    border-radius: 0.15rem;
    padding: 0.15rem 0.5rem;
    font-family: 'Space Mono', monospace;
    font-size: 0.75rem;
    margin-left: 0.5rem;
}
</style>
""",
    unsafe_allow_html=True,
)


# ── S3 helpers ───────────────────────────────────────────────────────────────


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
def _list_dir(prefix: str) -> tuple[list[str], list[dict]]:
    """List folders and files under prefix. Returns (folders, files_with_meta)."""
    s3 = _get_s3()
    paginator = s3._client.get_paginator("list_objects_v2")
    folders: list[str] = []
    files: list[dict] = []

    for page in paginator.paginate(Bucket=s3.bucket, Prefix=prefix, Delimiter="/"):
        for cp in page.get("CommonPrefixes", []):
            folders.append(cp["Prefix"])
        for obj in page.get("Contents", []):
            if obj["Key"] != prefix:
                files.append(
                    {
                        "key": obj["Key"],
                        "name": obj["Key"].split("/")[-1],
                        "size": obj.get("Size", 0),
                        "modified": obj.get("LastModified"),
                    }
                )
    return sorted(folders), sorted(files, key=lambda f: f["name"])


@st.cache_data(ttl=60)
def _load_file(key: str) -> bytes:
    return _get_s3().get_object(key)


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def _file_icon(name: str) -> str:
    if name.endswith(".json"):
        return "🔷"
    if name.endswith(".jsonl"):
        return "📋"
    if name.endswith(".parquet"):
        return "📊"
    if name.endswith(".csv"):
        return "📈"
    if name.startswith("_metadata"):
        return "⚙️"
    return "📄"


def _fmt_time(dt) -> str:
    if dt is None:
        return ""
    if hasattr(dt, "strftime"):
        return dt.strftime("%b %d %H:%M")
    return str(dt)[:16]


# ── Session state ────────────────────────────────────────────────────────────

if "s3_path" not in st.session_state:
    st.session_state.s3_path = ""
if "s3_file" not in st.session_state:
    st.session_state.s3_file = None


def _navigate(path: str):
    st.session_state.s3_path = path
    st.session_state.s3_file = None


def _select_file(key: str):
    st.session_state.s3_file = key


# ══════════════════════════════════════════════════════════════════════════════
# SIDEBAR: Tree navigation
# ══════════════════════════════════════════════════════════════════════════════

with st.sidebar:
    st.markdown("### :material/folder: S3 Explorer")

    col_r, col_j = st.columns(2)
    with col_r:
        if st.button("↻ Refresh", use_container_width=True):
            st.cache_data.clear()
            st.rerun()
    with col_j:
        jump = st.text_input("Go to", value="", placeholder="path/to/dir/", label_visibility="collapsed")
        if jump:
            _navigate(jump)
            st.rerun()

    st.divider()

    # Quick-access bookmarks
    st.markdown(
        "<p style='color:#a1a1aa;font-size:0.7rem;text-transform:uppercase;"
        "letter-spacing:0.1em;margin:0 0 0.3rem 0;font-family:Rajdhani,sans-serif;"
        "font-weight:600;'>Bookmarks</p>",
        unsafe_allow_html=True,
    )
    bookmarks = {
        "📦 Bronze": "bronze/",
        "🥈 Silver": "silver/",
        "🥇 Gold": "gold/",
        "🏛️ Congress": "silver/congress_data/",
        "📊 Manifests": "silver/congress_data/manifests/",
        "⏱️ Watermarks": "silver/congress_data/state/",
        "🎬 Media": "silver/media_ingest/",
    }
    bm_cols = st.columns(2)
    for i, (label, path) in enumerate(bookmarks.items()):
        with bm_cols[i % 2]:
            if st.button(label, key=f"bm_{path}", use_container_width=True):
                _navigate(path)
                st.rerun()

    st.divider()

    # Directory tree
    current = st.session_state.s3_path
    folders, files = _list_dir(current)

    # Show parent link
    if current:
        parent = "/".join(current.rstrip("/").split("/")[:-1])
        if parent:
            parent += "/"
        if st.button("⬆️ ..", key="parent", use_container_width=True):
            _navigate(parent)
            st.rerun()

    # Folders
    for folder in folders:
        name = folder.rstrip("/").split("/")[-1]
        if st.button(f"📁  {name}", key=f"d_{folder}", use_container_width=True):
            _navigate(folder)
            st.rerun()

    # Files in sidebar (compact list)
    if files:
        st.markdown(
            f"<p style='color:#a1a1aa;font-size:0.65rem;margin:0.5rem 0 0.2rem;'>"
            f"{len(files)} file{'s' if len(files) != 1 else ''}</p>",
            unsafe_allow_html=True,
        )
        for f in files:
            icon = _file_icon(f["name"])
            size = _fmt_size(f["size"])
            label = f"{icon} {f['name']}"
            is_selected = st.session_state.s3_file == f["key"]

            if st.button(
                label,
                key=f"f_{f['key']}",
                use_container_width=True,
                type="primary" if is_selected else "secondary",
            ):
                _select_file(f["key"])
                st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# MAIN: Breadcrumbs + File viewer
# ══════════════════════════════════════════════════════════════════════════════

# Breadcrumbs
current = st.session_state.s3_path
parts = [p for p in current.split("/") if p]
crumb_html = '<div class="breadcrumbs">'
crumb_html += '<span class="crumb" style="color:#00fcd6;font-weight:700;">🪣 bucket</span>'
for i, part in enumerate(parts):
    crumb_html += '<span class="sep">/</span>'
    is_last = i == len(parts) - 1
    cls = "crumb active" if is_last else "crumb"
    crumb_html += f'<span class="{cls}">{part}</span>'
crumb_html += "</div>"
st.markdown(crumb_html, unsafe_allow_html=True)

# ── File content viewer ──────────────────────────────────────────────────────

selected = st.session_state.s3_file

if selected:
    fname = selected.split("/")[-1]

    # Find file metadata
    file_meta = next((f for f in files if f["key"] == selected), None)
    size_str = _fmt_size(file_meta["size"]) if file_meta else ""
    mod_str = _fmt_time(file_meta["modified"]) if file_meta else ""

    # Path display with file highlighted
    path_parts = selected.rsplit("/", 1)
    dir_part = path_parts[0] + "/" if len(path_parts) > 1 else ""

    st.markdown(
        f'<div class="file-header">'
        f'<div class="path">{dir_part}<span>{fname}</span></div>'
        f'<div class="meta"><b>{size_str}</b> · {mod_str}</div>'
        f"</div>",
        unsafe_allow_html=True,
    )

    try:
        raw = _load_file(selected)

        # ── JSON ──
        if fname.endswith(".json"):
            data = json.loads(raw.decode("utf-8"))

            tab_tree, tab_table, tab_raw = st.tabs(["Tree View", "Table", "Raw"])

            with tab_tree:
                st.json(data, expanded=2)

            with tab_table:
                import pandas as pd

                if isinstance(data, list):
                    df = pd.DataFrame(data)
                    st.dataframe(df, use_container_width=True, height=500)
                elif isinstance(data, dict):
                    # Nested dict → show key-value pairs
                    flat = {k: str(v)[:200] for k, v in data.items()}
                    df = pd.DataFrame(list(flat.items()), columns=["Key", "Value"])
                    st.dataframe(df, use_container_width=True, height=400)

            with tab_raw:
                st.code(json.dumps(data, indent=2, default=str)[:80000], language="json")

        # ── JSONL ──
        elif fname.endswith(".jsonl"):
            text = raw.decode("utf-8")
            lines = [json.loads(line) for line in text.strip().split("\n") if line.strip()]

            st.markdown(
                f'<span class="record-badge">{len(lines)} records</span>',
                unsafe_allow_html=True,
            )

            tab_table, tab_records, tab_raw = st.tabs(["Table", "Records", "Raw"])

            with tab_table:
                import pandas as pd

                df = pd.DataFrame(lines)
                st.dataframe(df, use_container_width=True, height=500)

                # Column stats
                with st.expander("Column info"):
                    for col in df.columns:
                        nunique = df[col].nunique()
                        nulls = df[col].isna().sum()
                        st.markdown(
                            f"**{col}** — {df[col].dtype} · {nunique} unique · {nulls} null",
                        )

            with tab_records:
                page_size = 10
                total_pages = max(1, (len(lines) + page_size - 1) // page_size)
                page = st.number_input("Page", min_value=1, max_value=total_pages, value=1, step=1)
                start = (page - 1) * page_size
                end = min(start + page_size, len(lines))

                st.caption(f"Showing {start + 1}–{end} of {len(lines)}")

                for i in range(start, end):
                    with st.expander(f"Record {i}", expanded=(i == start)):
                        st.json(lines[i], expanded=2)

            with tab_raw:
                st.code(text[:80000], language="json")

        # ── Parquet ──
        elif fname.endswith(".parquet"):
            from io import BytesIO

            import pandas as pd

            df = pd.read_parquet(BytesIO(raw))
            st.markdown(
                f'<span class="record-badge">{len(df)} rows × {len(df.columns)} cols</span>',
                unsafe_allow_html=True,
            )
            st.dataframe(df, use_container_width=True, height=500)

        # ── CSV ──
        elif fname.endswith(".csv"):
            from io import StringIO

            import pandas as pd

            df = pd.read_csv(StringIO(raw.decode("utf-8")))
            st.dataframe(df, use_container_width=True, height=500)

        # ── Text ──
        elif fname.endswith((".txt", ".md", ".yaml", ".yml", ".toml", ".xml")):
            lang = fname.rsplit(".", 1)[-1]
            if lang == "yml":
                lang = "yaml"
            st.code(raw.decode("utf-8", errors="replace")[:80000], language=lang)

        # ── Binary ──
        else:
            st.warning(f"Binary file — no inline preview for `.{fname.rsplit('.', 1)[-1]}`")
            st.download_button("⬇️ Download", raw, file_name=fname)

    except Exception as e:
        st.error(f"Error loading file: {e}")

elif not current:
    # Landing state
    st.markdown("### Welcome to the S3 File Explorer")
    st.markdown("Use the **sidebar** to navigate folders, or click a **bookmark** to jump to a location.")

    # Show top-level folder overview
    folders, _ = _list_dir("")
    if folders:
        cols = st.columns(min(len(folders), 4))
        for i, folder in enumerate(folders):
            name = folder.rstrip("/").split("/")[-1]
            with cols[i % len(cols)]:
                if st.button(f"📁 {name}", key=f"landing_{folder}", use_container_width=True):
                    _navigate(folder)
                    st.rerun()
else:
    # Directory view with no file selected
    if not files:
        st.info(f"No files in `{current}` — navigate into a subfolder from the sidebar.")
    else:
        st.markdown(f"**{len(files)} files** — select one from the sidebar to preview.")

        # Quick overview table
        import pandas as pd

        overview = pd.DataFrame(
            [
                {
                    "Name": f["name"],
                    "Size": _fmt_size(f["size"]),
                    "Modified": _fmt_time(f["modified"]),
                    "Type": f["name"].rsplit(".", 1)[-1] if "." in f["name"] else "—",
                }
                for f in files
            ]
        )
        st.dataframe(overview, use_container_width=True, hide_index=True)
