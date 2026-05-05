"""Tests for ``dagster_io.bench.prompt_store`` (Gap #5).

Validates round-tripping rendered SPO prompts and raw responses through
S3 keyed by content hash + (run_id, chunk_id). Uses an in-memory fake
S3 client to keep the test offline.
"""

from __future__ import annotations

import pytest

from dagster_io.bench.prompt_store import (
    get_prompt,
    get_response,
    put_prompt,
    put_response,
)


class _FakeS3Client:
    """Minimal in-memory stand-in for ``S3Client``.

    Implements only the surface ``prompt_store`` calls into:
    ``put_object``, ``get_object``, ``head_object``. ``head_object``
    returns a dict (truthy) when the key exists, else ``None`` —
    matching the real client's ``NoSuchKey``-handling contract.
    """

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.bucket = "fake-bucket"

    def put_object(self, key: str, data: bytes) -> None:
        self.objects[key] = data

    def get_object(self, key: str) -> bytes:
        if key not in self.objects:
            raise KeyError(key)
        return self.objects[key]

    def head_object(self, key: str):
        return {"ContentLength": len(self.objects[key])} if key in self.objects else None


class _FakeBenchStore:
    def __init__(self) -> None:
        self.client = _FakeS3Client()


@pytest.fixture
def fake_store() -> _FakeBenchStore:
    return _FakeBenchStore()


def test_put_and_get_prompt_roundtrip(fake_store: _FakeBenchStore) -> None:
    text = "system prompt\n\nuser body with unicode: café"
    key = put_prompt(fake_store, "abc1234567890def", text)
    assert key == "bench/prompts/abc1234567890def.txt"
    got = get_prompt(fake_store, "abc1234567890def")
    assert got == text


def test_put_prompt_idempotent_skips_second_write(fake_store: _FakeBenchStore) -> None:
    h = "deadbeef00000000"
    put_prompt(fake_store, h, "first")
    # Mutate the stored bytes directly so we can detect a re-put.
    fake_store.client.objects[f"bench/prompts/{h}.txt"] = b"sentinel"
    # Second put with different text must not overwrite (idempotent).
    put_prompt(fake_store, h, "second different content")
    assert fake_store.client.objects[f"bench/prompts/{h}.txt"] == b"sentinel"


def test_get_prompt_miss_returns_none(fake_store: _FakeBenchStore) -> None:
    assert get_prompt(fake_store, "missingmissing00") is None


def test_put_and_get_response_roundtrip(fake_store: _FakeBenchStore) -> None:
    text = '{"propositions": []}'
    key = put_response(fake_store, "2026-05-04-120000", "doc-1:win-abc12345", text)
    assert key == "bench/responses/2026-05-04-120000/doc-1:win-abc12345.txt"
    got = get_response(fake_store, "2026-05-04-120000", "doc-1:win-abc12345")
    assert got == text


def test_put_response_idempotent(fake_store: _FakeBenchStore) -> None:
    put_response(fake_store, "run-1", "chunk-1", "first")
    fake_store.client.objects["bench/responses/run-1/chunk-1.txt"] = b"sentinel"
    put_response(fake_store, "run-1", "chunk-1", "second")
    assert fake_store.client.objects["bench/responses/run-1/chunk-1.txt"] == b"sentinel"


def test_response_chunk_id_with_slash_is_safe(fake_store: _FakeBenchStore) -> None:
    # ``/`` in chunk_id would partition under a fake prefix; the helper
    # rewrites it to ``_`` so the response lands at exactly one key.
    put_response(fake_store, "run-1", "doc/with/slashes", "body")
    assert "bench/responses/run-1/doc_with_slashes.txt" in fake_store.client.objects


def test_get_response_miss_returns_none(fake_store: _FakeBenchStore) -> None:
    assert get_response(fake_store, "run-x", "chunk-x") is None
