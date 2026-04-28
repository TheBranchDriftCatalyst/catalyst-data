"""Integration tests — run the full congress bill pipeline for a single partition.

Materializes every asset in the bill tail chain using the real Congress.gov API
and (optionally) real LLM calls. Outputs land in tests/congress-pipeline-output/
as readable JSON/JSONL.

Run full chain (requires CONGRESS_API_KEY + LLM_API_KEY):
    CONGRESS_API_KEY=xxx LLM_API_KEY=xxx DAGSTER_CODE_LOCATION=congress_data \
        pytest packages/congress-data/tests/integration/test_pipeline.py -v -s

Run just bronze + silver (no LLM, fast):
    CONGRESS_API_KEY=xxx DAGSTER_CODE_LOCATION=congress_data \
        pytest packages/congress-data/tests/integration/test_pipeline.py -v -s -k "bronze or silver"

Custom partition (bigger bill):
    pytest ... --partition 119-hr-1

Inspect outputs:
    find tests/congress-pipeline-output -name "*.json" -o -name "*.jsonl" | sort
    cat tests/congress-pipeline-output/bronze/congress_data/bill/bill_detail/119-hres-1/data.json | jq .
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from congress_data.assets.bill_tail import (
    bill_actions,
    bill_amendments,
    bill_assertions,
    bill_chunks,
    bill_cosponsors,
    bill_detail,
    bill_document,
    bill_embeddings,
    bill_full_text,
    bill_mentions,
    bill_text_versions,
)
from dagster import materialize

os.environ.setdefault("DAGSTER_CODE_LOCATION", "congress_data")


def _needs_api_key():
    if not os.environ.get("CONGRESS_API_KEY"):
        pytest.skip("CONGRESS_API_KEY not set")


def _needs_llm():
    if not (os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")):
        pytest.skip("LLM_API_KEY not set")


def _read_output(output_dir: Path, prefix: str) -> dict | list | None:
    base = output_dir / prefix
    jsonl = base / "data.jsonl"
    if jsonl.exists():
        with open(jsonl) as f:
            return [json.loads(line) for line in f if line.strip()]
    json_path = base / "data.json"
    if json_path.exists():
        with open(json_path) as f:
            return json.load(f)
    return None


# ═══════════════════════════════════════════════════════════════════════════
# Bronze — API extraction (requires CONGRESS_API_KEY)
# ═══════════════════════════════════════════════════════════════════════════


class TestBronze:
    """bill_detail, bill_actions, bill_cosponsors, bill_text_versions, bill_amendments."""

    def test_bill_detail(self, test_resources, partition_key, output_dir, dagster_instance):
        """Fetch bill detail from Congress.gov API."""
        _needs_api_key()
        result = materialize(
            [bill_detail],
            resources=test_resources,
            partition_key=partition_key,
            instance=dagster_instance,
        )
        assert result.success
        detail = result.output_for_node("bill_detail")
        assert detail.id, "Bill should have an ID"
        assert detail.title, "Bill should have a title"
        print(f"\n  Bill: {detail.id} — {detail.title}")
        print(f"  Sponsor: {detail.sponsor_name} ({detail.sponsor_party}-{detail.sponsor_state})")
        print(f"  Actions: {detail.action_count}, Cosponsors: {detail.cosponsor_count}")

    def test_bill_actions(self, test_resources, partition_key, output_dir, dagster_instance):
        """Fetch bill action history."""
        _needs_api_key()
        result = materialize(
            [bill_actions],
            resources=test_resources,
            partition_key=partition_key,
            instance=dagster_instance,
        )
        assert result.success
        actions = result.output_for_node("bill_actions")
        assert isinstance(actions, list)
        print(f"\n  {len(actions)} actions")
        for a in actions[:3]:
            print(f"    [{a.action_date}] {a.text[:80]}")

    def test_bill_cosponsors(self, test_resources, partition_key, output_dir, dagster_instance):
        """Fetch bill cosponsors."""
        _needs_api_key()
        result = materialize(
            [bill_cosponsors],
            resources=test_resources,
            partition_key=partition_key,
            instance=dagster_instance,
        )
        assert result.success
        cosponsors = result.output_for_node("bill_cosponsors")
        assert isinstance(cosponsors, list)
        print(f"\n  {len(cosponsors)} cosponsors")
        for c in cosponsors[:3]:
            print(f"    {c.name} ({c.party}-{c.state})")

    def test_bill_text_versions(self, test_resources, partition_key, output_dir, dagster_instance):
        """Fetch text version metadata."""
        _needs_api_key()
        result = materialize(
            [bill_text_versions],
            resources=test_resources,
            partition_key=partition_key,
            instance=dagster_instance,
        )
        assert result.success
        versions = result.output_for_node("bill_text_versions")
        assert isinstance(versions, list)
        assert len(versions) >= 1, "Bill should have at least one text version"
        print(f"\n  {len(versions)} text versions:")
        for v in versions:
            print(f"    [{v.version_code}] {v.version_name}")

    def test_bill_amendments(self, test_resources, partition_key, output_dir, dagster_instance):
        """Fetch bill amendments."""
        _needs_api_key()
        result = materialize(
            [bill_amendments],
            resources=test_resources,
            partition_key=partition_key,
            instance=dagster_instance,
        )
        assert result.success
        amendments = result.output_for_node("bill_amendments")
        assert isinstance(amendments, list)
        print(f"\n  {len(amendments)} amendments")

    def test_bill_full_text(self, test_resources, partition_key, output_dir, dagster_instance):
        """Download actual bill text from congress.gov."""
        _needs_api_key()
        result = materialize(
            [bill_text_versions, bill_full_text],
            resources=test_resources,
            partition_key=partition_key,
            instance=dagster_instance,
        )
        assert result.success
        texts = result.output_for_node("bill_full_text")
        assert isinstance(texts, list)
        assert len(texts) >= 1, "Should download at least one text version"
        t = texts[0]
        assert t["html"], "Should have raw HTML"
        assert t["text"], "Should have stripped plain text"
        print(f"\n  {len(texts)} versions downloaded:")
        for t in texts:
            print(f"    [{t['version_code']}] {t['text_length']:,} chars")
        print(f"  Preview: {texts[0]['text'][:200]}...")


# ═══════════════════════════════════════════════════════════════════════════
# Silver — Document + Chunks
# ═══════════════════════════════════════════════════════════════════════════


class TestSilver:
    """bill_document, bill_chunks — transforms bronze into chunkable documents."""

    def test_bill_document(self, test_resources, partition_key, output_dir, dagster_instance):
        """Build Document from detail + full text."""
        _needs_api_key()
        result = materialize(
            [bill_detail, bill_text_versions, bill_full_text, bill_document],
            resources=test_resources,
            partition_key=partition_key,
            instance=dagster_instance,
        )
        assert result.success
        doc = result.output_for_node("bill_document")
        assert doc.id.startswith("congress-bill-")
        assert doc.content, "Document should have content"
        assert len(doc.content) > 100, "Document should include full text"
        print(f"\n  Doc ID: {doc.id}")
        print(f"  Title: {doc.title}")
        print(f"  Content: {len(doc.content):,} chars")
        print(f"  Has full text: {'FULL TEXT' in doc.content}")

    def test_bill_chunks(self, test_resources, partition_key, output_dir, dagster_instance):
        """Chunk document for downstream extraction."""
        _needs_api_key()
        result = materialize(
            [bill_detail, bill_text_versions, bill_full_text, bill_document, bill_chunks],
            resources=test_resources,
            partition_key=partition_key,
            instance=dagster_instance,
        )
        assert result.success
        chunks = result.output_for_node("bill_chunks")
        assert isinstance(chunks, list)
        assert len(chunks) >= 1
        print(f"\n  {len(chunks)} chunks")
        for c in chunks[:3]:
            print(f"    chunk {c.index}/{c.total_chunks}: {len(c.text)} chars")


# ═══════════════════════════════════════════════════════════════════════════
# Gold — LLM extraction + embeddings (requires LLM_API_KEY)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.llm
@pytest.mark.slow
class TestGold:
    """bill_mentions, bill_assertions, bill_embeddings — full chain."""

    def test_bill_mentions(self, test_resources, partition_key, output_dir, dagster_instance):
        """Extract entity mentions via LLM.

        Saves extraction output as a per-model fixture for benchmark comparison.
        """
        _needs_api_key()
        _needs_llm()
        result = materialize(
            [bill_detail, bill_text_versions, bill_full_text, bill_document, bill_chunks, bill_mentions],
            resources=test_resources,
            partition_key=partition_key,
            instance=dagster_instance,
        )
        assert result.success
        mentions = result.output_for_node("bill_mentions")
        assert isinstance(mentions, list)
        assert len(mentions) >= 1, "Should extract at least one mention"

        # Save extraction fixture per-model for benchmark comparison
        model = os.environ.get("LLM_MODEL", "gpt-4o-mini")
        fixture_dir = output_dir / "fixtures"
        fixture_dir.mkdir(parents=True, exist_ok=True)
        fixture_path = fixture_dir / f"mentions_{model}.json"
        mention_dicts = [m.model_dump(mode="json") if hasattr(m, "model_dump") else m for m in mentions]
        with open(fixture_path, "w") as f:
            json.dump({"model": model, "mentions": mention_dicts, "partition": partition_key}, f, indent=2, default=str)
        print(f"\n  {len(mentions)} mentions extracted → {fixture_path}")

        types = {}
        for m in mentions:
            t = m.mention_type if hasattr(m, "mention_type") else m.get("mention_type", "?")
            types[t] = types.get(t, 0) + 1
        for t, c in sorted(types.items(), key=lambda x: -x[1]):
            print(f"    {t}: {c}")

    def test_bill_embeddings(self, test_resources, partition_key, output_dir, dagster_instance):
        """Generate embeddings for chunks."""
        _needs_api_key()
        _needs_llm()
        result = materialize(
            [bill_detail, bill_text_versions, bill_full_text, bill_document, bill_chunks, bill_embeddings],
            resources=test_resources,
            partition_key=partition_key,
            instance=dagster_instance,
        )
        assert result.success
        embedded = result.output_for_node("bill_embeddings")
        assert isinstance(embedded, list)
        assert len(embedded) >= 1
        dims = len(embedded[0]["embedding"])
        print(f"\n  {len(embedded)} vectors, {dims} dimensions each")
        assert dims == 768, f"Expected 768 dims (nomic-embed-text), got {dims}"


# ═══════════════════════════════════════════════════════════════════════════
# Full chain — end to end
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.llm
@pytest.mark.slow
class TestFullChain:
    """Run the ENTIRE bill tail pipeline for a single partition."""

    def test_full_bill_pipeline(self, test_resources, partition_key, output_dir, dagster_instance):
        """Bronze → Silver → Gold for one bill."""
        _needs_api_key()
        _needs_llm()

        all_assets = [
            bill_detail,
            bill_actions,
            bill_cosponsors,
            bill_text_versions,
            bill_amendments,
            bill_full_text,
            bill_document,
            bill_chunks,
            bill_mentions,
            bill_assertions,
            bill_embeddings,
        ]

        result = materialize(
            all_assets,
            resources=test_resources,
            partition_key=partition_key,
            instance=dagster_instance,
        )
        assert result.success, "Full pipeline should succeed"

        # Verify key outputs
        detail = result.output_for_node("bill_detail")
        doc = result.output_for_node("bill_document")
        chunks = result.output_for_node("bill_chunks")
        mentions = result.output_for_node("bill_mentions")
        embedded = result.output_for_node("bill_embeddings")

        # Save full extraction fixture per-model for benchmark comparison
        model = os.environ.get("LLM_MODEL", "gpt-4o-mini")
        assertions = result.output_for_node("bill_assertions")
        fixture_dir = output_dir / "fixtures"
        fixture_dir.mkdir(parents=True, exist_ok=True)
        mention_dicts = [m.model_dump(mode="json") if hasattr(m, "model_dump") else m for m in mentions]
        assertion_dicts = [a.model_dump(mode="json") if hasattr(a, "model_dump") else a for a in (assertions or [])]
        chunk_dicts = [c.model_dump(mode="json") if hasattr(c, "model_dump") else c for c in chunks]
        fixture_path = fixture_dir / f"extraction_{model}.json"
        with open(fixture_path, "w") as f:
            json.dump(
                {
                    "model": model,
                    "partition": partition_key,
                    "mentions": mention_dicts,
                    "assertions": assertion_dicts,
                    "chunks": chunk_dicts,
                },
                f,
                indent=2,
                default=str,
            )

        print("\n  ═══ FULL PIPELINE RESULTS ═══")
        print(f"  Bill: {detail.id} — {detail.title}")
        print(f"  Sponsor: {detail.sponsor_name}")
        print(f"  Document: {len(doc.content):,} chars")
        print(f"  Chunks: {len(chunks)}")
        print(f"  Mentions: {len(mentions)}")
        print(f"  Embeddings: {len(embedded)} × {len(embedded[0]['embedding'])} dims")
        print(f"  Extraction fixture: {fixture_path}")
        print(f"  Output dir: {output_dir}")
