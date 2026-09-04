from __future__ import annotations

import pytest

from tests.conftest import needs_database, needs_models

pytestmark = [needs_database, needs_models]


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in ("ok", "degraded")
    assert body["model_version"]["failure"]


def test_health_dengan_cek_database(client):
    body = client.get("/health", params={"check_database": True}).json()
    assert body["database"] == "reachable"


def test_cors_tertutup_secara_bawaan(client):
    from partrisk.api import app as api

    if api.CORS_ALLOW_ORIGINS:
        pytest.skip("CORS memang sedang dikonfigurasi di environment ini")
    response = client.get("/health", headers={"Origin": "http://jahat.example"})
    assert "access-control-allow-origin" not in response.headers


def test_cors_aktif_saat_origin_didaftarkan(monkeypatch):
    import importlib

    from fastapi.testclient import TestClient

    import partrisk.api.app

    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "http://localhost:3000")
    saved_pool, saved_configured = partrisk.api.app._pool, partrisk.api.app._configured
    module = importlib.reload(partrisk.api.app)
    module._pool, module._configured = saved_pool, saved_configured
    try:
        with TestClient(module.app) as configured:
            response = configured.get(
                "/health", headers={"Origin": "http://localhost:3000"}
            )
        assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"
    finally:
        monkeypatch.undo()
        saved_pool, saved_configured = partrisk.api.app._pool, partrisk.api.app._configured
        importlib.reload(partrisk.api.app)
        partrisk.api.app._pool, partrisk.api.app._configured = saved_pool, saved_configured


def test_require_api_key_terbuka_tanpa_konfigurasi(client, monkeypatch):
    import partrisk.api.app as api_app

    monkeypatch.setattr(api_app, "API_KEY", None)
    response = client.post("/api/v1/interventions", json={"host_serial_code": "TIDAK-ADA"})
    assert response.status_code != 401


def test_require_api_key_menolak_tanpa_header_saat_dikonfigurasi(client, monkeypatch):
    import partrisk.api.app as api_app

    monkeypatch.setattr(api_app, "API_KEY", "rahasia-test")
    response = client.post("/api/v1/interventions", json={"host_serial_code": "TIDAK-ADA"})
    assert response.status_code == 401


def test_require_api_key_menolak_header_yang_salah(client, monkeypatch):
    import partrisk.api.app as api_app

    monkeypatch.setattr(api_app, "API_KEY", "rahasia-test")
    response = client.post(
        "/api/v1/interventions",
        json={"host_serial_code": "TIDAK-ADA"},
        headers={"X-API-Key": "salah"},
    )
    assert response.status_code == 401


def test_require_api_key_menerima_header_yang_cocok(client, monkeypatch):
    import partrisk.api.app as api_app

    monkeypatch.setattr(api_app, "API_KEY", "rahasia-test")
    response = client.post(
        "/api/v1/interventions",
        json={"host_serial_code": "TIDAK-ADA"},
        headers={"X-API-Key": "rahasia-test"},
    )
    assert response.status_code != 401


def test_require_api_key_health_tetap_terbuka_walau_dikonfigurasi(client, monkeypatch):
    import partrisk.api.app as api_app

    monkeypatch.setattr(api_app, "API_KEY", "rahasia-test")
    assert client.get("/health").status_code == 200


def test_tidak_ada_endpoint_training(client):
    for path in ("/train", "/api/v1/train", "/api/v1/model/train"):
        assert client.post(path).status_code in (404, 405)


def test_intervention_endpoint_serial_code_tidak_ada_mengembalikan_404(client):
    response = client.post(
        "/api/v1/interventions",
        json={"host_serial_code": "TIDAK-ADA-SERIAL-CODE-INI"},
    )
    assert response.status_code == 404
    assert response.json()["status"] == "NOT_FOUND"


def test_intervention_endpoint_tanpa_host_serial_code_ditolak(client):
    response = client.post("/api/v1/interventions", json={})
    assert response.status_code == 422
