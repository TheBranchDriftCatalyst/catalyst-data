"""Bronze: Raw data extraction from open-source leaked document archives.

Downloads and parses:
- ICIJ Offshore Leaks Database (CSV bulk download)
- WikiLeaks Cablegate cables (CSV from archive.org)
- Epstein court documents (API from epsteininvestigation.org)
"""

import csv
import io
import zipfile
from pathlib import Path

import httpx
from dagster import AssetExecutionContext, MetadataValue, Output, asset

from open_leaks.config import OpenLeaksConfig
from open_leaks.entities import Cable, CourtDocument, OffshoreEntity, OffshoreRelationship

# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------

_HTTP_TIMEOUT = httpx.Timeout(connect=30, read=300, write=30, pool=30)


def _ensure_cache(config: OpenLeaksConfig) -> Path:
    cache = Path(config.cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    return cache


def _download_file(
    url: str,
    dest: Path,
    context: AssetExecutionContext,
) -> Path:
    """Stream-download a file with progress logging. Skips if cached."""
    if dest.exists() and dest.stat().st_size > 0:
        context.log.info(f"Using cached file: {dest} ({dest.stat().st_size / 1024 / 1024:.1f} MB)")
        return dest

    context.log.info(f"Downloading {url} → {dest}")
    with httpx.stream("GET", url, follow_redirects=True, timeout=_HTTP_TIMEOUT) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        downloaded = 0
        with open(dest, "wb") as f:
            for chunk in r.iter_bytes(chunk_size=65536):
                f.write(chunk)
                downloaded += len(chunk)
                if total and downloaded % (50 * 1024 * 1024) < 65536:
                    pct = downloaded / total * 100
                    context.log.info(
                        f"  {downloaded / 1024 / 1024:.1f} / {total / 1024 / 1024:.1f} MB ({pct:.0f}%)"
                    )

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


def _fetch_epstein_api(
    api_base: str,
    context: AssetExecutionContext,
    max_count: int = 0,
) -> list[CourtDocument]:
    """Fetch documents from epsteininvestigation.org paginated API.

    Response format: {"data": [...], "total": N, "page": N, "limit": N}
    Document fields: id, slug, title, document_type, source, document_date,
                     excerpt, page_count, file_url, source_url
    """
    docs: list[CourtDocument] = []
    page = 1
    page_size = 100
    client = httpx.Client(timeout=_HTTP_TIMEOUT, follow_redirects=True)

    try:
        while True:
            url = f"{api_base}/documents?page={page}&limit={page_size}"
            if page == 1 or page % 50 == 0:
                context.log.info(f"Fetching page {page} ({len(docs):,} docs so far)")

            try:
                resp = client.get(url)
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                context.log.warning(f"API returned {e.response.status_code} — stopping pagination")
                break
            except httpx.ConnectError:
                context.log.error(f"Cannot connect to {api_base} — API may be unavailable")
                break

            payload = resp.json()
            items = payload.get("data", []) if isinstance(payload, dict) else payload
            total = payload.get("total", 0) if isinstance(payload, dict) else 0

            if not items:
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
                        date_filed=item.get("document_date", ""),
                        content=item.get("excerpt", ""),
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
)
def wikileaks_cables(
    context: AssetExecutionContext,
    config: OpenLeaksConfig,
) -> Output[list[Cable]]:
    cache = _ensure_cache(config)
    csv_path = cache / "cables.csv"
    _download_file(config.cablegate_csv_url, csv_path, context)
    cables = _parse_cables_csv(csv_path, context, max_count=config.max_cables)

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
)
def icij_offshore_entities(
    context: AssetExecutionContext,
    config: OpenLeaksConfig,
) -> Output[list[OffshoreEntity]]:
    cache = _ensure_cache(config)
    zip_path = cache / "icij-offshoreleaks.zip"
    _download_file(config.icij_bulk_url, zip_path, context)
    entities = _parse_icij_entities_from_zip(zip_path, context, max_count=config.max_icij_entities)

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
)
def icij_offshore_relationships(
    context: AssetExecutionContext,
    config: OpenLeaksConfig,
) -> Output[list[OffshoreRelationship]]:
    cache = _ensure_cache(config)
    zip_path = cache / "icij-offshoreleaks.zip"
    _download_file(config.icij_bulk_url, zip_path, context)
    rels = _parse_icij_relationships_from_zip(zip_path, context, max_count=config.max_icij_relationships)

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
)
def epstein_court_docs(
    context: AssetExecutionContext,
    config: OpenLeaksConfig,
) -> Output[list[CourtDocument]]:
    docs = _fetch_epstein_api(config.epstein_api_url, context, max_count=config.max_epstein_docs)

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
