"""S3 data access layer for the explorer UI."""

from __future__ import annotations

import json

import numpy as np
from dagster_io.manifest import AssetManifest
from dagster_io.s3_client import S3Client
from dagster_io.serializers import deserialize


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
            # layout: {layer}/{code_location}/{group}/{asset}/.../_metadata.json
            # The asset root is everything before _metadata.json's parent dir
            # Minimum: layer/code_location/group/asset/_metadata.json (4 parts)
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

            # Find the matching _metadata.json in the same directory
            dir_prefix = dk.rsplit("/", 1)[0] + "/"
            meta_keys = [k for k in keys if k.startswith(dir_prefix) and k.endswith("_metadata.json")]
            metadata = {}
            if meta_keys:
                try:
                    metadata = json.loads(self.s3.get_object(meta_keys[0]))
                except Exception:
                    pass

            raw = self.s3.get_object(dk)
            # Deserialize without type hint — returns dicts/lists
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

        # Expect rows with 'embedding' (list[float]) and other fields
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
        # Cosine similarity
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
