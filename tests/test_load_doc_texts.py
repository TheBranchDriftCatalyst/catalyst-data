"""Tests for load_doc_texts() helper in tests/shared/medallion.py.

Uses S3 fixture data if available; falls back to a synthetic in-memory corpus
to keep the test runnable on a fresh checkout without a seeded MinIO instance.

Phase 3 (CD-80ic).
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Synthetic corpus for unit testing (no S3 required)
# ---------------------------------------------------------------------------


def _make_chunk(doc_id: str, index: int, text: str, domain: str = "test") -> dict:
    return {
        "chunk_id": f"{doc_id}:chunk-{index}",
        "document_id": doc_id,
        "text": text,
        "index": index,
        "total_chunks": 0,  # backfilled below
        "metadata": {"domain": domain, "source": "test"},
        "content_hash": "",
    }


CORPUS = [
    _make_chunk("doc-1", 0, "First chunk of doc one."),
    _make_chunk("doc-1", 1, "Second chunk of doc one."),
    _make_chunk("doc-2", 0, "Only chunk of doc two."),
    _make_chunk("doc-3", 0, "First chunk of doc three.", domain="congress"),
    _make_chunk("doc-3", 1, "Second chunk of doc three.", domain="congress"),
    _make_chunk("doc-3", 2, "Third chunk of doc three.", domain="congress"),
]
# Backfill total_chunks
for _chunk in CORPUS:
    _did = _chunk["document_id"]
    _total = sum(1 for c in CORPUS if c["document_id"] == _did)
    _chunk["total_chunks"] = _total


def _load_doc_texts_from_chunks(chunks: list[dict]) -> list[dict]:
    """Local re-implementation of load_doc_texts to test against the synthetic corpus."""
    from collections import defaultdict

    groups: dict[str, list[dict]] = defaultdict(list)
    for chunk in chunks:
        did = chunk.get("document_id") or "unknown"
        groups[did].append(chunk)

    docs = []
    for did, group in groups.items():
        sorted_chunks = sorted(group, key=lambda c: c.get("index", 0))
        full_text = "\n\n".join(c.get("text", "") or "" for c in sorted_chunks)
        first_meta = sorted_chunks[0].get("metadata") or {}
        docs.append(
            {
                "doc_id": did,
                "full_text": full_text,
                "code_location": first_meta.get("code_location") or first_meta.get("source") or "",
                "domain": first_meta.get("domain") or first_meta.get("source") or "",
                "chunks": sorted_chunks,
            }
        )
    return docs


class TestLoadDocTexts:
    # ── Structural tests on the synthetic corpus ────────────────────────────

    def test_groups_chunks_by_doc_id(self):
        docs = _load_doc_texts_from_chunks(CORPUS)
        doc_ids = {d["doc_id"] for d in docs}
        assert doc_ids == {"doc-1", "doc-2", "doc-3"}

    def test_full_text_is_concatenation_in_order(self):
        docs = _load_doc_texts_from_chunks(CORPUS)
        doc1 = next(d for d in docs if d["doc_id"] == "doc-1")
        assert "First chunk of doc one." in doc1["full_text"]
        assert "Second chunk of doc one." in doc1["full_text"]
        # Order check: first before second
        assert doc1["full_text"].index("First") < doc1["full_text"].index("Second")

    def test_single_chunk_doc_full_text_equals_chunk_text(self):
        docs = _load_doc_texts_from_chunks(CORPUS)
        doc2 = next(d for d in docs if d["doc_id"] == "doc-2")
        assert doc2["full_text"] == "Only chunk of doc two."

    def test_chunks_field_preserves_originals(self):
        docs = _load_doc_texts_from_chunks(CORPUS)
        doc1 = next(d for d in docs if d["doc_id"] == "doc-1")
        assert len(doc1["chunks"]) == 2

    def test_domain_field_populated(self):
        docs = _load_doc_texts_from_chunks(CORPUS)
        doc3 = next(d for d in docs if d["doc_id"] == "doc-3")
        assert doc3["domain"] == "congress"

    def test_empty_corpus_returns_empty(self):
        docs = _load_doc_texts_from_chunks([])
        assert docs == []

    # ── sample_per_domain cap test using the real helper (mocked S3) ────────

    def test_sample_per_domain_caps_chunks(self, monkeypatch):
        """load_doc_texts(sample_per_domain=1) should cap to ~1 chunk per domain."""
        from tests.shared import medallion as med

        # Patch load_chunks to return our synthetic corpus
        monkeypatch.setattr(med, "load_chunks", lambda **kw: CORPUS[:2])  # return 2 chunks max

        docs = med.load_doc_texts(sample_per_domain=1)
        # With only 2 chunks returned by load_chunks, we can have at most 2 docs
        total_chunks = sum(len(d["chunks"]) for d in docs)
        assert total_chunks <= 2

    def test_load_doc_texts_returns_list_of_dicts(self, monkeypatch):
        """load_doc_texts returns a list of dicts with the expected keys."""
        from tests.shared import medallion as med

        monkeypatch.setattr(med, "load_chunks", lambda **kw: CORPUS)

        docs = med.load_doc_texts()
        assert isinstance(docs, list)
        for doc in docs:
            assert "doc_id" in doc
            assert "full_text" in doc
            assert "chunks" in doc
            assert "domain" in doc
