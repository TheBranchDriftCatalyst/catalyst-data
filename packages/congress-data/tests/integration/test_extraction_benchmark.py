"""Congress-data extraction benchmark — ground truth + model comparison.

Phase 1: Run full pipeline to generate extraction fixtures per-model:
    CONGRESS_API_KEY=xxx LLM_API_KEY=xxx LLM_MODEL=gpt-4o \\
        pytest packages/congress-data/tests/integration/test_pipeline.py -k "full_bill" -v -s

Phase 2: Generate ground truth from reference model:
    pytest packages/congress-data/tests/integration/test_extraction_benchmark.py \\
        -k "generate_ground_truth" -v -s --output-dir .test-output/congress-data

Phase 3: Benchmark other models:
    LLM_MODEL=gpt-4o-mini pytest packages/congress-data/tests/integration/test_extraction_benchmark.py \\
        -k "benchmark" -v -s --output-dir .test-output/congress-data
"""

from __future__ import annotations

import json
import os

import pytest
from tests.shared.extraction_scoring import (
    print_scores,
    score_mentions,
    score_propositions,
    validate_ground_truth,
)

pytestmark = [pytest.mark.llm, pytest.mark.slow]


def _llm_model() -> str:
    return os.environ.get("LLM_MODEL", "gpt-4o-mini")


class TestGroundTruth:
    """Generate and validate ground truth from a reference model's output."""

    def test_generate_ground_truth(self, output_dir):
        """Generate ground truth from the current model's extraction fixture."""
        fixture_dir = output_dir / "fixtures"
        gt_path = fixture_dir / "ground_truth_congress.json"
        if gt_path.exists():
            gt = json.loads(gt_path.read_text())
            if gt.get("manually_reviewed"):
                pytest.skip("Ground truth already reviewed — won't overwrite")
                return

        model = _llm_model()
        ext_path = fixture_dir / f"extraction_{model}.json"
        if not ext_path.exists():
            pytest.skip(
                f"No extraction fixture for '{model}'. "
                f"Run the full pipeline first: CONGRESS_API_KEY=xxx LLM_API_KEY=xxx LLM_MODEL={model} "
                f"pytest packages/congress-data/tests/integration/test_pipeline.py -k full_bill -v -s"
            )
            return

        ext = json.loads(ext_path.read_text())

        # Build per-chunk ground truth
        mentions_by_chunk: dict[str, list] = {}
        for m in ext["mentions"]:
            cid = m.get("chunk_id", "")
            mentions_by_chunk.setdefault(cid, []).append(m)

        assertions_by_chunk: dict[str, list] = {}
        for a in ext.get("assertions", []):
            prov = a.get("provenance") or {}
            cid = prov.get("chunk_id", "")
            assertions_by_chunk.setdefault(cid, []).append(a)

        gt_chunks = []
        for chunk in ext.get("chunks", []):
            cid = chunk["chunk_id"]
            gt_chunks.append(
                {
                    "chunk_id": cid,
                    "text": chunk["text"],
                    "mentions": [
                        {
                            "text": m["text"],
                            "mention_type": m["mention_type"],
                            "span_start": m.get("span_start"),
                            "span_end": m.get("span_end"),
                            "confidence": m.get("confidence", 1.0),
                        }
                        for m in mentions_by_chunk.get(cid, [])
                    ],
                    "propositions": [
                        {
                            "subject": a.get("subject_text", a.get("subject", "")),
                            "predicate": a.get("predicate", ""),
                            "object": a.get("object_text", a.get("object", "")),
                            "confidence": a.get("confidence", 1.0),
                            "evidence": a.get("evidence", ""),
                        }
                        for a in assertions_by_chunk.get(cid, [])
                    ],
                }
            )

        ground_truth = {
            "domain": "congress_data",
            "reference_model": model,
            "partition": ext.get("partition", ""),
            "manually_reviewed": False,
            "chunk_count": len(gt_chunks),
            "total_mentions": sum(len(c["mentions"]) for c in gt_chunks),
            "total_propositions": sum(len(c["propositions"]) for c in gt_chunks),
            "chunks": gt_chunks,
        }

        fixture_dir.mkdir(parents=True, exist_ok=True)
        with open(gt_path, "w") as f:
            json.dump(ground_truth, f, indent=2, default=str)

        print(f"\n  Ground truth generated from {model}:")
        print(
            f"    {len(gt_chunks)} chunks, {ground_truth['total_mentions']} mentions, "
            f"{ground_truth['total_propositions']} propositions"
        )
        print(f"    File: {gt_path}")
        print("\n  *** Review and correct the file, then set manually_reviewed=true ***")

    def test_ground_truth_self_check(self, output_dir):
        """Validate that all ground truth spans match their source text."""
        gt_path = output_dir / "fixtures" / "ground_truth_congress.json"
        if not gt_path.exists():
            pytest.skip("No ground truth fixture.")
            return

        gt = json.loads(gt_path.read_text())
        all_errors = []
        for chunk in gt["chunks"]:
            errors = validate_ground_truth(chunk["mentions"], chunk["text"])
            for e in errors:
                all_errors.append(f"  {chunk['chunk_id']}: {e}")

        if all_errors:
            print(f"\n  Ground truth span errors ({len(all_errors)}):")
            for e in all_errors[:20]:
                print(e)

        if gt.get("manually_reviewed"):
            assert not all_errors, f"{len(all_errors)} span errors in reviewed ground truth"
        elif all_errors:
            print(f"\n  WARNING: {len(all_errors)} span errors in unreviewed ground truth")


class TestBenchmark:
    """Score a model's extraction output against ground truth."""

    def test_benchmark(self, output_dir):
        """Score the current model's extraction against ground truth."""
        fixture_dir = output_dir / "fixtures"
        gt_path = fixture_dir / "ground_truth_congress.json"
        if not gt_path.exists():
            pytest.skip("No ground truth. Run test_generate_ground_truth first.")
            return

        model = _llm_model()
        ext_path = fixture_dir / f"extraction_{model}.json"
        if not ext_path.exists():
            pytest.skip(f"No extraction fixture for '{model}'.")
            return

        gt = json.loads(gt_path.read_text())
        ext = json.loads(ext_path.read_text())

        gt_mentions = []
        gt_propositions = []
        for chunk in gt["chunks"]:
            gt_mentions.extend(chunk["mentions"])
            gt_propositions.extend(chunk["propositions"])

        m_scores = score_mentions(ext["mentions"], gt_mentions)
        p_scores = score_propositions(ext.get("assertions", []), gt_propositions)
        print_scores(m_scores, p_scores, model=f"{model} (congress)")

    def test_compare_all_models(self, output_dir):
        """Compare all available model fixtures against ground truth."""
        fixture_dir = output_dir / "fixtures"
        gt_path = fixture_dir / "ground_truth_congress.json"
        if not gt_path.exists():
            pytest.skip("No ground truth.")
            return

        gt = json.loads(gt_path.read_text())
        gt_mentions = []
        gt_propositions = []
        for chunk in gt["chunks"]:
            gt_mentions.extend(chunk["mentions"])
            gt_propositions.extend(chunk["propositions"])

        extraction_files = sorted(fixture_dir.glob("extraction_*.json"))
        if not extraction_files:
            pytest.skip("No extraction fixtures found.")
            return

        print(f"\n{'=' * 70}")
        print(f"  Congress Multi-Model Comparison (ground truth: {gt['reference_model']})")
        print(f"{'=' * 70}")
        print(
            f"\n  {'Model':<25} {'M-F1 (strict)':<15} {'M-F1 (relax)':<15} {'P-F1 (strict)':<15} {'P-F1 (relax)':<15}"
        )
        print(f"  {'-' * 65}")

        for f in extraction_files:
            ext = json.loads(f.read_text())
            model = ext.get("model", f.stem.replace("extraction_", ""))
            m_scores = score_mentions(ext["mentions"], gt_mentions)
            p_scores = score_propositions(ext.get("assertions", []), gt_propositions)
            print(
                f"  {model:<25} {m_scores['strict_f1']:<15.3f} {m_scores['relaxed_f1']:<15.3f} "
                f"{p_scores['strict_f1']:<15.3f} {p_scores['relaxed_f1']:<15.3f}"
            )

        print(f"{'=' * 70}\n")
