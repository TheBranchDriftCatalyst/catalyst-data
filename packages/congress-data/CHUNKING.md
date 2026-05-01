# Chunking Strategy

Section-aware chunking that preserves legislative structure.

## Pipeline

```
bill_text_versions (API)
  -> bill_full_text (download HTM + XML from congress.gov CDN)
    -> bill_document (transform to Document)
      -> bill_chunks (section-aware chunking)
```

## Two Parsing Modes

### A. XML parsing (preferred)

When GPO Formatted XML is available (most bills have it), the chunker parses
the XML tree directly. This provides exact structural boundaries:

- `<section>` with `<enum>` and `<header>` children
- `<subsection>` with `(a)`, `(b)` markers
- `<paragraph>`, `<subparagraph>` for deeper nesting
- `<quoted-block>` for inline amendment text
- `<title>`, `<subtitle>`, `<chapter>`, `<part>` for container hierarchy

<!-- TODO: might send this through another llm in order to be a BilLReaderGraph... 
 that could help downstream extraction cause we can progressively summarize and do stuff -->

### B. Plain text regex (fallback)

When XML is not available, the chunker falls back to regex-based parsing of
the plain text, detecting `SECTION/SEC.` and `TITLE/CHAPTER/PART` headers.

## Three Strategies

### 1. `section_split` -- whole sections kept intact

When a section is **under 4000 characters**, it becomes a single chunk.
Most bill sections (definitions, short provisions, effective dates) fit here.

- **Provenance**: section number + title from XML `<enum>`/`<header>` or regex match
- **When**: short-to-medium sections (the majority of bill sections)

### 2. `subsection_split` -- split at (a), (b), (c) boundaries

When a section exceeds 4000 chars, split at **subsection boundaries**.
In XML mode, these are explicit `<subsection>` elements. In regex mode,
the pattern `^\s*\([a-z]\)\s` is matched.

Adjacent small subsections are grouped together up to MAX_CHUNK_CHARS to
avoid creating many tiny chunks.

- **Provenance**: section number + subsection labels (e.g. "a,b,c")
- **When**: long sections with multiple subsections (common for substantive provisions)

### 3. `text_split_fallback` -- last resort text splitter

If after subsection splitting, a sub-chunk is **still over 4000 chars**
(giant quoted amendment text, dense statutory language), fall back to
`RecursiveCharacterTextSplitter` with 2000-char chunks, 200 overlap.

- **Provenance**: section number preserved, chunk is positional
- **When**: rare -- only for massive quoted blocks or amendment text

### Preamble

Everything before the first SECTION header (bill number, enacting clause,
committee referral, official title) becomes its own chunk with
`strategy="preamble"`. In XML mode, this comes from the `<form>` element.

## Chunk Metadata

Every chunk carries:

| Field | Description |
|-------|-------------|
| `strategy` | Which strategy produced this chunk |
| `parse_mode` | `xml` or `regex` |
| `section_number` | Section number (e.g. "101", "10101") |
| `section_title` | Section header text |
| `subsection` | Subsection labels (e.g. "a,b,c") -- subsection_split only |
| `parent_title` | Container title/subtitle -- XML mode only |
| `source` | Always `congress.gov` |
| `document_type` | Always `bill` |

## Key Parameters

- `MAX_CHUNK_CHARS = 4000` -- threshold for section-level chunks
- `MAX_SUBSECTION_CHARS = 4000` -- threshold before text_split_fallback
- `FALLBACK_CHUNK_SIZE = 2000` -- RecursiveCharacterTextSplitter chunk size
- `FALLBACK_CHUNK_OVERLAP = 200` -- overlap for fallback splitter
