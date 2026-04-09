# Media Viewer QA Test Matrix

Generated: 2026-04-09
Status: 84 tests | 34 PASS | 10 FAIL | 37 NOT_TESTED | 3 NOT_IMPLEMENTED

## Bugs Filed

| Bead | Title | Priority | Status |
|------|-------|----------|--------|
| CD-363 | Annotation & Speaker endpoints not registered in deployed viewer API | P1 | Open |
| CD-4ly | Viewer media streaming returns 404 in local dev port-forward mode | P2 | Open |
| CD-evt | Transcription segments missing word-level data — karaoke highlighting inert | P3 | Open |

## Test Matrix

| Test ID | Category | Test Name | Description | API/UI | Status | Playwright |
|---------|----------|-----------|-------------|--------|--------|------------|
| H-01 | Health & Config | Health endpoint | `GET /viewer/health` returns `{"status":"ok"}` | API | PASS | Yes |
| H-02 | Health & Config | OpenAPI spec | `GET /viewer/openapi.json` returns valid JSON | API | PASS | No |
| H-03 | Health & Config | Swagger UI | `GET /viewer/docs` returns Swagger HTML | API | PASS | Yes |
| H-04 | Health & Config | CORS headers | Dev origins get CORS headers | API | PASS | No |
| D-01 | Document CRUD | List documents | `GET /viewer/api/documents` returns 15 docs | API | PASS | Yes |
| D-02 | Document CRUD | Required fields | id, title, source, source_path, metadata present | API | PASS | No |
| D-03 | Document CRUD | Media URL enriched | Each doc has `/viewer/media/...` URL | API | PASS | No |
| D-04 | Document CRUD | Get single doc | `GET /viewer/api/documents/{id}` returns 200 | API | PASS | Yes |
| D-05 | Document CRUD | Nonexistent doc | `GET /viewer/api/documents/fake-id` returns 404 | API | PASS | Yes |
| D-06 | Document CRUD | Metadata structure | extension, size_bytes, has_audio, has_video, duration | API | PASS | No |
| D-07 | Document CRUD | Unicode doc IDs | Docs with unicode chars fetchable via URL encoding | API | PASS | Yes |
| T-01 | Transcription | Get transcription | `GET /viewer/api/documents/{id}/transcription` returns data | API | PASS | Yes |
| T-02 | Transcription | Structure | document_id, title, text, language, segments, duration_s | API | PASS | No |
| T-03 | Transcription | Segment timestamps | Each segment has start, end, text | API | PASS | No |
| T-04 | Transcription | Nonexistent doc | Returns 404 | API | PASS | Yes |
| T-05 | Transcription | Word-level timestamps | Segments should contain words[] | API | **FAIL** | Yes |
| DR-01 | Diarization | Get diarization | Returns speaker-attributed data | API | PASS | Yes |
| DR-02 | Diarization | Has speakers | speakers[] and speaker_count present | API | PASS | No |
| DR-03 | Diarization | Speaker labels | Segments have speaker field | API | PASS | No |
| DR-04 | Diarization | Nonexistent doc | Returns 404 | API | PASS | Yes |
| M-01 | Mentions | Get mentions | Returns array of mentions | API | PASS | Yes |
| M-02 | Mentions | Structure | mention_id, text, mention_type, context, spans | API | PASS | No |
| M-03 | Mentions | Multiple types | PERSON, ORG, GPE, LOC, DATE, EVENT, MONEY | API | PASS | No |
| M-04 | Mentions | Empty graceful | Returns [] for docs without NER | API | PASS | Yes |
| A-01 | Assertions | Get assertions | Returns array of assertions | API | PASS | Yes |
| A-02 | Assertions | Structure | subject, predicate, object, confidence, negated, hedged | API | PASS | No |
| A-03 | Assertions | Confidence range | All values 0-1 | API | PASS | No |
| A-04 | Assertions | Empty graceful | Returns [] for docs without assertions | API | PASS | Yes |
| AN-01 | Annotations | List annotations | `GET /documents/{id}/annotations` | API | **FAIL** (not deployed) | Yes |
| AN-02 | Annotations | Create annotation | `POST /documents/{id}/annotations` | API | **FAIL** (not deployed) | Yes |
| AN-03 | Annotations | Update annotation | `PATCH /annotations/{id}` | API | **FAIL** (not deployed) | Yes |
| AN-04 | Annotations | Delete annotation | `DELETE /annotations/{id}` | API | **FAIL** (not deployed) | Yes |
| AN-05 | Annotations | Bulk create | `POST /documents/{id}/annotations/bulk` | API | **FAIL** (not deployed) | Yes |
| AN-06 | Annotations | Validation | Invalid target_type/action returns 422 | API | **FAIL** (not deployed) | Yes |
| AN-07 | Annotations | PG unavailable | Returns [] for list, 503 for writes | API | **FAIL** (not deployed) | Yes |
| SP-01 | Speaker Mappings | Get mappings | `GET /documents/{id}/speakers` | API | **FAIL** (not deployed) | Yes |
| SP-02 | Speaker Mappings | Update mappings | `PATCH /documents/{id}/speakers` | API | **FAIL** (not deployed) | Yes |
| MS-01 | Media Streaming | Stream file | Returns file with correct MIME | API | **FAIL** (NFS not local) | Yes (cluster) |
| MS-02 | Media Streaming | Range requests | 206 with Content-Range | API | **FAIL** (NFS not local) | Yes (cluster) |
| MS-03 | Media Streaming | Invalid source | Unknown source returns 400 | API | NOT_TESTED | Yes |
| MS-04 | Media Streaming | Path traversal blocked | `../` returns 403 | API | NOT_TESTED | Yes |
| MS-05 | Media Streaming | Bad extension | `.exe` returns 400 | API | NOT_TESTED | Yes |
| MS-06 | Media Streaming | Accept-Ranges header | Full response includes Accept-Ranges: bytes | API | NOT_TESTED | Yes |
| UL-01 | Document List | Page loads | Shows all 15 documents | UI | PASS | Yes |
| UL-02 | Document List | Grid view | Cards with thumbnail, title, badges | UI | PASS | Yes |
| UL-03 | Document List | List view | Rows with icon, title, metadata | UI | PASS | Yes |
| UL-04 | Document List | View toggle | Grid/List toggle works | UI | PASS | Yes |
| UL-05 | Document List | Loading state | Spinner during load | UI | PASS | Yes |
| UL-06 | Document List | Error state | Red banner on API failure | UI | NOT_TESTED | Yes |
| UL-07 | Document List | Empty state | "No documents found" message | UI | NOT_TESTED | Yes |
| UL-08 | Document List | Card links to player | Click navigates to player | UI | PASS | Yes |
| SF-01 | Search & Filter | Main search | Filters by title | UI | NOT_TESTED | Yes |
| SF-02 | Search & Filter | Sidebar search | Also filters | UI | NOT_TESTED | Yes |
| SF-03 | Search & Filter | Source filter pills | metube pill filters | UI | NOT_TESTED | Yes |
| SF-04 | Search & Filter | Source dropdown | Sidebar dropdown filters | UI | NOT_TESTED | Yes |
| SF-05 | Search & Filter | Sort A-Z | Default alphabetical | UI | PASS | Yes |
| SF-06 | Search & Filter | Sort by duration | Reorders by length | UI | NOT_TESTED | Yes |
| SF-07 | Search & Filter | Sort by source | Reorders by source | UI | NOT_TESTED | Yes |
| SF-08 | Search & Filter | No results | Shows message | UI | NOT_TESTED | Yes |
| SF-09 | Search & Filter | Filter count | Footer updates | UI | NOT_TESTED | Yes |
| PL-01 | Player Layout | Header metadata | Title, source, duration shown | UI | PASS | Yes |
| PL-02 | Player Layout | Back button | Returns to list | UI | PASS | Yes |
| PL-03 | Player Layout | Two-column layout | Player+timeline left, transcript right | UI | PASS | Yes |
| PL-04 | Player Layout | Loading spinner | During data fetch | UI | PASS | Yes |
| PL-05 | Player Layout | Error state | Error with back link | UI | NOT_TESTED | Yes |
| VP-01 | Video Playback | Video renders | `<video>` element for video docs | UI | PASS | Yes |
| VP-02 | Video Playback | Audio renders | `<audio>` for audio-only | UI | NOT_TESTED | Yes |
| VP-03 | Video Playback | Media URL correct | `/viewer/media/{source}/{path}` | UI | PASS | Yes |
| VP-04 | Video Playback | Play/Pause callbacks | Events propagate | UI | NOT_TESTED | Yes (cluster) |
| VP-05 | Video Playback | Time updates | RAF loop sends currentTime | UI | NOT_TESTED | Yes (cluster) |
| ST-01 | Speaker Timeline | Renders segments | Colored blocks per speaker | UI | PASS | Yes |
| ST-02 | Speaker Timeline | Playhead updates | White line moves | UI | PASS | Yes |
| ST-03 | Speaker Timeline | Click to seek | Proportional seeking | UI | NOT_TESTED | Yes |
| ST-04 | Speaker Timeline | Keyboard seek | Arrow keys ±5s | UI | NOT_TESTED | Yes |
| ST-05 | Speaker Timeline | Speaker legend | Color labels below | UI | PASS | Yes |
| ST-06 | Speaker Timeline | Time markers | Current + duration | UI | PASS | Yes |
| TS-01 | Transcript | Speaker labels | Name + timestamp shown | UI | PASS | Yes |
| TS-02 | Transcript | Speaker colors | Left border matches timeline | UI | PASS | Yes |
| TS-03 | Transcript | Click to seek | Segment click seeks | UI | NOT_TESTED | Yes |
| TS-04 | Transcript | Active highlight | Current segment highlighted | UI | NOT_TESTED | Yes |
| TS-05 | Transcript | Auto-scroll | Scrolls to active segment | UI | NOT_TESTED | Yes |
| TS-06 | Transcript | Keyboard a11y | Focusable, Enter/Space seeks | UI | PASS | Yes |
| TS-07 | Transcript | Word highlighting | Karaoke effect on active word | UI | NOT_TESTED (no data) | Yes |
| TS-08 | Transcript | Entity highlighting | Click entity highlights in transcript | UI | NOT_TESTED | Yes |
| TS-09 | Transcript | Empty fallback | "No transcript" message | UI | NOT_TESTED | Yes |
| EP-01 | Entity Panel | Types grouped | Mentions grouped by type | UI | PASS | Yes |
| EP-02 | Entity Panel | Group counts | Unique + total badges | UI | PASS | Yes |
| EP-03 | Entity Panel | Expand/collapse | Toggle entity list | UI | NOT_TESTED | Yes |
| EP-04 | Entity Panel | Click highlights | Sets highlightText | UI | NOT_TESTED | Yes |
| EP-05 | Entity Panel | Deduplication | Same text merged with count | UI | PASS | Yes |
| EP-06 | Entity Panel | Type colors | Distinct colors per type | UI | PASS | Yes |
| EP-07 | Entity Panel | Empty state | "No entities" message | UI | NOT_TESTED | Yes |
| AP-01 | Assertion Panel | Table renders | Subject, Predicate, Object, Conf columns | UI | PASS | Yes |
| AP-02 | Assertion Panel | Sort by confidence | Click header sorts | UI | NOT_TESTED | Yes |
| AP-03 | Assertion Panel | Sort by subject | Click header sorts | UI | NOT_TESTED | Yes |
| AP-04 | Assertion Panel | Sort by predicate | Click header sorts | UI | NOT_TESTED | Yes |
| AP-05 | Assertion Panel | Sort toggle | Same column toggles asc/desc | UI | NOT_TESTED | Yes |
| AP-06 | Assertion Panel | Filter | Text filter by subject/predicate/object | UI | NOT_TESTED | Yes |
| AP-07 | Assertion Panel | NEG badge | Red badge on negated | UI | PASS | Yes |
| AP-08 | Assertion Panel | HEDGED badge | Yellow badge on hedged | UI | NOT_TESTED | Yes |
| AP-09 | Assertion Panel | Qualifier expansion | Click expands qualifiers | UI | NOT_TESTED | Yes |
| AP-10 | Assertion Panel | Confidence colors | Green/Amber/Red thresholds | UI | PASS | Yes |
| AP-11 | Assertion Panel | Filter count | "X of Y" footer | UI | NOT_TESTED | Yes |
| AP-12 | Assertion Panel | Empty state | "No assertions" message | UI | NOT_TESTED | Yes |
| SB-01 | Speaker Breakdown | Stacked bar | Horizontal proportional bar | UI | PASS | Yes |
| SB-02 | Speaker Breakdown | Per-speaker stats | Time, %, segment count | UI | PASS | Yes |
| SB-03 | Speaker Breakdown | Progress bars | Individual per speaker | UI | PASS | Yes |
| SB-04 | Speaker Breakdown | Color consistency | Matches timeline + transcript | UI | PASS | Yes |
| SB-05 | Speaker Breakdown | Empty state | "No speaker data" message | UI | NOT_TESTED | Yes |
| NV-01 | Navigation | Sidebar docs | 15 documents listed | UI | PASS | Yes |
| NV-02 | Navigation | Active highlighted | Blue left border on current | UI | PASS | Yes |
| NV-03 | Navigation | Collapse/expand | Toggle to thin bar | UI | PASS | Yes |
| NV-04 | Navigation | Switch docs | Click sidebar navigates | UI | NOT_TESTED | Yes |
| NV-05 | Navigation | Browser history | Back/forward works | UI | NOT_TESTED | Yes |
| NV-06 | Navigation | Direct URL | `/viewer/player/{id}` loads | UI | NOT_TESTED | Yes |
| NV-07 | Navigation | Tab switching | Entities/Assertions tabs | UI | PASS | Yes |
| NV-08 | Navigation | Tab badges | Count badges shown | UI | PASS | Yes |
| KB-01 | Keyboard | Timeline arrows | Left/Right ±5s | UI | NOT_TESTED | Yes |
| KB-02 | Keyboard | Segment Enter/Space | Seeks on key press | UI | NOT_TESTED | Yes |
| AC-01 | Annotations UI | No UI exists | Types defined, no components | UI | NOT_IMPL | Yes |
| AC-02 | Annotations UI | Approve/Reject/Edit | Action buttons per item | UI | NOT_IMPL | Yes |
| AC-03 | Annotations UI | Speaker naming | Rename SPEAKER_00 | UI | NOT_IMPL | Yes |

## Notes

- Media streaming tests (MS-*) require in-cluster execution (NFS mounts)
- Video playback tests (VP-04, VP-05) require media files accessible
- Word-level tests (TS-07) blocked by CD-evt (OpenVINO backend doesn't output words[])
- Annotation tests (AN-*, SP-*, AC-*) blocked by CD-363 (deploy) and frontend implementation
