"""Section-aware text chunking for congressional bill text.

Splits bill text into chunks that respect legislative structure (sections,
subsections, titles) rather than naive character-count splitting.
See CHUNKING.md for the full strategy overview.

Two parsing modes:
  A. XML parsing (preferred) — uses GPO Formatted XML with explicit
     <section>, <subsection>, <paragraph>, <quoted-block> tags
  B. Plain text regex (fallback) — for bills without XML available

Hierarchy (both modes):
  0. preamble            — enacting clause, committee referral, bill number
  1. section_split       — whole sections kept intact (< MAX_CHUNK_CHARS)
  2. subsection_split    — split oversized sections at subsection boundaries
  3. text_split_fallback — last resort RecursiveCharacterTextSplitter
"""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field

from langchain_text_splitters import RecursiveCharacterTextSplitter

from dagster_io import TextChunk
from dagster_io.chunking import ChunkConfig
from dagster_io.logging import get_logger
from dagster_io.text import normalize_text

logger = get_logger(__name__)

# ── Constants ───────────────────────────────────────────────────────────────

MAX_CHUNK_CHARS = 4000
MAX_SUBSECTION_CHARS = 4000
FALLBACK_CHUNK_SIZE = 2000
FALLBACK_CHUNK_OVERLAP = 200

# ── Section header patterns (plain text fallback) ───────────────────────────

SECTION_RE = re.compile(
    r"^(?:SECTION|SEC\.?)\s+(\d+)\.?\s*(.*?)$",
    re.MULTILINE | re.IGNORECASE,
)

DIVISION_RE = re.compile(
    r"^(TITLE|CHAPTER|PART)\s+([IVXLCDM\d]+[A-Z]?)[\s.—\-]*(.*?)$",
    re.MULTILINE | re.IGNORECASE,
)

SUBSECTION_RE = re.compile(
    r"^\s*\([a-z]\)\s",
    re.MULTILINE,
)


# ── Internal data types ─────────────────────────────────────────────────────


@dataclass
class RawSection:
    """A parsed section from the bill text."""

    text: str
    section_number: str | None = None
    section_title: str | None = None
    header_type: str = "section"  # section, title, chapter, part


@dataclass
class ChunkCandidate:
    """A chunk ready for conversion to TextChunk."""

    text: str
    strategy: str
    metadata: dict = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════════════
# XML PARSING (primary strategy)
# ═══════════════════════════════════════════════════════════════════════════


def _xml_get_text(elem: ET.Element) -> str:
    """Extract all text content from an XML element and its descendants."""
    return "".join(elem.itertext()).strip()


def _xml_get_header(elem: ET.Element) -> tuple[str, str]:
    """Extract (enum, header) from a section/title/subtitle element."""
    enum_el = elem.find("enum")
    header_el = elem.find("header")
    enum_text = (enum_el.text or "").strip() if enum_el is not None else ""
    header_text = (header_el.text or "").strip() if header_el is not None else ""
    return enum_text, header_text


def _xml_subsection_split(
    section_elem: ET.Element,
    section_number: str,
    section_title: str,
    config: ChunkConfig | None = None,
) -> list[ChunkCandidate]:
    """Split an oversized XML section at <subsection> boundaries.

    Groups adjacent small subsections up to MAX_SUBSECTION_CHARS.
    Falls back to text_split_fallback for giant <quoted-block> content.
    """
    max_sub_chars = config.target_chars if config else MAX_SUBSECTION_CHARS
    subsections = section_elem.findall("subsection")
    if not subsections:
        # No subsection children — text_split_fallback
        raw = RawSection(
            text=_xml_get_text(section_elem),
            section_number=section_number,
            section_title=section_title,
        )
        return _text_split_fallback(raw.text, raw, config=config)

    chunks: list[ChunkCandidate] = []
    buffer_text = ""
    buffer_labels: list[str] = []

    # Include section header text (enum + header) before first subsection
    header_parts = []
    enum_el = section_elem.find("enum")
    header_el = section_elem.find("header")
    if enum_el is not None:
        header_parts.append((enum_el.text or "").strip())
    if header_el is not None:
        header_parts.append((header_el.text or "").strip())
    section_header_text = " ".join(p for p in header_parts if p)

    # Also grab any <text> children directly under the section (before subsections)
    preamble_parts = [section_header_text] if section_header_text else []
    for child in section_elem:
        if child.tag == "subsection":
            break
        if child.tag == "text":
            preamble_parts.append(_xml_get_text(child))
    section_preamble = "\n".join(p for p in preamble_parts if p)

    for ss in subsections:
        ss_enum, ss_header = _xml_get_header(ss)
        label = ss_enum.strip("(). ") or "?"
        ss_text = _xml_get_text(ss)

        combined = (buffer_text + "\n\n" + ss_text).strip() if buffer_text else ss_text
        if len(combined) <= max_sub_chars:
            buffer_text = combined
            buffer_labels.append(label)
        else:
            # Flush current buffer
            if buffer_text:
                chunks.append(
                    ChunkCandidate(
                        text=buffer_text,
                        strategy="subsection_split",
                        metadata={
                            "section_number": section_number,
                            "section_title": section_title,
                            "subsection": ",".join(buffer_labels),
                        },
                    )
                )
            if len(ss_text) > max_sub_chars:
                # Individual subsection too large — text_split_fallback
                raw = RawSection(
                    text=ss_text,
                    section_number=section_number,
                    section_title=section_title,
                )
                chunks.extend(_text_split_fallback(raw.text, raw, subsection=label, config=config))
                buffer_text = ""
                buffer_labels = []
            else:
                buffer_text = ss_text
                buffer_labels = [label]

    # Flush remaining buffer
    if buffer_text:
        # Prepend section preamble to first chunk if we have it and this is the first chunk
        final_text = (
            (section_preamble + "\n\n" + buffer_text).strip() if (section_preamble and not chunks) else buffer_text
        )
        chunks.append(
            ChunkCandidate(
                text=final_text,
                strategy="subsection_split",
                metadata={
                    "section_number": section_number,
                    "section_title": section_title,
                    "subsection": ",".join(buffer_labels),
                },
            )
        )
    elif section_preamble and chunks:
        # Prepend preamble to the first chunk that was already flushed
        chunks[0] = ChunkCandidate(
            text=(section_preamble + "\n\n" + chunks[0].text).strip(),
            strategy=chunks[0].strategy,
            metadata=chunks[0].metadata,
        )

    return chunks


def _xml_section_to_chunks(section_elem: ET.Element, config: ChunkConfig | None = None) -> list[ChunkCandidate]:
    """Apply the tiered strategy to an XML <section> element."""
    max_chars = config.target_chars if config else MAX_CHUNK_CHARS
    section_number, section_title = _xml_get_header(section_elem)
    full_text = _xml_get_text(section_elem)

    if len(full_text) <= max_chars:
        return [
            ChunkCandidate(
                text=full_text,
                strategy="section_split",
                metadata={
                    "section_number": section_number or None,
                    "section_title": section_title or None,
                },
            )
        ]

    return _xml_subsection_split(section_elem, section_number, section_title, config=config)


def _parse_xml(xml_content: str, config: ChunkConfig | None = None) -> tuple[str | None, list[ChunkCandidate]]:
    """Parse GPO Formatted XML into preamble + section chunks.

    Returns (preamble_text_or_None, list_of_ChunkCandidate).
    Raises ValueError if XML cannot be parsed.
    """
    root = ET.fromstring(xml_content)

    # Extract preamble from <form> element
    form = root.find("form")
    preamble = _xml_get_text(form) if form is not None else None
    preamble = preamble if preamble else None

    # Bill XMLs use ``<legis-body>``; resolution XMLs (hres/sres) use
    # ``<resolution-body>``. Both share the same section/title structure
    # downstream, so accept either as the chunkable body.
    legis_body = root.find("legis-body")
    if legis_body is None:
        legis_body = root.find("resolution-body")
    if legis_body is None:
        raise ValueError("No <legis-body> or <resolution-body> element found in bill XML")

    max_chars = config.target_chars if config else MAX_CHUNK_CHARS
    candidates: list[ChunkCandidate] = []

    # Walk direct children of legis-body
    # Structure: legis-body > (section | title > (subtitle > section | section))
    #
    # OMNIBUS BILLS — use ``<division>`` (Division A, Division B, ...) at
    # the top level instead of ``<title>``. Pre-2026 versions of this
    # chunker missed the ``division`` tag entirely; the Consolidated
    # Appropriations Act 2023 (117-hr-2617) chunked to 10 tiny
    # boilerplate sections instead of the 1000+ inside each division.
    # Treating ``division`` as a container alongside title/subtitle/
    # chapter/part fixes that.
    for child in legis_body:
        tag = child.tag

        if tag == "section":
            candidates.extend(_xml_section_to_chunks(child, config=config))

        elif tag in ("division", "title", "subtitle", "chapter", "part"):
            # These are container elements — process their child sections
            container_enum, container_header = _xml_get_header(child)
            container_label = f"{tag.upper()} {container_enum}: {container_header}"

            # Check if this container has direct section children
            has_sections = any(c.tag in ("section", "division", "title", "subtitle", "chapter", "part") for c in child)

            if has_sections:
                # Process each child section/sub-container recursively
                for subchild in child:
                    if subchild.tag == "section":
                        section_chunks = _xml_section_to_chunks(subchild, config=config)
                        # Tag with parent container info
                        for c in section_chunks:
                            c.metadata["parent_title"] = container_label
                        candidates.extend(section_chunks)
                    elif subchild.tag in ("division", "title", "subtitle", "chapter", "part"):
                        # Recurse into nested containers. ``.//section``
                        # is an XPath descendant query, so it picks up
                        # sections at any depth under this container
                        # (handles division > title > subtitle > section).
                        sub_enum, sub_header = _xml_get_header(subchild)
                        sub_label = f"{subchild.tag.upper()} {sub_enum}: {sub_header}"
                        for subsec in subchild.findall(".//section"):
                            section_chunks = _xml_section_to_chunks(subsec, config=config)
                            for c in section_chunks:
                                c.metadata["parent_title"] = f"{container_label} > {sub_label}"
                            candidates.extend(section_chunks)
            else:
                # Container with no section children — treat as a section itself
                full_text = _xml_get_text(child)
                if full_text:
                    if len(full_text) <= max_chars:
                        candidates.append(
                            ChunkCandidate(
                                text=full_text,
                                strategy="section_split",
                                metadata={
                                    "section_number": container_enum or None,
                                    "section_title": container_header or None,
                                    "header_type": tag,
                                },
                            )
                        )
                    else:
                        raw = RawSection(
                            text=full_text,
                            section_number=container_enum or None,
                            section_title=container_header or None,
                            header_type=tag,
                        )
                        candidates.extend(_text_split_fallback(raw.text, raw, config=config))

    return preamble, candidates


# ═══════════════════════════════════════════════════════════════════════════
# PLAIN TEXT REGEX PARSING (fallback)
# ═══════════════════════════════════════════════════════════════════════════


def _parse_sections_regex(text: str) -> tuple[str | None, list[RawSection]]:
    """Split bill text into preamble + ordered sections using regex.

    Returns (preamble_text_or_None, list_of_RawSection).
    """
    headers: list[tuple[int, str, str, str]] = []

    for m in SECTION_RE.finditer(text):
        headers.append((m.start(), "section", m.group(1), m.group(2).strip()))

    for m in DIVISION_RE.finditer(text):
        headers.append((m.start(), m.group(1).lower(), m.group(2), m.group(3).strip()))

    headers.sort(key=lambda h: h[0])

    if not headers:
        return None, [RawSection(text=text, section_number=None, section_title=None)]

    first_pos = headers[0][0]
    preamble = text[:first_pos].strip() if first_pos > 0 else None

    sections: list[RawSection] = []
    for i, (pos, htype, number, title) in enumerate(headers):
        end = headers[i + 1][0] if i + 1 < len(headers) else len(text)
        section_text = text[pos:end].strip()
        if section_text:
            sections.append(
                RawSection(
                    text=section_text,
                    section_number=number,
                    section_title=title or None,
                    header_type=htype,
                )
            )

    return preamble, sections


def _regex_subsection_split(section: RawSection, config: ChunkConfig | None = None) -> list[ChunkCandidate]:
    """Split an oversized section at (a), (b), (c) subsection boundaries.

    Groups adjacent small subsections together up to MAX_SUBSECTION_CHARS.
    Falls back to text_split_fallback if no subsection markers are found.
    """
    max_sub_chars = config.target_chars if config else MAX_SUBSECTION_CHARS
    text = section.text
    matches = list(SUBSECTION_RE.finditer(text))

    if not matches:
        return _text_split_fallback(text, section, config=config)

    parts: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        part_text = text[start:end].strip()
        label_match = re.match(r"\s*\(([a-z])\)", part_text)
        label = label_match.group(1) if label_match else f"sub_{i}"
        parts.append((label, part_text))

    if matches and matches[0].start() > 0:
        prefix = text[: matches[0].start()].strip()
        if prefix and parts:
            parts[0] = (parts[0][0], prefix + "\n" + parts[0][1])

    chunks: list[ChunkCandidate] = []
    buffer_text = ""
    buffer_labels: list[str] = []

    for label, part_text in parts:
        combined = (buffer_text + "\n" + part_text).strip() if buffer_text else part_text
        if len(combined) <= max_sub_chars:
            buffer_text = combined
            buffer_labels.append(label)
        else:
            if buffer_text:
                chunks.append(
                    ChunkCandidate(
                        text=buffer_text,
                        strategy="subsection_split",
                        metadata={
                            "section_number": section.section_number,
                            "section_title": section.section_title,
                            "subsection": ",".join(buffer_labels),
                        },
                    )
                )
            if len(part_text) > max_sub_chars:
                chunks.extend(_text_split_fallback(part_text, section, subsection=label, config=config))
                buffer_text = ""
                buffer_labels = []
            else:
                buffer_text = part_text
                buffer_labels = [label]

    if buffer_text:
        chunks.append(
            ChunkCandidate(
                text=buffer_text,
                strategy="subsection_split",
                metadata={
                    "section_number": section.section_number,
                    "section_title": section.section_title,
                    "subsection": ",".join(buffer_labels),
                },
            )
        )

    return chunks


def _regex_section_to_chunks(section: RawSection, config: ChunkConfig | None = None) -> list[ChunkCandidate]:
    """Apply the tiered strategy to a regex-parsed section."""
    max_chars = config.target_chars if config else MAX_CHUNK_CHARS
    if len(section.text) <= max_chars:
        return [
            ChunkCandidate(
                text=section.text,
                strategy="section_split",
                metadata={
                    "section_number": section.section_number,
                    "section_title": section.section_title,
                },
            )
        ]

    return _regex_subsection_split(section, config=config)


# ═══════════════════════════════════════════════════════════════════════════
# SHARED: text_split_fallback
# ═══════════════════════════════════════════════════════════════════════════


def _text_split_fallback(
    text: str,
    section: RawSection,
    subsection: str | None = None,
    config: ChunkConfig | None = None,
) -> list[ChunkCandidate]:
    """Last-resort recursive character splitter."""
    fb_size = (config.target_chars // 2) if config else FALLBACK_CHUNK_SIZE
    fb_overlap = (config.overlap_tokens * 4) if config else FALLBACK_CHUNK_OVERLAP
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=fb_size,
        chunk_overlap=fb_overlap,
        separators=["\n\n", "\n", ". ", " ", ""],
        length_function=len,
    )
    pieces = splitter.split_text(text)
    meta: dict = {
        "section_number": section.section_number,
        "section_title": section.section_title,
    }
    if subsection:
        meta["subsection"] = subsection

    return [ChunkCandidate(text=piece, strategy="text_split_fallback", metadata=dict(meta)) for piece in pieces]


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════


def chunk_bill_text(
    document_id: str,
    title: str,
    text: str,
    bill_metadata: dict | None = None,
    xml_content: str | None = None,
    config: ChunkConfig | None = None,
) -> list[TextChunk]:
    """Section-aware chunking for congressional bill text.

    Applies a three-tier strategy that preserves legislative structure:
      1. section_split       — whole sections kept intact (< target_chars)
      2. subsection_split    — oversized sections split at subsection boundaries
      3. text_split_fallback — recursive char split for giant blocks

    When ``xml_content`` is provided, parses the GPO Formatted XML for
    precise structural boundaries.  Falls back to plain text regex parsing
    when XML is not available.

    When ``config`` is provided, chunk size thresholds are derived from the
    model's context window via ChunkConfig.  Otherwise the module-level
    defaults (4000 chars / 2000 fallback) are used.

    Args:
        document_id: Parent document ID (e.g. "congress-bill-hr1-119").
        title: Bill title (prepended to each chunk).
        text: Full bill text content (plain text).
        bill_metadata: Extra metadata to attach to every chunk.
        xml_content: GPO Formatted XML content (preferred over plain text).
        config: Optional ChunkConfig for model-aware sizing.

    Returns:
        List of TextChunk objects ready for embedding/extraction.
    """
    if not text and not xml_content:
        return []

    # Normalize unicode
    title = normalize_text(title) if title else title
    if text:
        text = normalize_text(text)

    base_meta = {
        **(bill_metadata or {}),
        "source": "congress.gov",
        "document_type": "bill",
    }

    candidates: list[ChunkCandidate] = []
    parse_mode = "regex"

    # ── Try XML parsing first ──────────────────────────────────────────────
    if xml_content:
        try:
            preamble, xml_candidates = _parse_xml(xml_content, config=config)
            parse_mode = "xml"

            if preamble:
                candidates.append(
                    ChunkCandidate(
                        text=preamble,
                        strategy="preamble",
                        metadata={"section_number": None, "section_title": "preamble"},
                    )
                )
            candidates.extend(xml_candidates)
            logger.info(
                "Parsed bill %s via XML: %d section candidates",
                document_id,
                len(xml_candidates),
            )
        except Exception:
            logger.warning(
                "XML parsing failed for %s, falling back to regex",
                document_id,
                exc_info=True,
            )
            candidates = []
            parse_mode = "regex"

    # ── Fallback to plain text regex ───────────────────────────────────────
    if not candidates and text:
        preamble, sections = _parse_sections_regex(text)

        if preamble:
            candidates.append(
                ChunkCandidate(
                    text=preamble,
                    strategy="preamble",
                    metadata={"section_number": None, "section_title": "preamble"},
                )
            )

        for section in sections:
            candidates.extend(_regex_section_to_chunks(section, config=config))

    if not candidates:
        return []

    # ── Build TextChunk objects ─────────────────────────────────────────────
    total = len(candidates)
    chunks: list[TextChunk] = []

    for i, cand in enumerate(candidates):
        full_text = f"{title}\n\n{cand.text}" if title else cand.text
        chunk_meta = {
            **base_meta,
            "strategy": cand.strategy,
            "parse_mode": parse_mode,
            **cand.metadata,
        }
        chunks.append(
            TextChunk(
                chunk_id=f"{document_id}:chunk-{i}",
                document_id=document_id,
                text=full_text,
                index=i,
                total_chunks=total,
                metadata=chunk_meta,
                content_hash=hashlib.sha256(full_text.encode()).hexdigest(),
            )
        )

    strategies = Counter(c.metadata.get("strategy") for c in chunks)
    avg_len = sum(len(c.text) for c in chunks) / max(len(chunks), 1)
    logger.info(
        "Bill chunker: %s -> %d chunks (mode=%s, strategies=%s, avg=%.0f chars)",
        document_id,
        len(chunks),
        parse_mode,
        dict(strategies),
        avg_len,
    )

    return chunks
