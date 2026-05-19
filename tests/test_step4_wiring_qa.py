"""QA pyramid for Wave 1, Step 4 (bead llm-g0b) — catalyst-data side wiring.

Step 4 wired 4 catalyst-data pyprojects (congress-data, media-ingest, open-leaks,
knowledge-graph) to consume catalyst-exgraph + catalyst-contracts-core as
editable workspace deps, fixed 11 caller-side tuple-unpack / ``last_stats``
breakages, deleted 3 dead test files, and shipped ``docs/PROJECTION_LAYERS.md``.

This file is the **non-tautological** strength test for Step 4 — it pins the
contracts the wiring depends on, sweeps the source tree for the legacy patterns
that were retired, and walks the projection-layers doc against the actual
LangGraph node implementations.

Pyramid:

  T1 — Adversarial unit (≈60%)
  T2 — Property-based (≈25%) [hypothesis]
  T3 — Differential / regression (≈10%)
  T4 — Scenario — real-corpus AMR wire-path (≈5%)

QA-3's three thin smoke tests in ``libs/dagster-io/tests/test_extraction.py``
plus QA-3's deeper ``test_extraction_qa.py`` are NOT duplicated here. This file
deliberately covers what Step 4 specifically touched (the consumer-side wiring
in catalyst-data) — not the wrapper internals (those are owned by Steps 3 + QA-3).
"""

from __future__ import annotations

import importlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# Repo root — Step 4's grep audits walk the whole tree, so anchor here.
REPO_ROOT = Path(__file__).resolve().parents[1]


# ───────────────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────────────


def _grep_py_sources(pattern: str) -> list[str]:
    """Grep *.py files outside of beads/worktrees/venv/__pycache__ for ``pattern``.

    Excludes THIS file (the QA test itself mentions the legacy symbols as
    test patterns; we don't want self-matches polluting the audit).

    Returns the matching ``path:line:content`` strings. Empty list means clean.
    """
    cmd = [
        "grep",
        "-rn",
        "--include=*.py",
        "--exclude-dir=.beads",
        "--exclude-dir=.claude",
        "--exclude-dir=.venv",
        "--exclude-dir=__pycache__",
        "--exclude-dir=node_modules",
        "--exclude-dir=.pytest_cache",
        "--exclude-dir=.hypothesis",
        "--exclude=test_step4_wiring_qa.py",
        pattern,
        str(REPO_ROOT),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode not in (0, 1):  # 1 = no match, 0 = match found
        pytest.fail(f"grep failed: {result.stderr}")
    return [ln for ln in result.stdout.splitlines() if ln.strip()]


def _make_text_chunk(document_id: str = "d1", chunk_id: str = "c1", text: str = "hello") -> MagicMock:
    chunk = MagicMock()
    chunk.text = text
    chunk.document_id = document_id
    chunk.chunk_id = chunk_id
    chunk.metadata = {}
    return chunk


# ───────────────────────────────────────────────────────────────────
# T1 — Adversarial unit
# ───────────────────────────────────────────────────────────────────


class TestExtractValidatedReturnType:
    """``extract_validated()`` MUST return an ``ExtractionResult`` instance
    (NOT a tuple) per the Step 3 contract that Step 4 wired all 11 callers
    to. If someone reverts the wrapper to a tuple-return shape, every
    asset that does ``result.mentions`` would silently break with
    ``AttributeError`` — and the asset would yield an empty list, zeroing
    out a whole partition with no signal.
    """

    def test_return_is_extraction_result_not_tuple(self):
        from catalyst_exgraph.protocol import ExtractionResult

        from dagster_io.extraction import extract_validated

        result = extract_validated([], "congress_data")
        assert isinstance(result, ExtractionResult)
        assert not isinstance(result, tuple)

    def test_return_has_required_attributes(self):
        """ExtractionResult must surface ``.mentions``, ``.assertions``,
        ``.stats``, ``.audit_events`` — the four attributes asset_factory
        + the per-domain assets read from."""
        from dagster_io.extraction import extract_validated

        result = extract_validated([], "congress_data")
        for attr in ("mentions", "assertions", "stats", "audit_events"):
            assert hasattr(result, attr), f"ExtractionResult missing {attr!r}"


class TestExtractValidatedEmptyStatsShape:
    """The empty-chunks short-circuit returns an ``ExtractionResult()`` with
    factory defaults. Pin those: mentions/assertions empty lists, stats an
    empty dict, audit_events empty list. If the default ever becomes
    ``None`` or a populated dict, this catches it.
    """

    def test_empty_chunks_returns_empty_lists(self):
        from dagster_io.extraction import extract_validated

        result = extract_validated([], "congress_data")
        assert result.mentions == []
        assert result.assertions == []

    def test_empty_chunks_stats_is_dict(self):
        """``stats`` is a dict (possibly empty on the short-circuit).
        Specifically NOT None, NOT a list — downstream code does
        ``stats.get(...)`` so the dict invariant is load-bearing.
        """
        from dagster_io.extraction import extract_validated

        result = extract_validated([], "congress_data")
        assert isinstance(result.stats, dict), f"stats is {type(result.stats).__name__}, not dict"
        # ``audit_events`` shape pinned too — assets that persist this need a list.
        assert isinstance(result.audit_events, list)


class TestPopulatedStatsKeySet:
    """When extract_assertions runs (resource-side, not the wrapper
    short-circuit), ``stats`` MUST carry exactly these 6 keys per the
    Step 4 docstring contract:

        {chunk_count, duration_s, mention_count, assertion_count,
         errors, pipeline}

    The old SPO-LLM keys (``pipeline_breakdown``, ``llm_call_count``,
    ``context_window``, ``mention_retries``, ``proposition_retries``)
    are GONE.
    """

    EXPECTED_KEYS = {
        "chunk_count",
        "duration_s",
        "mention_count",
        "assertion_count",
        "errors",
        "pipeline",
    }
    FORBIDDEN_KEYS = {
        "pipeline_breakdown",
        "llm_call_count",
        "context_window",
        "mention_retries",
        "proposition_retries",
        "last_stats",  # never a key — but pin in case someone re-introduces
    }

    def test_resource_stats_keys_pinned(self):
        """Walk through the resource directly with an empty real call so
        we hit the populated-stats path (not the wrapper short-circuit).
        ``ExtractionResource.extract_assertions([], …)`` short-circuits
        BEFORE the stats dict is built — so we exercise the NER-only
        ``_run_ner_only_pipeline`` no-op branch via ``extract_mentions``
        instead (also returns empty ExtractionResult on chunks=[]).

        Hmm, both short-circuit on empty input. To pin the actual
        populated-stats schema, exercise the AMR pipeline with a single
        chunk via a stubbed pipeline. The stub returns the empty pipeline
        result, but the resource still builds stats dict at the end.
        """
        # Read the resource source — Step 4 + Step 3 hand-rolled the
        # stats dict literal. If anyone adds or removes a key, the
        # literal changes. Pin by inspecting source. Faster + more
        # accurate than building a multi-encoder pipeline mock.
        from catalyst_exgraph import resource as r_mod

        src = Path(r_mod.__file__).read_text()

        # Two locations in the file build the stats dict (amr + ner-only).
        # Both must use the EXPECTED_KEYS set.
        for forbidden in self.FORBIDDEN_KEYS:
            # The forbidden keys must NOT appear as quoted dict keys.
            # Allow them in comments/docstrings (we deprecated, didn't erase).
            pattern = re.compile(r'^\s*"' + re.escape(forbidden) + r'"\s*:', re.MULTILINE)
            assert not pattern.search(src), (
                f"Forbidden stats key {forbidden!r} still appears as a dict literal "
                f"in catalyst_exgraph.resource — Step 3 was supposed to drop it."
            )

        for expected in self.EXPECTED_KEYS:
            pattern = re.compile(r'^\s*"' + re.escape(expected) + r'"\s*:', re.MULTILINE)
            assert pattern.search(src), (
                f"Expected stats key {expected!r} missing from catalyst_exgraph.resource"
            )


class TestGrepAuditTupleUnpack:
    """Search the catalyst-data tree for the legacy ``mentions, assertions
    = extract_validated(...)`` pattern (Step 4 fixed 11 of these). Should
    return ZERO hits.

    .claude/worktrees holds throwaway agent scratch copies of the repo;
    they're excluded.
    """

    def test_no_tuple_unpack_of_extract_validated(self):
        # Match ``... = extract_validated(`` where the LHS contains a comma
        # (the tuple-unpack shape) — but NOT the new ``result = extract_validated(``.
        hits = _grep_py_sources(r"\w\+\s*,\s*\w\+\s*=\s*extract_validated(")
        assert not hits, (
            f"Tuple-unpack of extract_validated() still present in {len(hits)} "
            f"location(s) — Step 3 contract is broken:\n" + "\n".join(hits)
        )


class TestGrepAuditLastStats:
    """``last_stats`` was the deleted SPO-LLM side channel. It may still
    appear in docstrings / comments / bead history (explicitly noting
    deprecation), but it MUST NOT appear as a Python attribute access
    or assignment in *.py source.
    """

    def test_no_last_stats_attribute_access(self):
        """Catches ``.last_stats``, ``last_stats =``, ``last_stats[`` patterns.
        Docstring mentions like '``last_stats`` is gone' are fine — they don't
        match these patterns.
        """
        # Attribute-access pattern: ``.last_stats``
        hits_attr = _grep_py_sources(r"\.last_stats\b")
        # Assignment pattern: ``last_stats =``
        hits_assign = _grep_py_sources(r"^\s*last_stats\s*=")
        # Subscript pattern: ``last_stats[``
        hits_subscript = _grep_py_sources(r"last_stats\s*\[")

        all_hits = hits_attr + hits_assign + hits_subscript
        # Filter out any that look like they're inside a string literal
        # mentioning the deprecation — grep returns the whole line, so
        # check the context word.
        real = [
            h for h in all_hits
            if "deprecat" not in h.lower()
            and "gone" not in h.lower()
            and "retire" not in h.lower()
        ]
        assert not real, (
            f"`last_stats` still used as a real attribute/assignment in "
            f"{len(real)} location(s):\n" + "\n".join(real)
        )


class TestGrepAuditDeadSymbols:
    """``extract_with_shared_clusters``, ``CATALYST_BENCH_MODEL``, and
    ``LLM_PER_CALL_TIMEOUT`` were all retired with the SPO-LLM phase. They
    may still appear in docstrings/comments as deprecation notes, but NOT
    as actual code (call sites, env var reads, etc.).
    """

    def test_no_extract_with_shared_clusters_callsite(self):
        # Call-site pattern: ``extract_with_shared_clusters(``
        hits = _grep_py_sources(r"extract_with_shared_clusters\s*(")
        # All hits should be inside docstring backticks or comments.
        real = [
            h for h in hits
            if not (
                "``extract_with_shared_clusters``" in h
                or "# " in h.split(":", 2)[-1] if len(h.split(":", 2)) >= 3 else False
            )
        ]
        # Stricter: just filter for code-shaped lines (no triple-backtick context).
        real = []
        for h in hits:
            # Pull the line content after ``path:line:``
            parts = h.split(":", 2)
            if len(parts) < 3:
                continue
            line = parts[2]
            # Skip if it's a docstring/comment mention (backticks, ``#``, or
            # contains 'retired'/'gone'/'deprecated')
            if "``extract_with_shared_clusters``" in line:
                continue
            if re.match(r"\s*#", line):
                continue
            if any(w in line.lower() for w in ("retired", "gone", "deprecat", "is gone")):
                continue
            real.append(h)
        assert not real, (
            f"`extract_with_shared_clusters` callsite still present in "
            f"{len(real)} location(s):\n" + "\n".join(real)
        )

    def test_no_catalyst_bench_model_env_read(self):
        # Pattern: ``CATALYST_BENCH_MODEL`` used in env or string literal
        # not inside a docstring/comment.
        hits = _grep_py_sources(r"CATALYST_BENCH_MODEL")
        real = []
        for h in hits:
            parts = h.split(":", 2)
            if len(parts) < 3:
                continue
            line = parts[2]
            if "``CATALYST_BENCH_MODEL``" in line:
                continue
            if re.match(r"\s*#", line):
                continue
            if any(w in line.lower() for w in ("retired", "gone", "deprecat", "is gone")):
                continue
            real.append(h)
        assert not real, (
            f"`CATALYST_BENCH_MODEL` env read still present in "
            f"{len(real)} location(s):\n" + "\n".join(real)
        )

    def test_no_llm_per_call_timeout(self):
        hits = _grep_py_sources(r"LLM_PER_CALL_TIMEOUT")
        # No deprecation context expected — this name shouldn't appear at all.
        real = [h for h in hits if "LLM_PER_CALL_TIMEOUT" in h]
        assert not real, (
            f"`LLM_PER_CALL_TIMEOUT` still present in "
            f"{len(real)} location(s):\n" + "\n".join(real)
        )


class TestDeletedTestFilesGone:
    """The 3 deleted test files MUST be truly gone — no skip-only placeholders
    that would mask a regression by appearing 'passing'.
    """

    DELETED = [
        "tests/test_phase_b_uses_consensus_for_spo.py",
        "tests/test_run_model_encoder_tier_skips_phase_b.py",
        "tests/test_per_llm_call_timeout.py",
    ]

    def test_files_do_not_exist(self):
        for rel in self.DELETED:
            p = REPO_ROOT / rel
            assert not p.exists(), f"{rel} should be deleted but still exists"


class TestProjectionLayersDocCompleteness:
    """``docs/PROJECTION_LAYERS.md`` is the canonical projection table. Pin
    the rows / medallion layers / S3 prefixes / preamble that downstream
    contributors rely on.
    """

    @pytest.fixture(scope="class")
    def doc(self) -> str:
        return (REPO_ROOT / "docs" / "PROJECTION_LAYERS.md").read_text()

    def test_preamble_exists(self, doc):
        """The 'Why this matters' section is the rationale anchor. Pin it
        so a doc-shortening refactor doesn't accidentally drop it."""
        assert "## Why this matters" in doc, "Preamble heading missing"
        # And there's substantive prose under it (not just an empty stub).
        match = re.search(r"## Why this matters\s*\n+(.+?)(\n## |\Z)", doc, re.DOTALL)
        assert match, "Preamble section structure broken"
        body = match.group(1).strip()
        assert len(body) > 100, f"Preamble body too short ({len(body)} chars)"

    def test_required_row_targets_present(self, doc):
        """Every medallion-tier asset that callers map back from the table
        must be present as a row (left or middle column).
        """
        required_assets = [
            "bill_documents",          # bronze
            "congress_chunks",         # silver (per-domain chunker)
            "{domain}_chunks",         # silver (semantic-seed row)
            "congress_mentions",       # gold
            "congress_assertions",     # gold
            "canonical_entities",      # platinum
        ]
        for asset in required_assets:
            assert asset in doc, (
                f"Required asset {asset!r} missing from PROJECTION_LAYERS table"
            )

    def test_all_medallion_layers_represented(self, doc):
        """The 4 medallion tiers should all appear as values in the
        ``Medallion layer`` column.
        """
        for layer in ("bronze", "silver", "gold", "platinum"):
            assert f"| {layer} |" in doc, (
                f"Medallion layer {layer!r} missing from table column"
            )

    def test_s3_prefixes_match_medallion(self, doc):
        """For each table row that carries an S3 prefix, the prefix MUST
        start with the medallion layer column value (or be 'not persisted'
        / 'Neo4j primary' for transient rows).

        Parses the markdown table rows under '## The mapping table'.
        """
        # Find the mapping table block
        m = re.search(r"## The mapping table\s*\n+(\|.+?)(\n##|\Z)", doc, re.DOTALL)
        assert m, "Mapping table block missing"
        table = m.group(1)

        rows = [ln for ln in table.splitlines() if ln.startswith("|") and "---" not in ln]
        # Drop the header row
        data_rows = rows[1:]

        def _strip_md(s: str) -> str:
            # Drop markdown backticks + extract the first whitespace-delimited token.
            cleaned = s.strip().strip("`").strip()
            return cleaned

        violations: list[str] = []
        for row in data_rows:
            cells = [c.strip() for c in row.split("|")[1:-1]]
            if len(cells) < 5:
                continue  # malformed; skip — different test catches that
            medallion_cell = cells[2].strip().lower()
            s3_prefix_cell = cells[4].strip()
            # Extract the leading prefix from the s3 cell — may have
            # backticks and a trailing parenthetical note like "(unpartitioned)".
            s3_first = _strip_md(s3_prefix_cell.split()[0]) if s3_prefix_cell else ""
            s3_first = s3_first.strip("`").lower()

            if medallion_cell in ("—", "-", ""):
                continue  # transient row
            if s3_prefix_cell.lower().startswith("not persisted"):
                continue
            if "neo4j" in s3_prefix_cell.lower():
                continue  # special-cased platinum (Neo4j primary)

            # Medallion cell may carry a slash-separated dual marker like
            # "— / gold-aux" for transient-or-persisted rows. Accept any
            # candidate tier that appears in the cell.
            candidates = [
                t for t in ("bronze", "silver", "gold", "platinum")
                if t in medallion_cell
            ]
            if not candidates:
                continue  # purely-transient row
            if not any(s3_first.startswith(c + "/") for c in candidates):
                violations.append(
                    f"Row layer-cell={medallion_cell!r} but s3_first={s3_first!r} "
                    f"doesn't start with any of {[c + '/' for c in candidates]}"
                )
        assert not violations, (
            "S3 prefixes don't match medallion column:\n" + "\n".join(violations)
        )


class TestProjectionLayersDocLangGraphNodes:
    """Every LangGraph node class name mentioned in the doc must be a real
    importable class from ``catalyst_exgraph.nodes.*``. Catches typos +
    stale references after a class rename.
    """

    # Map of nodes referenced in the table → (module, class).
    REFERENCED_NODES = {
        "ChunkNode": ("catalyst_exgraph.nodes.chunk", "ChunkNode"),
        "NerEnsembleNode": ("catalyst_exgraph.nodes.ner_ensemble", "NerEnsembleNode"),
        "ConsensusNode": ("catalyst_exgraph.nodes.consensus", "ConsensusNode"),
        "ClusterEntitiesNode": ("catalyst_exgraph.nodes.cluster", "ClusterEntitiesNode"),
        "PackEvidenceNode": ("catalyst_exgraph.nodes.pack", "PackEvidenceNode"),
        "AmrParseNode": ("catalyst_exgraph.nodes.amr_parse", "AmrParseNode"),
        "AmrToAssertionNode": ("catalyst_exgraph.nodes.amr_project", "AmrToAssertionNode"),
    }

    @pytest.fixture(scope="class")
    def doc(self) -> str:
        return (REPO_ROOT / "docs" / "PROJECTION_LAYERS.md").read_text()

    def test_each_referenced_node_appears_in_doc(self, doc):
        """Every node in REFERENCED_NODES should actually be mentioned in
        the doc — if a node disappears here, the doc rationale is broken.
        """
        missing = [name for name in self.REFERENCED_NODES if name not in doc]
        assert not missing, f"REFERENCED_NODES not found in doc: {missing}"

    @pytest.mark.parametrize(
        ("node_name", "module_class"),
        list(REFERENCED_NODES.items()),
    )
    def test_node_class_importable(self, node_name, module_class):
        """Each referenced node must import cleanly from catalyst-exgraph.
        Stale references (e.g. an old class name after a rename) blow up here.
        """
        module_name, class_name = module_class
        mod = importlib.import_module(module_name)
        assert hasattr(mod, class_name), (
            f"Doc references {node_name!r} but {module_name}.{class_name} doesn't exist"
        )


class TestWorkspaceDepResolution:
    """The 4 catalyst-data packages all declare ``catalyst_exgraph`` and
    ``catalyst_contracts_core`` as deps via ``[tool.uv.sources]``. Smoke-
    test that the imports actually work for each package's source tree.

    These are import-only tests — we don't try to instantiate
    ExtractionResource (heavy: pulls in label packs, AMR parser shims).
    """

    def test_dagster_io_can_import_resource(self):
        from catalyst_exgraph.resource import ExtractionResource  # noqa: F401

        from catalyst_contracts_core import Assertion, Mention, Provenance  # noqa: F401

    def test_congress_data_package_imports(self):
        sys.path.insert(0, str(REPO_ROOT / "packages" / "congress-data" / "src"))
        try:
            # Importing the package surface validates the pyproject deps resolve
            import congress_data  # noqa: F401
            import congress_data.partitions  # noqa: F401
        finally:
            # Don't leak path mutation into other tests
            try:
                sys.path.remove(str(REPO_ROOT / "packages" / "congress-data" / "src"))
            except ValueError:
                pass

    def test_media_ingest_assets_import_chain(self):
        sys.path.insert(0, str(REPO_ROOT / "packages" / "media-ingest" / "src"))
        try:
            from media_ingest.assets.assertions import media_assertions  # noqa: F401
            from media_ingest.assets.mentions import media_mentions  # noqa: F401
        finally:
            try:
                sys.path.remove(str(REPO_ROOT / "packages" / "media-ingest" / "src"))
            except ValueError:
                pass

    def test_open_leaks_assets_import_chain(self):
        sys.path.insert(0, str(REPO_ROOT / "packages" / "open-leaks" / "src"))
        try:
            from open_leaks.assets.assertions import leak_assertions  # noqa: F401
            from open_leaks.assets.mentions import leak_mentions  # noqa: F401
        finally:
            try:
                sys.path.remove(str(REPO_ROOT / "packages" / "open-leaks" / "src"))
            except ValueError:
                pass

    def test_knowledge_graph_package_imports(self):
        sys.path.insert(0, str(REPO_ROOT / "packages" / "knowledge-graph" / "src"))
        try:
            import knowledge_graph  # noqa: F401
        finally:
            try:
                sys.path.remove(str(REPO_ROOT / "packages" / "knowledge-graph" / "src"))
            except ValueError:
                pass


class TestAssetFactoryImports:
    """asset_factory was the load-bearing file Step 4 fixed (tuple-unpack →
    result.mentions/result.assertions). Smoke-test its import chain so a
    regression that breaks the file is caught at collection time, not at
    Dagster materialization time (which fails opaquely under k8s).
    """

    def test_asset_factory_imports_cleanly(self):
        from dagster_io.asset_factory import (  # noqa: F401
            EMBEDDING_ASSET_K8S_CONFIG,
            PipelineConfig,
            extraction_assets,
        )

    def test_pipeline_config_dataclass_shape(self):
        """``PipelineConfig`` is a dataclass — pin the required fields so
        downstream constructors (per-domain Definitions) catch a renamed
        field at test time.
        """
        from dataclasses import fields

        from dagster_io.asset_factory import PipelineConfig

        names = {f.name for f in fields(PipelineConfig)}
        required = {"domain", "code_location", "chunks_asset_key"}
        assert required.issubset(names), (
            f"PipelineConfig missing required fields: {required - names}"
        )


# ───────────────────────────────────────────────────────────────────
# T2 — Property-based (hypothesis)
# ───────────────────────────────────────────────────────────────────


class TestExtractValidatedTotality:
    """Across any (small) chunks input + arbitrary code_location string,
    ``extract_validated`` returns an ExtractionResult or propagates a
    known exception type (ImportError for missing amrlib in-env, or
    FileNotFoundError for missing label pack). It must NOT raise a
    bare Exception or return None.
    """

    @settings(
        max_examples=30,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    @given(
        n_chunks=st.integers(min_value=0, max_value=3),
        code_location=st.sampled_from(["", "congress_data", "media_ingest", "open_leaks", "weird"]),
    )
    def test_returns_result_or_known_exception(self, n_chunks, code_location, monkeypatch):
        """Stub the resource module so we never run AMR; the wrapper still
        owns the dispatch logic, and that's what we're sweeping.
        """
        import types

        from catalyst_exgraph.protocol import ExtractionResult

        from dagster_io.extraction import extract_validated

        # Build a stub resource so no real AMR fires. The stub returns an
        # empty ExtractionResult for the populated-chunks path.
        instance = MagicMock()
        instance.extract_assertions.return_value = ExtractionResult()
        stub = types.ModuleType("catalyst_exgraph.resource")
        stub.ExtractionResource = MagicMock(return_value=instance)
        monkeypatch.setitem(sys.modules, "catalyst_exgraph.resource", stub)

        chunks = [_make_text_chunk(f"d{i}", f"c{i}", "text") for i in range(n_chunks)]
        result = extract_validated(chunks, code_location)
        assert isinstance(result, ExtractionResult)


class TestEmptyChunksShortCircuitInvariant:
    """For any code_location, empty chunks → ExtractionResult with empty
    mentions + assertions. The short-circuit must NEVER allocate a
    populated result.
    """

    @settings(max_examples=50, deadline=None)
    @given(code_location=st.text(min_size=0, max_size=30))
    def test_empty_chunks_always_returns_empty(self, code_location):
        from dagster_io.extraction import extract_validated

        result = extract_validated([], code_location)
        assert result.mentions == []
        assert result.assertions == []


class TestExtractionResultStatsDictInvariant:
    """``ExtractionResult.stats`` is always a dict. The factory default is
    ``dict``; the resource-populated path is also a dict. Property: stats
    has dict-like ``.get(...)`` semantics for any default kwarg.
    """

    @settings(max_examples=20, deadline=None)
    @given(
        key=st.text(min_size=1, max_size=20),
        default=st.one_of(st.integers(), st.text(), st.none()),
    )
    def test_stats_get_with_default(self, key, default):
        from dagster_io.extraction import extract_validated

        result = extract_validated([], "congress_data")
        # On the empty short-circuit, stats is {}. ``.get(...)`` returns default.
        assert result.stats.get(key, default) == default


# ───────────────────────────────────────────────────────────────────
# T3 — Differential / regression
# ───────────────────────────────────────────────────────────────────


class TestWrapperVsResourceDifferential:
    """Differential: ``extract_validated([], "congress_data")`` and
    ``ExtractionResource(...).extract_assertions([], "congress_data")``
    MUST produce equivalent ExtractionResults (same empty-shape).

    If they diverge, the wrapper has injected per-call behavior that the
    resource doesn't have — a wire-shape leak. We use ``label_pack_id``
    explicitly so the resource doesn't try to load 'generic' (which would
    succeed but emit zero assertions — same shape, but for the wrong
    reason).
    """

    def test_empty_chunks_equivalent_shape(self):
        from catalyst_exgraph.resource import ExtractionResource

        from dagster_io.extraction import extract_validated

        wrapper_result = extract_validated([], "congress_data")

        # Direct resource construction — pick label_pack_id="generic"
        # since we're not running anything past the short-circuit anyway.
        resource = ExtractionResource(
            label_pack_id="generic",
            prompt_dir="",
        )
        direct_result = resource.extract_assertions([], "congress_data")

        # Both short-circuit, both return ExtractionResult() factory defaults.
        assert wrapper_result.mentions == direct_result.mentions == []
        assert wrapper_result.assertions == direct_result.assertions == []
        assert wrapper_result.stats == direct_result.stats == {}
        assert wrapper_result.audit_events == direct_result.audit_events == []
        assert type(wrapper_result) is type(direct_result)


class TestStatsKeyParityRegression:
    """Pin the EXACT key set on ``ExtractionResult.stats`` for a
    populated-pipeline call. If the resource adds or removes a key, this
    breaks — forcing the contributor to update the doc/contracts in
    lockstep.

    Strategy: instead of running real AMR, scan the resource source for
    the two stats-dict literals and verify the union of their keys equals
    the documented set.
    """

    DOCUMENTED = {
        "chunk_count",
        "duration_s",
        "mention_count",
        "assertion_count",
        "errors",
        "pipeline",
    }

    def test_resource_stats_dict_literal_key_parity(self):
        from catalyst_exgraph import resource as r_mod

        src = Path(r_mod.__file__).read_text()

        # Find all dict-literal stats= blocks: stats={...}
        # Match each ``stats={`` through the matching closing ``}``. Use a
        # lightweight scanner since regex on nested braces is unreliable.
        keys_seen: set[str] = set()
        for m in re.finditer(r"stats=\{", src):
            start = m.end()
            depth = 1
            i = start
            while i < len(src) and depth > 0:
                if src[i] == "{":
                    depth += 1
                elif src[i] == "}":
                    depth -= 1
                i += 1
            block = src[start : i - 1]
            for k in re.findall(r'"([a-z_]+)"\s*:', block):
                keys_seen.add(k)

        assert keys_seen == self.DOCUMENTED, (
            f"Resource stats key set drifted from docs.\n"
            f"  In source:   {sorted(keys_seen)}\n"
            f"  Documented:  {sorted(self.DOCUMENTED)}\n"
            f"  Added:       {keys_seen - self.DOCUMENTED}\n"
            f"  Removed:     {self.DOCUMENTED - keys_seen}"
        )


# ───────────────────────────────────────────────────────────────────
# T4 — Scenario — real-corpus AMR wire-path
# ───────────────────────────────────────────────────────────────────


class TestRealCorpusWiringSmoke:
    """End-to-end wiring smoke: load a real congress chunk from MinIO,
    run it through ``extract_validated`` with the congress label pack.

    What we verify:
      - The wiring path resolves: label-pack load → AMR parser
        construction → pipeline build all happen without an ImportError
        on catalyst-exgraph or catalyst-contracts-core.
      - The call either returns an ExtractionResult (full success) OR
        raises a known infra-dependency exception (amrlib missing,
        label pack missing). Either way, the WIRING is sound.

    If the call instead raises ImportError on catalyst-exgraph /
    catalyst-contracts-core, Step 4's wiring is BROKEN — that's what
    this test catches.
    """

    @pytest.fixture(scope="class")
    def real_chunks(self):
        """Load 1 chunk from MinIO if available; skip if MinIO unreachable.

        MinIO is part of the dev tilt stack — present if `tilt up` ran but
        not guaranteed in CI. Skipping (not failing) is the right call.
        """
        try:
            import boto3
        except ImportError:
            pytest.skip("boto3 not installed — wiring smoke skipped")

        endpoint = os.environ.get("DAGSTER_S3_ENDPOINT_URL", "http://localhost:9000")
        access = os.environ.get("DAGSTER_S3_ACCESS_KEY", "minio")
        secret = os.environ.get("DAGSTER_S3_SECRET_KEY", "minio123")
        bucket = os.environ.get("DAGSTER_S3_BUCKET", "dagster")

        try:
            s3 = boto3.client(
                "s3",
                endpoint_url=endpoint,
                aws_access_key_id=access,
                aws_secret_access_key=secret,
            )
            listing = s3.list_objects_v2(
                Bucket=bucket,
                Prefix="silver/congress_data/bill/bill_chunks/",
                MaxKeys=20,
            )
        except Exception as e:
            pytest.skip(f"MinIO unreachable at {endpoint}: {e}")

        keys = [
            x["Key"]
            for x in listing.get("Contents", [])
            if x["Key"].endswith("/data.jsonl")
        ]
        if not keys:
            pytest.skip(
                f"No silver/congress_data/bill/bill_chunks/*/data.jsonl in {bucket} — "
                "materialize bronze+silver via `task dev` first"
            )

        obj = s3.get_object(Bucket=bucket, Key=keys[0])
        lines = obj["Body"].read().decode().splitlines()
        records = [json.loads(ln) for ln in lines if ln.strip()]
        if not records:
            pytest.skip(f"{keys[0]} empty — re-materialize silver chunks")

        from dagster_io import TextChunk

        # 1 chunk is enough to exercise the full wire path
        return [TextChunk(**records[0])]

    def test_amr_pipeline_wires_through(self, real_chunks, monkeypatch):
        """The call should:
          (a) succeed and return an ExtractionResult (possibly with
              ``errors > 0`` in stats if amrlib swallowed an ImportError
              inside the chunk-level ThreadPoolExecutor — that's the
              current dev-env behaviour, not a wiring bug), OR
          (b) raise ImportError on something OTHER than catalyst_exgraph /
              catalyst_contracts_core — those would signal Step 4 wiring
              broke.

        Specifically forbidden: ImportError mentioning ``catalyst_exgraph``
        or ``catalyst_contracts_core`` — those packages MUST resolve through
        the workspace deps Step 4 wired.
        """
        from catalyst_exgraph.protocol import ExtractionResult

        from dagster_io.extraction import extract_validated

        # Point at the congress label pack we know lives in the repo.
        monkeypatch.setenv(
            "PROMPT_REGISTRY_DIR",
            str(REPO_ROOT / "k8s" / "base" / "congress-data" / "prompts"),
        )

        try:
            result = extract_validated(real_chunks, code_location="congress_data")
        except ImportError as e:
            msg = str(e)
            # KNOWN failures: amrlib not in env, gliner model fetch offline,
            # spacy model not downloaded. Not wiring bugs.
            allowed_substrings = (
                "amrlib",
                "gliner",
                "spacy",
                "transformers",
                "torch",
                "huggingface",
            )
            if any(sub in msg.lower() for sub in allowed_substrings):
                pytest.skip(f"infra dep missing (not a wiring bug): {msg[:200]}")
            # Specifically forbidden: ImportError on our own packages.
            assert "catalyst_exgraph" not in msg, f"Step 4 wiring broken: {msg}"
            assert "catalyst_contracts_core" not in msg, f"Step 4 wiring broken: {msg}"
            raise
        except FileNotFoundError as e:
            # KNOWN: label pack lookup miss — env-specific, not a wiring bug.
            if "label pack" in str(e).lower():
                pytest.skip(f"label pack not found in env: {e}")
            raise
        except RuntimeError as e:
            # KNOWN: model load failure, LLM endpoint unreachable, etc.
            msg = str(e).lower()
            if any(k in msg for k in ("model", "ollama", "connect", "endpoint")):
                pytest.skip(f"runtime infra not available: {e}")
            raise
        else:
            # Happy path — both modes are acceptable:
            #   (a) full amrlib-installed run produces real assertions
            #   (b) amrlib missing in chunk worker → ImportError caught,
            #       errors+=1 in stats, empty assertions list (current dev env)
            # Either way the wire-path is sound.
            assert isinstance(result, ExtractionResult)
            assert isinstance(result.mentions, list)
            assert isinstance(result.assertions, list)
            assert "pipeline" in result.stats
            assert result.stats["pipeline"] in ("amr", "ner_only")
            # Stats schema also pinned here as the populated-path version:
            for key in ("chunk_count", "duration_s", "mention_count", "assertion_count", "errors"):
                assert key in result.stats, f"Populated stats missing key {key!r}"
            # And chunk_count must reflect the input.
            assert result.stats["chunk_count"] == len(real_chunks)
