"""S3 data access layer for the explorer UI."""

from __future__ import annotations

import json
from collections import defaultdict

import numpy as np
import streamlit as st
from dagster_io.logging import get_logger
from dagster_io.manifest import AssetManifest
from dagster_io.models import (
    AlignmentEdge,
    AlignmentType,
    Assertion,
    CanonicalEntity,
    EntityCandidate,
    Mention,
    MentionType,
    Provenance,
)
from dagster_io.s3_client import S3Client
from dagster_io.serializers import deserialize

logger = get_logger(__name__)


# ------------------------------------------------------------------
# Module-level cached loaders (st.cache_data cannot hash `self`)
# ------------------------------------------------------------------

@st.cache_data(ttl=300, show_spinner="Loading asset data...")
def _load_asset_data(
    endpoint_url: str,
    access_key: str,
    secret_key: str,
    bucket: str,
    asset_root: str,
    limit: int,
) -> list[dict]:
    """Cached data loader that reconstructs a DataClient internally."""
    client = DataClient(
        endpoint_url=endpoint_url,
        access_key=access_key,
        secret_key=secret_key,
        bucket=bucket,
    )
    return client.load_data(asset_root, limit=limit)


@st.cache_data(ttl=300, show_spinner="Listing assets...")
def _list_assets_cached(
    endpoint_url: str,
    access_key: str,
    secret_key: str,
    bucket: str,
) -> list[dict]:
    """Cached asset listing."""
    client = DataClient(
        endpoint_url=endpoint_url,
        access_key=access_key,
        secret_key=secret_key,
        bucket=bucket,
    )
    return client.list_assets()


@st.cache_data(ttl=300, show_spinner="Listing partitions...")
def _list_partitions_cached(
    endpoint_url: str,
    access_key: str,
    secret_key: str,
    bucket: str,
    prefix: str,
) -> list[str]:
    """Cached partition listing."""
    s3 = S3Client(
        endpoint_url=endpoint_url,
        access_key=access_key,
        secret_key=secret_key,
        bucket=bucket,
    )
    keys = s3.list_all_objects(prefix)
    partitions: set[str] = set()
    for k in keys:
        suffix = k[len(prefix):].lstrip("/")
        parts = suffix.split("/")
        if len(parts) >= 2:
            partitions.add(parts[0])
    return sorted(partitions)


class DataClient:
    """Wraps S3Client with asset discovery and data loading."""

    def __init__(
        self,
        endpoint_url: str,
        access_key: str,
        secret_key: str,
        bucket: str,
    ) -> None:
        self.s3 = S3Client(
            endpoint_url=endpoint_url,
            access_key=access_key,
            secret_key=secret_key,
            bucket=bucket,
        )
        self._endpoint_url = endpoint_url
        self._access_key = access_key
        self._secret_key = secret_key
        self._bucket = bucket

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _conn_args(self) -> tuple[str, str, str, str]:
        """Return connection params tuple for cached helpers."""
        return (self._endpoint_url, self._access_key, self._secret_key, self._bucket)

    def _cached_load(self, asset_root: str, limit: int) -> list[dict]:
        """Delegate to the module-level cached loader."""
        return _load_asset_data(*self._conn_args(), asset_root=asset_root, limit=limit)

    # ------------------------------------------------------------------
    # Asset discovery
    # ------------------------------------------------------------------

    def list_assets(self) -> list[dict]:
        """Walk S3 prefix tree and find all assets with _metadata.json sidecars."""
        all_keys = self.s3.list_all_objects("")
        metadata_keys = [k for k in all_keys if k.endswith("/_metadata.json")]

        assets = []
        for mk in metadata_keys:
            parts = mk.rsplit("/_metadata.json", 1)[0].split("/")
            if len(parts) < 4:
                continue
            layer, code_location, group, asset = parts[0], parts[1], parts[2], parts[3]
            asset_root = "/".join(parts)
            assets.append({
                "layer": layer,
                "code_location": code_location,
                "group": group,
                "asset": asset,
                "root": asset_root,
                "metadata_key": mk,
            })
        return assets

    # ------------------------------------------------------------------
    # Metadata / manifest
    # ------------------------------------------------------------------

    def get_metadata(self, asset_root: str) -> dict | None:
        """Load _metadata.json for an asset (any partition — picks first found)."""
        keys = self.s3.list_all_objects(asset_root + "/")
        meta_keys = [k for k in keys if k.endswith("_metadata.json")]
        if not meta_keys:
            return None
        raw = self.s3.get_object(meta_keys[0])
        return json.loads(raw)

    def get_manifest(self, asset_root: str) -> AssetManifest | None:
        """Load _manifest.json for an asset."""
        key = f"{asset_root}/_manifest.json"
        try:
            raw = self.s3.get_object(key)
            return AssetManifest.model_validate_json(raw)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def load_data(self, asset_root: str, limit: int = 100) -> list[dict]:
        """Fetch data file(s) under an asset root, deserialize, return dicts."""
        keys = self.s3.list_all_objects(asset_root + "/")
        data_keys = [k for k in keys if "/data." in k or k.endswith("/data.jsonl") or k.endswith("/data.json") or k.endswith("/data.pkl")]
        if not data_keys:
            return []

        all_rows: list[dict] = []
        for dk in data_keys:
            if len(all_rows) >= limit:
                break
            ext = "." + dk.rsplit(".", 1)[-1]

            dir_prefix = dk.rsplit("/", 1)[0] + "/"
            meta_keys = [k for k in keys if k.startswith(dir_prefix) and k.endswith("_metadata.json")]
            metadata = {}
            if meta_keys:
                try:
                    metadata = json.loads(self.s3.get_object(meta_keys[0]))
                except Exception:
                    pass

            raw = self.s3.get_object(dk)
            result = deserialize(raw, ext, metadata, type_hint=None)

            if isinstance(result, list):
                all_rows.extend(result[: limit - len(all_rows)])
            elif isinstance(result, dict):
                all_rows.append(result)

        return all_rows[:limit]

    def list_data_keys(self, asset_root: str) -> list[str]:
        """List all data file keys under an asset root."""
        keys = self.s3.list_all_objects(asset_root + "/")
        return [k for k in keys if "/data." in k]

    # ------------------------------------------------------------------
    # Embedding search
    # ------------------------------------------------------------------

    def search_embeddings(
        self,
        query_vec: list[float],
        asset_root: str,
        top_k: int = 10,
    ) -> list[dict]:
        """Cosine similarity search over embedding dicts stored in an asset."""
        rows = self.load_data(asset_root, limit=10000)
        if not rows:
            return []

        embeddings = []
        valid_rows = []
        for r in rows:
            emb = r.get("embedding") or r.get("vector")
            if emb and isinstance(emb, list):
                embeddings.append(emb)
                valid_rows.append(r)

        if not embeddings:
            return []

        q = np.array(query_vec, dtype=np.float32)
        mat = np.array(embeddings, dtype=np.float32)
        norms = np.linalg.norm(mat, axis=1) * np.linalg.norm(q)
        norms = np.where(norms == 0, 1, norms)
        scores = mat @ q / norms
        top_idx = np.argsort(scores)[::-1][:top_k]

        results = []
        for i in top_idx:
            row = {k: v for k, v in valid_rows[i].items() if k not in ("embedding", "vector")}
            row["score"] = float(scores[i])
            results.append(row)
        return results

    # ------------------------------------------------------------------
    # Source & partition discovery
    # ------------------------------------------------------------------

    def list_sources(self) -> list[str]:
        """Return distinct pipeline prefixes extracted from asset names.

        Assets are named like ``congress_entities``, ``leak_propositions``,
        ``media_chunks``.  This method extracts the prefix before the last
        underscore-delimited suffix (entities, propositions, chunks,
        embeddings, documents, transcriptions, metadata) and returns the
        unique pipeline names (e.g. ``["congress", "leak", "media"]``).
        """
        assets = _list_assets_cached(*self._conn_args())
        suffixes = {
            "entities", "propositions", "chunks", "embeddings",
            "documents", "transcriptions", "metadata", "bills",
            "mentions", "assertions", "entity_candidates",
        }
        sources: set[str] = set()
        for a in assets:
            name = a["asset"]
            # Try to split off a known suffix
            for suf in suffixes:
                if name.endswith(f"_{suf}"):
                    prefix = name[: -(len(suf) + 1)]
                    if prefix:
                        sources.add(prefix)
                    break
        return sorted(sources) if sources else sorted({a["code_location"] for a in assets})

    def _find_asset_root(self, source: str, asset_suffix: str, layer: str) -> str | None:
        """Find the S3 root for an asset matching ``{source}_{asset_suffix}`` in *layer*.

        Returns the ``root`` path (e.g. ``silver/default/default/congress_entities``)
        or ``None`` if not found.
        """
        assets = _list_assets_cached(*self._conn_args())
        target = f"{source}_{asset_suffix}"
        for a in assets:
            if a["layer"] == layer and a["asset"] == target:
                return a["root"]
        return None

    def list_partitions(
        self,
        source: str,
        asset_suffix: str,
        layer: str = "silver",
    ) -> list[str]:
        """List available partition keys for a source's asset."""
        root = self._find_asset_root(source, asset_suffix, layer)
        if not root:
            return []
        return _list_partitions_cached(*self._conn_args(), prefix=root + "/")

    # ------------------------------------------------------------------
    # Linguistic & knowledge-graph data exploration
    # ------------------------------------------------------------------

    def load_documents(
        self,
        source: str,
        partition: str | None = None,
        limit: int = 2000,
    ) -> list[dict]:
        """Load document rows for *source*."""
        root = self._find_asset_root(source, "documents", "silver")
        if not root:
            return []
        asset_root = f"{root}/{partition}" if partition else root
        return self._cached_load(asset_root, limit)

    def load_entities(
        self,
        source: str,
        partition: str | None = None,
        limit: int = 5000,
        legacy: bool = False,
    ) -> list[dict]:
        """Load NER entity rows for *source* (e.g. ``congress``, ``leak``).

        When *legacy* is False (default), loads gold-layer mentions instead.
        Set *legacy=True* to use the original silver-layer entities.
        """
        if not legacy:
            logger.info("load_entities: routing to load_mentions for source=%s", source)
            return self.load_mentions(source, partition)

        logger.info("load_entities(legacy=True): loading silver entities for source=%s", source)
        root = self._find_asset_root(source, "entities", "silver")
        if not root:
            return []
        asset_root = f"{root}/{partition}" if partition else root
        return self._cached_load(asset_root, limit)

    def load_propositions(
        self,
        source: str,
        partition: str | None = None,
        limit: int = 5000,
    ) -> list[dict]:
        """Load SPO proposition rows for *source*."""
        root = self._find_asset_root(source, "propositions", "gold")
        if not root:
            return []
        asset_root = f"{root}/{partition}" if partition else root
        return self._cached_load(asset_root, limit)

    def load_chunks(
        self,
        source: str,
        partition: str | None = None,
        limit: int = 5000,
    ) -> list[dict]:
        """Load text-chunk rows for *source*."""
        root = self._find_asset_root(source, "chunks", "silver")
        if not root:
            return []
        asset_root = f"{root}/{partition}" if partition else root
        return self._cached_load(asset_root, limit)

    # ------------------------------------------------------------------
    # EDC data loading (mentions, assertions, canonical entities, alignments)
    # ------------------------------------------------------------------

    def load_mentions(
        self,
        source: str,
        partition: str | None = None,
        mention_type: str | None = None,
        limit: int = 5000,
    ) -> list[dict]:
        """Load gold-layer Mention objects for a source."""
        logger.info("Loading mentions for source=%s", source)
        root = self._find_asset_root(source, "mentions", "gold")
        if root is None:
            logger.warning("No mentions found for source=%s, falling back to legacy entities", source)
            return self.load_entities(source, partition, legacy=True)

        asset_root = f"{root}/{partition}" if partition else root
        records = self._cached_load(asset_root, limit)

        # Validate with Pydantic model
        validated = []
        for r in records:
            try:
                mention = Mention.model_validate(r) if isinstance(r, dict) else r
                if mention_type and mention.mention_type.value != mention_type:
                    continue
                validated.append(mention.model_dump() if isinstance(mention, Mention) else r)
            except Exception as e:
                logger.debug("Skipping invalid mention record: %s", e)
                validated.append(r)  # pass through as dict

        logger.info("Loaded %d mentions for source=%s", len(validated), source)
        return validated

    def load_assertions(
        self,
        source: str,
        partition: str | None = None,
        limit: int = 5000,
    ) -> list[dict]:
        """Load gold-layer Assertion objects for a source."""
        logger.info("Loading assertions for source=%s", source)
        root = self._find_asset_root(source, "assertions", "gold")
        if root is None:
            logger.warning("No assertions found for source=%s, falling back to propositions", source)
            return self.load_propositions(source, partition, limit=limit)

        asset_root = f"{root}/{partition}" if partition else root
        records = self._cached_load(asset_root, limit)

        validated = []
        for r in records:
            try:
                assertion = Assertion.model_validate(r) if isinstance(r, dict) else r
                validated.append(assertion.model_dump() if isinstance(assertion, Assertion) else r)
            except Exception as e:
                logger.debug("Skipping invalid assertion record: %s", e)
                validated.append(r)

        logger.info("Loaded %d assertions for source=%s", len(validated), source)
        return validated

    def load_canonical_entities(
        self,
        partition: str | None = None,
        limit: int = 5000,
    ) -> list[dict]:
        """Load platinum-layer canonical entities from knowledge_graph."""
        logger.info("Loading canonical entities")
        assets = _list_assets_cached(*self._conn_args())
        root = None
        for a in assets:
            if a["layer"] == "platinum" and a["asset"] == "canonical_entities":
                root = a["root"]
                break

        if root is None:
            logger.warning("No canonical_entities found in platinum layer")
            return []

        logger.debug("Resolved canonical_entities root=%s", root)
        asset_root = f"{root}/{partition}" if partition else root
        records = self._cached_load(asset_root, limit)

        validated = []
        for r in records:
            try:
                entity = CanonicalEntity.model_validate(r) if isinstance(r, dict) else r
                validated.append(entity.model_dump() if isinstance(entity, CanonicalEntity) else r)
            except Exception as e:
                logger.debug("Skipping invalid canonical entity record: %s", e)
                validated.append(r)

        logger.info("Loaded %d canonical entities", len(validated))
        return validated

    def load_entity_alignments(
        self,
        partition: str | None = None,
        limit: int = 5000,
    ) -> list[dict]:
        """Load platinum-layer entity alignment edges from knowledge_graph."""
        logger.info("Loading entity alignments")
        assets = _list_assets_cached(*self._conn_args())
        root = None
        for a in assets:
            if a["layer"] == "platinum" and a["asset"] == "entity_alignments":
                root = a["root"]
                break

        if root is None:
            logger.warning("No entity_alignments found in platinum layer")
            return []

        logger.debug("Resolved entity_alignments root=%s", root)
        asset_root = f"{root}/{partition}" if partition else root
        records = self._cached_load(asset_root, limit)

        validated = []
        for r in records:
            try:
                edge = AlignmentEdge.model_validate(r) if isinstance(r, dict) else r
                validated.append(edge.model_dump() if isinstance(edge, AlignmentEdge) else r)
            except Exception as e:
                logger.debug("Skipping invalid alignment edge record: %s", e)
                validated.append(r)

        logger.info("Loaded %d entity alignments", len(validated))
        return validated

    def load_assertion_graph(
        self,
        partition: str | None = None,
        limit: int = 5000,
    ) -> list[dict]:
        """Load platinum-layer cross-source assertion graph from knowledge_graph."""
        logger.info("Loading assertion graph")
        assets = _list_assets_cached(*self._conn_args())
        root = None
        for a in assets:
            if a["layer"] == "platinum" and a["asset"] == "assertion_graph":
                root = a["root"]
                break

        if root is None:
            logger.warning("No assertion_graph found in platinum layer")
            return []

        logger.debug("Resolved assertion_graph root=%s", root)
        asset_root = f"{root}/{partition}" if partition else root
        records = self._cached_load(asset_root, limit)
        logger.info("Loaded %d assertion graph records", len(records))
        return records

    # ------------------------------------------------------------------
    # EDC helper queries
    # ------------------------------------------------------------------

    def get_mentions_for_document(
        self,
        source: str,
        document_id: str,
        partition: str | None = None,
    ) -> list[dict]:
        """Load mentions for a specific document, sorted by span_start."""
        mentions = self.load_mentions(source, partition)
        filtered = [m for m in mentions if m.get("document_id") == document_id]
        filtered.sort(key=lambda m: m.get("span_start") or 0)
        return filtered

    def get_assertions_for_entity(
        self,
        source: str,
        entity_text: str,
        partition: str | None = None,
    ) -> list[dict]:
        """Load assertions where entity_text appears as subject or object."""
        assertions = self.load_assertions(source, partition)
        needle = entity_text.lower()
        return [
            a for a in assertions
            if a.get("subject_text", "").lower() == needle
            or a.get("object_text", "").lower() == needle
        ]

    def get_canonical_entity_by_name(self, name: str) -> dict | None:
        """Search canonical entities by name (case-insensitive)."""
        entities = self.load_canonical_entities()
        needle = name.lower()
        for e in entities:
            if e.get("canonical_name", "").lower() == needle:
                return e
        return None

    # ------------------------------------------------------------------
    # Cross-asset queries
    # ------------------------------------------------------------------

    def get_entity_context(
        self,
        entity_text: str,
        source: str,
        partition: str | None = None,
    ) -> list[dict]:
        """Find all text chunks containing a given entity."""
        entities = self.load_entities(source, partition)
        matching_chunk_ids = {
            e["chunk_id"]
            for e in entities
            if e.get("text", "").lower() == entity_text.lower()
            and "chunk_id" in e
        }
        if not matching_chunk_ids:
            return []
        chunks = self.load_chunks(source, partition)
        return [c for c in chunks if c.get("chunk_id") in matching_chunk_ids]

    def get_entity_propositions(
        self,
        entity_text: str,
        source: str,
        partition: str | None = None,
    ) -> list[dict]:
        """Find all SPO triples where entity is subject or object."""
        propositions = self.load_propositions(source, partition)
        needle = entity_text.lower()
        return [
            p for p in propositions
            if p.get("subject", "").lower() == needle
            or p.get("object", "").lower() == needle
        ]

    def get_document_entities(
        self,
        document_id: str,
        source: str,
        partition: str | None = None,
    ) -> list[dict]:
        """Return all entities extracted from a specific document."""
        entities = self.load_entities(source, partition)
        return [e for e in entities if e.get("source_doc_id") == document_id]

    def get_document_propositions(
        self,
        document_id: str,
        source: str,
        partition: str | None = None,
    ) -> list[dict]:
        """Return all propositions extracted from a specific document."""
        propositions = self.load_propositions(source, partition)
        return [p for p in propositions if p.get("source_doc_id") == document_id]

    # ------------------------------------------------------------------
    # Config discovery
    # ------------------------------------------------------------------

    def list_configs(self, asset_root: str) -> list[str]:
        """Scan S3 prefixes under *asset_root* for ``config=`` segments.

        Returns a list like ``["default", "cs500_co100_te3s", ...]``.
        Assets without a config segment are listed as ``"default"``.
        """
        keys = self.s3.list_all_objects(asset_root + "/")
        configs: set[str] = set()
        has_bare_data = False

        for k in keys:
            suffix = k[len(asset_root):].lstrip("/")
            parts = suffix.split("/")
            for p in parts:
                if p.startswith("config="):
                    configs.add(p.split("=", 1)[1])
                    break
            else:
                if "data." in suffix or "_metadata.json" in suffix:
                    has_bare_data = True

        result = []
        if has_bare_data or not configs:
            result.append("default")
        result.extend(sorted(configs))
        return result

    def load_config_metadata(self, asset_root: str, config_key: str) -> dict | None:
        """Load ``_metadata.json`` for a specific config variant."""
        if config_key == "default":
            prefix = asset_root
        else:
            prefix = f"{asset_root}/config={config_key}"
        try:
            raw = self.s3.get_object(f"{prefix}/_metadata.json")
            return json.loads(raw)
        except Exception:
            return None

    def search_embeddings_with_config(
        self,
        query_vec: list[float],
        asset_root: str,
        config_key: str = "default",
        top_k: int = 10,
    ) -> list[dict]:
        """Search embeddings under a specific config variant."""
        if config_key == "default":
            prefix = asset_root
        else:
            prefix = f"{asset_root}/config={config_key}"
        return self.search_embeddings(query_vec, prefix, top_k=top_k)

    # ------------------------------------------------------------------
    # Cross-asset queries
    # ------------------------------------------------------------------

    def build_entity_cooccurrence(
        self,
        entity_texts: list[str],
        source: str,
        partition: str | None = None,
    ) -> dict[tuple[str, str], int]:
        """Build entity co-occurrence dict: ``{(entity_a, entity_b): count}``.

        Uses mentions (grouped by mention_type) when available, falling back
        to legacy entities.
        """
        mentions = self.load_mentions(source, partition)
        target_set = {t.lower() for t in entity_texts}

        chunk_entities: dict[str, set[str]] = defaultdict(set)
        for m in mentions:
            text_lower = m.get("text", "").lower()
            if text_lower in target_set and "chunk_id" in m:
                # Include mention_type in the key for richer co-occurrence
                mention_type = m.get("mention_type", "OTHER")
                chunk_entities[m["chunk_id"]].add(text_lower)

        cooccurrence: dict[tuple[str, str], int] = defaultdict(int)
        for _chunk_id, ent_set in chunk_entities.items():
            ents = sorted(ent_set)
            for i, a in enumerate(ents):
                for b in ents[i + 1:]:
                    cooccurrence[(a, b)] += 1

        return dict(cooccurrence)
