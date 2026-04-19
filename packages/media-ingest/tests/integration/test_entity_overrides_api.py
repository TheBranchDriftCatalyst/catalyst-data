"""Tests for HITL entity override API routes."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def mock_store():
    store = MagicMock()
    store.list_entity_overrides.return_value = [
        {
            "override_id": "ovr-1",
            "alias_text": "Trump",
            "target_name": "Donald Trump",
            "entity_type": "PERSON",
            "reviewer": "",
            "notes": "Single-name alias",
            "is_active": True,
            "created_at": "2026-04-14T00:00:00+00:00",
            "updated_at": "2026-04-14T00:00:00+00:00",
        }
    ]
    store.create_entity_override.return_value = {
        "override_id": "ovr-new",
        "alias_text": "Pelosi",
        "target_name": "Nancy Pelosi",
        "entity_type": "PERSON",
    }
    store.delete_entity_override.return_value = True
    store.toggle_entity_override.return_value = {"override_id": "ovr-1", "is_active": False}
    return store


@pytest.fixture
def client(mock_store):
    from media_ingest.viewer.routes.annotations import router, set_store

    set_store(mock_store)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestEntityOverridesAPI:
    def test_list(self, client, mock_store):
        resp = client.get("/viewer/api/entity-overrides")
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert resp.json()[0]["alias_text"] == "Trump"

    def test_list_include_inactive(self, client, mock_store):
        resp = client.get("/viewer/api/entity-overrides?active_only=false")
        assert resp.status_code == 200
        mock_store.list_entity_overrides.assert_called_once_with(False)

    def test_create(self, client, mock_store):
        resp = client.post(
            "/viewer/api/entity-overrides",
            json={"alias_text": "Pelosi", "target_name": "Nancy Pelosi", "entity_type": "PERSON"},
        )
        assert resp.status_code == 200
        assert resp.json()["override_id"] == "ovr-new"

    def test_create_validation_empty_alias(self, client):
        resp = client.post(
            "/viewer/api/entity-overrides",
            json={"alias_text": "", "target_name": "Donald Trump", "entity_type": "PERSON"},
        )
        assert resp.status_code == 422

    def test_delete(self, client, mock_store):
        resp = client.delete("/viewer/api/entity-overrides/ovr-1")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

    def test_toggle(self, client, mock_store):
        resp = client.patch("/viewer/api/entity-overrides/ovr-1", json={"is_active": False})
        assert resp.status_code == 200
        mock_store.toggle_entity_override.assert_called_once_with("ovr-1", False)
