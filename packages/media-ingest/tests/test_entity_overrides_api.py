"""Tests for HITL entity override API routes + annotation store methods."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def mock_store():
    """Create a mock annotation store with override methods."""
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
    """Create test client with mocked store."""
    from media_ingest.viewer.routes.annotations import router, set_store

    set_store(mock_store)

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_list_overrides(client, mock_store):
    resp = client.get("/viewer/api/entity-overrides")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["alias_text"] == "Trump"
    mock_store.list_entity_overrides.assert_called_once_with(True)


def test_list_overrides_include_inactive(client, mock_store):
    resp = client.get("/viewer/api/entity-overrides?active_only=false")
    assert resp.status_code == 200
    mock_store.list_entity_overrides.assert_called_once_with(False)


def test_create_override(client, mock_store):
    resp = client.post(
        "/viewer/api/entity-overrides",
        json={
            "alias_text": "Pelosi",
            "target_name": "Nancy Pelosi",
            "entity_type": "PERSON",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["override_id"] == "ovr-new"
    mock_store.create_entity_override.assert_called_once()


def test_create_override_validation(client):
    """Empty alias_text should fail validation."""
    resp = client.post(
        "/viewer/api/entity-overrides",
        json={
            "alias_text": "",
            "target_name": "Donald Trump",
            "entity_type": "PERSON",
        },
    )
    assert resp.status_code == 422


def test_delete_override(client, mock_store):
    resp = client.delete("/viewer/api/entity-overrides/ovr-1")
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True
    mock_store.delete_entity_override.assert_called_once_with("ovr-1")


def test_toggle_override(client, mock_store):
    resp = client.patch(
        "/viewer/api/entity-overrides/ovr-1",
        json={"is_active": False},
    )
    assert resp.status_code == 200
    mock_store.toggle_entity_override.assert_called_once_with("ovr-1", False)
