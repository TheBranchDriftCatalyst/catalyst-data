"""Bronze: Raw data extraction from open-source leaked document archives.

Downloads and parses:
- ICIJ Offshore Leaks Database (CSV bulk download)
- WikiLeaks Cablegate cables (CSV from archive.org)
- Epstein court documents (API from epsteininvestigation.org)
"""

import csv
import io
import time
import zipfile
from pathlib import Path

import httpx
from dagster import AssetExecutionContext, MetadataValue, Output, asset

from dagster_io.logging import get_logger
from dagster_io.metrics import ASSET_RECORDS_PROCESSED
from dagster_io.observability import get_tracer, trace_operation
from open_leaks.config import OpenLeaksConfig
from open_leaks.entities import Cable, CourtDocument, OffshoreEntity, OffshoreRelationship

logger = get_logger(__name__)
tracer = get_tracer(__name__)

# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------

_HTTP_TIMEOUT = httpx.Timeout(connect=30, read=300, write=30, pool=30)
# Longer read timeout for large file streaming (1.65 GB over slow links)
_STREAM_TIMEOUT = httpx.Timeout(connect=30, read=600, write=30, pool=30)
_MAX_RETRIES = 5
_RETRY_BACKOFF_BASE = 2  # seconds — exponential: 2, 4, 8, 16, 32
_PROGRESS_INTERVAL_MB = 25  # log progress every N MB

# Retryable transport-level exceptions (covers connection drops mid-download,
# read timeouts, and generic transport failures — not just connect + timeout).
_RETRYABLE_ERRORS = (
    httpx.ConnectError,
    httpx.TimeoutException,
    httpx.ReadError,
    httpx.RemoteProtocolError,
    httpx.CloseError,
)


def _retry_on_network_error(
    func,
    *args,
    context: AssetExecutionContext,
    description: str = "request",
    max_retries: int = _MAX_RETRIES,
    **kwargs,
):
    """Execute *func* with exponential-backoff retry on network errors.

    Retries up to *max_retries* times for transport-level httpx errors
    (connect, timeout, read, protocol, close).  Other exceptions propagate
    immediately.
    """
    for attempt in range(1, max_retries + 1):
        try:
            return func(*args, **kwargs)
        except _RETRYABLE_ERRORS as exc:
            if attempt == max_retries:
                context.log.error(
                    f"{description}: failed after {max_retries} attempts — {type(exc).__name__}: {exc}"
                )
                raise
            delay = _RETRY_BACKOFF_BASE ** attempt
            context.log.warning(
                f"{description}: attempt {attempt}/{max_retries} failed "
                f"({type(exc).__name__}: {exc}), retrying in {delay}s…"
            )
            time.sleep(delay)


def _ensure_cache(config: OpenLeaksConfig) -> Path:
    cache = Path(config.cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    return cache


def _download_file(
    url: str,
    dest: Path,
    context: AssetExecutionContext,
) -> Path:
    """Stream-download a large file with resume support and progress logging.

    * **Streaming**: writes chunks to disk immediately — never holds the full
      file in memory.
    * **Resume**: if a partial file exists from a previous interrupted attempt,
      sends an HTTP ``Range`` header so the server can pick up where it left
      off (common on archive.org / CDN origins).  Falls back to a full
      download if the server returns 200 instead of 206.
    * **Retries**: each download attempt is retried independently via
      ``_retry_on_network_error``.
    * **Cache**: if the file already exists *and* its size matches the
      ``Content-Length`` from a HEAD request, skips the download entirely.
    """
    if dest.exists() and dest.stat().st_size > 0:
        # Quick validation: ask the server for the expected size so we don't
        # skip a truncated file from a prior crash.
        try:
            head = httpx.head(url, follow_redirects=True, timeout=_HTTP_TIMEOUT)
            expected = int(head.headers.get("content-length", 0))
            actual = dest.stat().st_size
            if expected and actual >= expected:
                context.log.info(
                    f"Using cached file: {dest} ({actual / 1024 / 1024:.1f} MB, "
                    f"matches expected {expected / 1024 / 1024:.1f} MB)"
                )
                return dest
            if expected:
                context.log.info(
                    f"Cached file incomplete: {actual / 1024 / 1024:.1f} / "
                    f"{expected / 1024 / 1024:.1f} MB — will attempt resume"
                )
        except _RETRYABLE_ERRORS:
            # If HEAD fails, fall through to download (cache might be fine)
            if dest.stat().st_size > 0:
                context.log.warning(
                    f"HEAD request failed; using potentially-complete cached file: {dest}"
                )
                return dest

    def _do_download():
        # Determine resume offset from any existing partial file
        resume_offset = 0
        if dest.exists():
            resume_offset = dest.stat().st_size

        headers: dict[str, str] = {}
        if resume_offset > 0:
            headers["Range"] = f"bytes={resume_offset}-"
            context.log.info(
                f"Resuming download of {url} from byte {resume_offset} "
                f"({resume_offset / 1024 / 1024:.1f} MB)"
            )
        else:
            context.log.info(f"Downloading {url} → {dest}")

        with httpx.stream(
            "GET", url, headers=headers, follow_redirects=True, timeout=_STREAM_TIMEOUT,
        ) as r:
            r.raise_for_status()

            # Determine whether server honoured the Range request
            is_partial = r.status_code == 206
            if resume_offset > 0 and not is_partial:
                # Server ignored Range header — restart from scratch
                context.log.warning(
                    "Server returned 200 (not 206); restarting full download"
                )
                resume_offset = 0

            total = int(r.headers.get("content-length", 0))
            if is_partial:
                total += resume_offset  # content-length is remaining bytes
            downloaded = resume_offset

            mode = "ab" if is_partial else "wb"
            with open(dest, mode) as f:
                for chunk in r.iter_bytes(chunk_size=65_536):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total and downloaded % (_PROGRESS_INTERVAL_MB * 1024 * 1024) < 65_536:
                        pct = downloaded / total * 100
                        context.log.info(
                            f"  {downloaded / 1024 / 1024:.1f} / "
                            f"{total / 1024 / 1024:.1f} MB ({pct:.0f}%)"
                        )

    _retry_on_network_error(
        _do_download,
        context=context,
        description=f"download {url}",
    )

    if not dest.exists() or dest.stat().st_size == 0:
        raise RuntimeError(f"Download produced empty file: {dest}")

    size_mb = dest.stat().st_size / 1024 / 1024
    context.log.info(f"Download complete: {dest.name} ({size_mb:.1f} MB)")
    return dest


# ---------------------------------------------------------------------------
# ICIJ Offshore Leaks — CSV bulk download
# ---------------------------------------------------------------------------

_ICIJ_NODE_FILES = [
    ("nodes-entities.csv", "Entity"),
    ("nodes-officers.csv", "Officer"),
    ("nodes-intermediaries.csv", "Intermediary"),
    ("nodes-addresses.csv", "Address"),
    ("nodes-others.csv", "Other"),
]


def _find_in_zip(zf: zipfile.ZipFile, suffix: str) -> str | None:
    for name in zf.namelist():
        if name.endswith(suffix):
            return name
    return None


def _parse_icij_entities_from_zip(
    zip_path: Path,
    context: AssetExecutionContext,
    max_count: int = 0,
) -> list[OffshoreEntity]:
    entities: list[OffshoreEntity] = []
    with zipfile.ZipFile(zip_path) as zf:
        for csv_suffix, entity_type in _ICIJ_NODE_FILES:
            match = _find_in_zip(zf, csv_suffix)
            if not match:
                context.log.warning(f"Not found in ZIP: {csv_suffix}")
                continue

            with zf.open(match) as f:
                reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
                count_before = len(entities)
                for row in reader:
                    if max_count and len(entities) >= max_count:
                        break
                    node_id = row.get("node_id", row.get("id", ""))
                    entities.append(
                        OffshoreEntity(
                            id=str(node_id),
                            name=row.get("name", ""),
                            entity_type=entity_type,
                            jurisdiction=row.get("jurisdiction", row.get("jurisdiction_description", "")),
                            country=row.get("countries", row.get("country_codes", "")),
                            source_dataset=row.get("sourceID", ""),
                            status=row.get("status", ""),
                            incorporation_date=row.get("incorporation_date", ""),
                            source_url=f"https://offshoreleaks.icij.org/nodes/{node_id}" if node_id else None,
                        )
                    )
                added = len(entities) - count_before
                context.log.info(f"  {csv_suffix}: {added} entities")
                if max_count and len(entities) >= max_count:
                    break

    context.log.info(f"Total ICIJ entities parsed: {len(entities)}")
    return entities


def _parse_icij_relationships_from_zip(
    zip_path: Path,
    context: AssetExecutionContext,
    max_count: int = 0,
) -> list[OffshoreRelationship]:
    relationships: list[OffshoreRelationship] = []
    with zipfile.ZipFile(zip_path) as zf:
        match = _find_in_zip(zf, "relationships.csv")
        if not match:
            context.log.error("relationships.csv not found in ZIP")
            return relationships

        with zf.open(match) as f:
            reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
            for i, row in enumerate(reader):
                if max_count and i >= max_count:
                    break
                relationships.append(
                    OffshoreRelationship(
                        id=str(i),
                        source_id=str(row.get("node_id_start", row.get("START_ID", ""))),
                        target_id=str(row.get("node_id_end", row.get("END_ID", ""))),
                        rel_type=row.get("rel_type", row.get("TYPE", "")),
                        source_dataset=row.get("sourceID", ""),
                        start_date=row.get("start_date", ""),
                        end_date=row.get("end_date", ""),
                    )
                )

    context.log.info(f"Total ICIJ relationships parsed: {len(relationships)}")
    return relationships


# ---------------------------------------------------------------------------
# WikiLeaks Cablegate — CSV from archive.org
# ---------------------------------------------------------------------------

# The cables.csv has 8 columns (no headers), but body fields contain unescaped
# newlines that break standard CSV parsing. We detect cable boundaries using
# the start-of-row pattern and accumulate lines between boundaries.
#
# Columns: id, date, reference_id, origin, classification, references, header, body
# SUBJECT: and TAGS: appear in the body text, not the header.

import re

_CABLE_START = re.compile(r'^"(\d+)","(\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2})"')


def _parse_cable_block(lines: list[str]) -> dict | None:
    """Parse accumulated lines for a single cable into a dict."""
    block = "".join(lines)
    try:
        rows = list(csv.reader(io.StringIO(block)))
    except csv.Error:
        return None

    if not rows or len(rows[0]) < 6:
        return None

    r = rows[0]
    # Continuation rows are part of the body that got split by newlines
    body_extra = "\n".join(",".join(row) for row in rows[1:] if row)
    body = (r[7] if len(r) > 7 else "") + ("\n" + body_extra if body_extra else "")

    return {
        "id": r[0],
        "date": r[1],
        "ref_id": r[2],
        "origin": r[3],
        "classification": r[4],
        "references": r[5],
        "header": r[6] if len(r) > 6 else "",
        "body": body,
    }


def _extract_subject(body: str) -> str:
    for line in body.split("\n"):
        stripped = line.strip().upper()
        if stripped.startswith("SUBJECT:"):
            return line.strip()[8:].strip()
        if stripped.startswith("SUBJ:"):
            return line.strip()[5:].strip()
    return ""


def _extract_tags(body: str) -> list[str]:
    for line in body.split("\n"):
        stripped = line.strip().upper()
        if stripped.startswith("TAGS:"):
            raw = line.strip()[5:]
            return [t.strip() for t in raw.split(",") if t.strip()]
    return []


def _parse_cables_csv(
    csv_path: Path,
    context: AssetExecutionContext,
    max_count: int = 0,
) -> list[Cable]:
    cables: list[Cable] = []
    current_lines: list[str] = []

    def _flush():
        if not current_lines:
            return
        parsed = _parse_cable_block(current_lines)
        if parsed:
            ref_id = parsed["ref_id"]
            body = parsed["body"]
            subject = _extract_subject(body) or f"Cable {ref_id}"
            tags = _extract_tags(body)

            cables.append(
                Cable(
                    id=ref_id,
                    date=parsed["date"],
                    subject=subject,
                    origin=parsed["origin"],
                    classification=parsed["classification"],
                    content=body,
                    tags=tags,
                    source_url=f"https://wikileaks.org/plusd/cables/{ref_id}.html",
                )
            )

    with open(csv_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if _CABLE_START.match(line) and current_lines:
                _flush()
                if max_count and len(cables) >= max_count:
                    break
                current_lines = [line]
                if len(cables) % 50000 == 0 and len(cables) > 0:
                    context.log.info(f"  Parsed {len(cables):,} cables...")
            else:
                current_lines.append(line)

    # Flush last cable
    if not (max_count and len(cables) >= max_count):
        _flush()

    context.log.info(f"Total cables parsed: {len(cables):,}")
    return cables


# ---------------------------------------------------------------------------
# Epstein Court Documents — REST API
# ---------------------------------------------------------------------------


def _check_api_reachable(
    api_base: str,
    context: AssetExecutionContext,
) -> bool:
    """Verify the API base URL is reachable before starting pagination.

    Returns True if the API responds (any 2xx/3xx), False otherwise.
    Logs detailed diagnostics on failure.
    """
    try:
        resp = httpx.get(
            f"{api_base}/documents?page=1&limit=1",
            follow_redirects=True,
            timeout=_HTTP_TIMEOUT,
        )
        if resp.status_code < 400:
            context.log.info(
                f"API health check passed: {api_base} (status {resp.status_code})"
            )
            return True
        context.log.error(
            f"API health check failed: {api_base} returned HTTP {resp.status_code} — "
            f"the source may be down or the URL may have changed"
        )
        return False
    except _RETRYABLE_ERRORS as exc:
        context.log.error(
            f"API health check failed: cannot reach {api_base} — "
            f"{type(exc).__name__}: {exc}. "
            f"Check DNS resolution, firewall rules, and whether the source is still online."
        )
        return False
    except httpx.HTTPStatusError as exc:
        context.log.error(
            f"API health check failed: {api_base} returned {exc.response.status_code}"
        )
        return False


def _fetch_epstein_api(
    api_base: str,
    context: AssetExecutionContext,
    max_count: int = 0,
) -> list[CourtDocument]:
    """Fetch documents from epsteininvestigation.org paginated API.

    Response format: {"data": [...], "total": N, "page": N, "limit": N}
    Document fields: id, slug, title, document_type, source, document_date,
                     excerpt, page_count, file_url, source_url

    Includes a pre-flight health check, exponential-backoff retries on each
    page, and broad transport-error handling so transient failures on page 1
    don't immediately abort the entire asset.
    """
    # --- Pre-flight: verify the API is reachable ---
    if not _check_api_reachable(api_base, context):
        context.log.error(
            f"Epstein API at {api_base} is unreachable. Returning empty list "
            f"to avoid crashing downstream assets. Re-materialise once the "
            f"source is back online."
        )
        return []

    docs: list[CourtDocument] = []
    page = 1
    page_size = 100
    consecutive_failures = 0
    max_consecutive_failures = 3
    client = httpx.Client(timeout=_HTTP_TIMEOUT, follow_redirects=True)

    try:
        while True:
            url = f"{api_base}/documents?page={page}&limit={page_size}"
            if page == 1 or page % 10 == 0:
                context.log.info(f"Fetching page {page} ({len(docs):,} docs so far)")

            try:
                resp = _retry_on_network_error(
                    client.get,
                    url,
                    context=context,
                    description=f"epstein API page {page}",
                )
                resp.raise_for_status()
                consecutive_failures = 0  # reset on success
            except httpx.HTTPStatusError as e:
                context.log.warning(
                    f"API returned HTTP {e.response.status_code} on page {page} — "
                    f"stopping pagination ({len(docs):,} docs collected)"
                )
                break
            except _RETRYABLE_ERRORS as e:
                consecutive_failures += 1
                context.log.error(
                    f"Page {page} failed after {_MAX_RETRIES} retries — "
                    f"{type(e).__name__}: {e} "
                    f"(consecutive failures: {consecutive_failures}/{max_consecutive_failures})"
                )
                if consecutive_failures >= max_consecutive_failures:
                    context.log.error(
                        f"Aborting: {max_consecutive_failures} consecutive page failures. "
                        f"Returning {len(docs):,} docs collected so far."
                    )
                    break
                # Skip this page and try the next one
                page += 1
                continue

            try:
                payload = resp.json()
            except Exception as e:
                context.log.warning(
                    f"Page {page} returned non-JSON response — {type(e).__name__}: {e}. "
                    f"Stopping pagination."
                )
                break

            items = payload.get("data", []) if isinstance(payload, dict) else payload
            total = payload.get("total", 0) if isinstance(payload, dict) else 0

            if page == 1:
                context.log.info(
                    f"API reports {total:,} total documents (page_size={page_size})"
                )

            if not items:
                context.log.info(f"Page {page} returned empty data — pagination complete")
                break

            for item in items:
                if max_count and len(docs) >= max_count:
                    break

                docs.append(
                    CourtDocument(
                        id=str(item.get("id", len(docs))),
                        title=item.get("title", item.get("slug", "")),
                        case_number=item.get("case_number", ""),
                        document_type=item.get("document_type", ""),
                        date_filed=item.get("document_date") or "",
                        content=item.get("excerpt") or "",
                        page_count=int(item.get("page_count", 0) or 0),
                        source_url=item.get("source_url", item.get("file_url", None)),
                    )
                )

            if max_count and len(docs) >= max_count:
                break

            # Check if there are more pages
            if total and len(docs) >= total:
                break
            if len(items) < page_size:
                break

            page += 1
    finally:
        client.close()

    context.log.info(f"Total Epstein documents fetched: {len(docs):,}")
    return docs


# ---------------------------------------------------------------------------
# Dagster Assets
# ---------------------------------------------------------------------------


@asset(
    group_name="leaks",
    description="Extract diplomatic cables from WikiLeaks Cablegate archive (archive.org CSV)",
    compute_kind="extract",
    metadata={"layer": "bronze"},
    op_tags={
        "dagster-k8s/config": {
            "container_config": {
                "resources": {
                    "requests": {"cpu": "500m", "memory": "2Gi", "ephemeral-storage": "4Gi"},
                    "limits": {"cpu": "2", "memory": "4Gi", "ephemeral-storage": "8Gi"},
                }
            }
        }
    },
)
def wikileaks_cables(
    context: AssetExecutionContext,
    config: OpenLeaksConfig,
) -> Output[list[Cable]]:
    with trace_operation("wikileaks_cables", tracer, {"code_location": "open_leaks", "layer": "bronze"}):
        logger.info("Starting wikileaks_cables extraction")
        cache = _ensure_cache(config)
        csv_path = cache / "cables.csv"
        _download_file(config.cablegate_csv_url, csv_path, context)
        cables = _parse_cables_csv(csv_path, context, max_count=config.max_cables)
        ASSET_RECORDS_PROCESSED.labels(code_location="open_leaks", asset_key="wikileaks_cables", layer="bronze").inc(len(cables))
        logger.info("wikileaks_cables extraction complete count=%d", len(cables))

        return Output(
            cables,
            metadata={
                "count": len(cables),
                "source_url": config.cablegate_csv_url,
                "sample_subjects": MetadataValue.json([c.subject[:100] for c in cables[:5]]),
            },
        )


@asset(
    group_name="leaks",
    description="Extract offshore entities from ICIJ databases (Panama/Paradise/Pandora Papers)",
    compute_kind="extract",
    metadata={"layer": "bronze"},
    op_tags={
        "dagster-k8s/config": {
            "container_config": {
                "resources": {
                    "requests": {"cpu": "500m", "memory": "2Gi"},
                    "limits": {"cpu": "2", "memory": "4Gi"},
                }
            }
        }
    },
)
def icij_offshore_entities(
    context: AssetExecutionContext,
    config: OpenLeaksConfig,
) -> Output[list[OffshoreEntity]]:
    with trace_operation("icij_offshore_entities", tracer, {"code_location": "open_leaks", "layer": "bronze"}):
        logger.info("Starting icij_offshore_entities extraction")
        cache = _ensure_cache(config)
        zip_path = cache / "icij-offshoreleaks.zip"
        _download_file(config.icij_bulk_url, zip_path, context)
        entities = _parse_icij_entities_from_zip(zip_path, context, max_count=config.max_icij_entities)
        ASSET_RECORDS_PROCESSED.labels(code_location="open_leaks", asset_key="icij_offshore_entities", layer="bronze").inc(len(entities))
        logger.info("icij_offshore_entities extraction complete count=%d", len(entities))

        datasets = {}
        for e in entities:
            ds = e.source_dataset or "unknown"
            datasets[ds] = datasets.get(ds, 0) + 1

        return Output(
            entities,
            metadata={
                "count": len(entities),
                "by_dataset": MetadataValue.json(datasets),
                "sample_names": MetadataValue.json([e.name for e in entities[:5]]),
            },
        )


@asset(
    group_name="leaks",
    description="Extract offshore relationships from ICIJ databases (edge data)",
    compute_kind="extract",
    metadata={"layer": "bronze"},
    op_tags={
        "dagster-k8s/config": {
            "container_config": {
                "resources": {
                    "requests": {"cpu": "500m", "memory": "2Gi"},
                    "limits": {"cpu": "2", "memory": "4Gi"},
                }
            }
        }
    },
)
def icij_offshore_relationships(
    context: AssetExecutionContext,
    config: OpenLeaksConfig,
) -> Output[list[OffshoreRelationship]]:
    with trace_operation("icij_offshore_relationships", tracer, {"code_location": "open_leaks", "layer": "bronze"}):
        logger.info("Starting icij_offshore_relationships extraction")
        cache = _ensure_cache(config)
        zip_path = cache / "icij-offshoreleaks.zip"
        _download_file(config.icij_bulk_url, zip_path, context)
        rels = _parse_icij_relationships_from_zip(zip_path, context, max_count=config.max_icij_relationships)
        ASSET_RECORDS_PROCESSED.labels(code_location="open_leaks", asset_key="icij_offshore_relationships", layer="bronze").inc(len(rels))
        logger.info("icij_offshore_relationships extraction complete count=%d", len(rels))

        rel_types = {}
        for r in rels:
            rt = r.rel_type or "unknown"
            rel_types[rt] = rel_types.get(rt, 0) + 1

        return Output(
            rels,
            metadata={
                "count": len(rels),
                "by_rel_type": MetadataValue.json(rel_types),
            },
        )


@asset(
    group_name="leaks",
    description="Extract court documents from Epstein case files (public API)",
    compute_kind="extract",
    metadata={"layer": "bronze"},
    op_tags={
        "dagster-k8s/config": {
            "container_config": {
                "resources": {
                    "requests": {"cpu": "250m", "memory": "512Mi"},
                    "limits": {"cpu": "1", "memory": "1Gi"},
                }
            }
        }
    },
)
def epstein_court_docs(
    context: AssetExecutionContext,
    config: OpenLeaksConfig,
) -> Output[list[CourtDocument]]:
    with trace_operation("epstein_court_docs", tracer, {"code_location": "open_leaks", "layer": "bronze"}):
        logger.info("Starting epstein_court_docs extraction")
        docs = _fetch_epstein_api(config.epstein_api_url, context, max_count=config.max_epstein_docs)
        ASSET_RECORDS_PROCESSED.labels(code_location="open_leaks", asset_key="epstein_court_docs", layer="bronze").inc(len(docs))
        logger.info("epstein_court_docs extraction complete count=%d", len(docs))

        doc_types = {}
        for d in docs:
            dt = d.document_type or "unknown"
            doc_types[dt] = doc_types.get(dt, 0) + 1

        return Output(
            docs,
            metadata={
                "count": len(docs),
                "by_type": MetadataValue.json(doc_types),
                "sample_titles": MetadataValue.json([d.title[:100] for d in docs[:5] if d.title]),
            },
        )
