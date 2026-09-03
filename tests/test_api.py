from __future__ import annotations

import pytest

from partrisk.core import config
from partrisk.api import services as geocoding_service
from partrisk.serving import alerts as alert_store
from tests.conftest import needs_database, needs_internet, needs_models

pytestmark = [needs_database, needs_models]

HORIZONS = config.PREDICTION_HORIZON_DAYS


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in ("ok", "degraded")
    assert body["model_version"]["failure"]


def test_health_dengan_cek_database(client):
    body = client.get("/health", params={"check_database": True}).json()
    assert body["database"] == "reachable"


def test_model_info(client):
    body = client.get("/api/v1/model").json()
    assert body["failure"]["model_version"]
    assert "risk_cutoffs" in body["failure"]
    assert body["failure"]["horizons_days"] == HORIZONS


def test_prediksi_kerusakan_satu_part(client, scorable_item):
    body = client.get(f"/api/v1/parts/{scorable_item}/failure").json()
    assert body["status"] == "SCORED"
    failure = body["failure"]
    assert failure["risk_level"] in ("LOW", "MEDIUM", "HIGH")
    for days in HORIZONS:
        assert 0.0 <= failure[f"failure_probability_{days}d"] <= 1.0


def test_risiko_kumulatif_tidak_menurun(client, scorable_item):
    failure = client.get(f"/api/v1/parts/{scorable_item}/failure").json()["failure"]
    values = [failure[f"failure_probability_{days}d"] for days in HORIZONS]
    assert values == sorted(values)


def test_assessment_gabungan(client, scorable_item):
    body = client.get(f"/api/v1/parts/{scorable_item}/assessment").json()
    assert body["status"] == "SCORED"
    assert body["failure"]["risk_level"]
    assert body["recommendation"]["action"]
    assert body["recommendation"]["based_on"]["failure_risk_level"] == (
        body["failure"]["risk_level"]
    )
    assert body["model_version"]["failure"]
    assert body["explanation"]["disclaimer"]
    assert isinstance(body["explanation"]["factors"], list)


def test_assessment_tanpa_penjelasan(client, scorable_item):
    body = client.get(
        f"/api/v1/parts/{scorable_item}/assessment", params={"explain": False}
    ).json()
    assert body["status"] == "SCORED"
    assert body["explanation"] is None


def test_part_tidak_ditemukan(client):
    response = client.get("/api/v1/parts/PART-YANG-TIDAK-PERNAH-ADA/assessment")
    assert response.status_code == 404
    body = response.json()
    assert body["status"] == "NOT_FOUND"
    assert "password" not in response.text.lower()
    assert "psycopg" not in response.text.lower()


def test_part_ada_tapi_tidak_bisa_diskor(client, not_scorable_item):
    response = client.get(f"/api/v1/parts/{not_scorable_item}/assessment")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "NOT_SCORABLE"
    assert body["reason"]
    assert body["failure"] is None
    assert body["recommendation"] is None


def test_daftar_rekomendasi(client):

    body = client.get(
        "/api/v1/recommendations", params={"limit": 5, "official_queue_only": False}
    ).json()
    assert body["returned"] <= 5
    assert body["total"] >= body["returned"]
    assert body["scored_at"]["data_through"]
    ranks = [item["rank"] for item in body["items"]]
    assert ranks == sorted(ranks)
    for item in body["items"]:
        assert item["recommended_action"]
        assert item["priority"]


def test_saring_rekomendasi_berdasar_risiko(client):
    body = client.get(
        "/api/v1/recommendations",
        params={"risk": "HIGH", "limit": 20, "official_queue_only": False},
    ).json()
    assert all(item["failure_risk_level"] == "HIGH" for item in body["items"])


def test_saring_rekomendasi_berdasar_jenis_part(client):
    filters = client.get("/api/v1/filters").json()
    if not filters["item_types"]:
        return
    item_type = filters["item_types"][0]
    body = client.get(
        "/api/v1/recommendations",
        params={"item_type": item_type, "limit": 10, "official_queue_only": False},
    ).json()
    assert all(item["item_type"] == item_type for item in body["items"])


def test_cari_sebagian_item_id(client):
    full = client.get(
        "/api/v1/recommendations", params={"limit": 1, "official_queue_only": False}
    ).json()["items"][0]
    fragment = full["item_id"][:7]
    body = client.get(
        "/api/v1/recommendations",
        params={"search": fragment, "limit": 50, "official_queue_only": False},
    ).json()
    assert body["total"] >= 1
    assert all(fragment in item["item_id"] for item in body["items"])
    assert any(item["item_id"] == full["item_id"] for item in body["items"])


def test_cari_yang_tidak_cocok_mengembalikan_kosong(client):
    body = client.get(
        "/api/v1/recommendations", params={"search": "TIDAK-ADA-INI"}
    ).json()
    assert body["total"] == 0
    assert body["items"] == []


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
    assert client.get("/api/v1/model").status_code == 200


def test_require_api_key_menolak_tanpa_header_saat_dikonfigurasi(client, monkeypatch):
    import partrisk.api.app as api_app

    monkeypatch.setattr(api_app, "API_KEY", "rahasia-test")
    assert client.get("/api/v1/model").status_code == 401


def test_require_api_key_menolak_header_yang_salah(client, monkeypatch):
    import partrisk.api.app as api_app

    monkeypatch.setattr(api_app, "API_KEY", "rahasia-test")
    response = client.get("/api/v1/model", headers={"X-API-Key": "salah"})
    assert response.status_code == 401


def test_require_api_key_menerima_header_yang_cocok(client, monkeypatch):
    import partrisk.api.app as api_app

    monkeypatch.setattr(api_app, "API_KEY", "rahasia-test")
    response = client.get("/api/v1/model", headers={"X-API-Key": "rahasia-test"})
    assert response.status_code == 200


def test_require_api_key_health_tetap_terbuka_walau_dikonfigurasi(client, monkeypatch):

    import partrisk.api.app as api_app

    monkeypatch.setattr(api_app, "API_KEY", "rahasia-test")
    assert client.get("/health").status_code == 200


def test_paging_rekomendasi(client):
    first = client.get(
        "/api/v1/recommendations", params={"limit": 3, "official_queue_only": False}
    ).json()
    second = client.get(
        "/api/v1/recommendations",
        params={"limit": 3, "offset": 3, "official_queue_only": False},
    ).json()
    assert first["total"] == second["total"]
    ids = {item["item_id"] for item in first["items"]}
    assert ids.isdisjoint({item["item_id"] for item in second["items"]})


def test_overview(client):
    body = client.get("/api/v1/overview", params={"top": 5}).json()
    summary = body["summary"]
    assert summary["active_parts"] > 0
    assert (
        summary["high_risk_parts"]
        + summary["medium_risk_parts"]
        + summary["low_risk_parts"]
        == summary["active_parts"]
    )
    assert len(body["top_priority"]) <= 5

    assert 0 <= summary["official_queue_size"] <= summary["active_parts"]


def test_batas_limit_dijaga(client):
    from partrisk.api import app as api

    body = client.get(
        "/api/v1/recommendations",
        params={"limit": api.MAX_RECOMMENDATION_LIMIT * 10, "official_queue_only": False},
    ).json()
    assert body["returned"] <= api.MAX_RECOMMENDATION_LIMIT


def test_antrian_resmi_default_digerbang_dan_boleh_kosong(client):

    response = client.get("/api/v1/recommendations", params={"limit": 500})
    assert response.status_code == 200
    body = response.json()
    assert body["returned"] == len(body["items"])
    assert body["total"] >= body["returned"]
    assert all(item["gate_flagged"] for item in body["items"])


def test_endpoint_terminals(client):
    body = client.get("/api/v1/terminals").json()
    assert body["parts_with_terminal"] + body["parts_without_terminal"] >= 0
    assert body["terminals_total"] == len(body["terminals"])
    for terminal in body["terminals"]:
        assert terminal["active_parts"] == (
            terminal["high_risk_parts"] + terminal["medium_risk_parts"] + terminal["low_risk_parts"]
        )

    high_then_medium = [(t["high_risk_parts"], t["medium_risk_parts"]) for t in body["terminals"]]
    assert high_then_medium == sorted(high_then_medium, reverse=True)


def test_recommendations_terminal_id_menyaring_ke_satu_terminal(client):
    terminals = client.get("/api/v1/terminals").json()["terminals"]
    if not terminals:
        pytest.skip("Tidak ada Terminal dengan PART aktif di database saat ini.")
    target = terminals[0]
    body = client.get(
        "/api/v1/recommendations",
        params={"terminal_id": target["terminal_id"], "official_queue_only": False, "limit": 500},
    ).json()
    assert body["total"] == target["active_parts"]
    assert all(item["terminal_id"] == target["terminal_id"] for item in body["items"])


def test_antrian_resmi_subset_dari_mode_eksplorasi(client):

    official = client.get(
        "/api/v1/recommendations", params={"limit": 500}
    ).json()
    exploratory = client.get(
        "/api/v1/recommendations", params={"limit": 500, "official_queue_only": False}
    ).json()
    assert exploratory["total"] >= official["total"]
    official_ids = {item["item_id"] for item in official["items"]}
    exploratory_ids = {item["item_id"] for item in exploratory["items"]}
    assert official_ids <= exploratory_ids


def test_resolve_alert_yang_tidak_ada_mengembalikan_404(client):
    response = client.post(
        "/api/v1/parts/PART-TANPA-ALERT-SAMA-SEKALI/resolve-alert"
    )
    assert response.status_code == 404
    assert response.json()["status"] == "NOT_FOUND"


def test_resolve_alert_membuka_alert_baru_kalau_masih_berisiko(client):

    from partrisk.serving import batch as serving_batch

    alert_store.clear()
    serving_batch.score_active_parts(force_refresh=True)
    official = client.get("/api/v1/recommendations", params={"limit": 500}).json()
    if official["total"] == 0:
        pytest.skip("tidak ada PART di antrian resmi saat ini untuk diuji")
    item = official["items"][0]
    assert item["alert_status"] == "OPEN"
    first_opened_at = item["alert_opened_at"]

    resolve_response = client.post(f"/api/v1/parts/{item['item_id']}/resolve-alert")
    assert resolve_response.status_code == 200
    assert resolve_response.json() == {"item_id": item["item_id"], "status": "RESOLVED"}

    serving_batch.score_active_parts(force_refresh=True)
    refreshed = client.get("/api/v1/recommendations", params={"limit": 500}).json()
    refreshed_item = next(i for i in refreshed["items"] if i["item_id"] == item["item_id"])
    assert refreshed_item["alert_status"] == "OPEN"
    assert refreshed_item["alert_opened_at"] != first_opened_at


def test_tidak_ada_endpoint_training(client):
    for path in ("/train", "/api/v1/train", "/api/v1/model/train"):
        assert client.post(path).status_code in (404, 405)


def test_assessment_cocok_dengan_daftar_prioritas(client):
    listed = client.get("/api/v1/recommendations", params={"limit": 1}).json()["items"][0]
    detail = client.get(f"/api/v1/parts/{listed['item_id']}/assessment").json()
    assert detail["status"] == "SCORED"
    for days in HORIZONS:
        column = f"failure_probability_{days}d"
        assert detail["failure"][column] == listed[column]
    assert detail["failure"]["risk_level"] == listed["failure_risk_level"]
    assert detail["recommendation"]["action"] == listed["recommended_action"]


@pytest.fixture(scope="module")
def location_response(client):
    result = client.get(
        "/api/v1/locations/map", params={"resolve": True, "budget_seconds": 8}
    )
    assert result.status_code == 200
    return result.json()


@needs_internet
def test_bentuk_jawaban(location_response):
    assert "resolved" in location_response
    assert "unresolved" in location_response
    assert location_response["scored_at"]["data_through"]


@needs_internet
def test_titik_yang_ditampilkan_selalu_di_dalam_indonesia(location_response):
    box = geocoding_service.INDONESIA_BBOX
    for item in location_response["resolved"]:
        assert box["south"] <= item["lat"] <= box["north"], item
        assert box["west"] <= item["lon"] <= box["east"], item


@needs_internet
def test_setiap_lokasi_punya_hitungan_risiko(location_response):
    for item in location_response["resolved"] + location_response["unresolved"]:
        assert item["active_parts"] >= 1
        assert item["high_risk_parts"] >= 0
        assert item["medium_risk_parts"] >= 0


@needs_internet
def test_tanpa_resolve_tidak_mengubah_cache(client):
    before = client.get(
        "/api/v1/locations/map", params={"resolve": False}
    ).json()
    after = client.get(
        "/api/v1/locations/map", params={"resolve": False}
    ).json()
    assert len(before["resolved"]) == len(after["resolved"])
    assert len(before["unresolved"]) == len(after["unresolved"])


@needs_internet
def test_fasilitas_internal_tidak_pernah_muncul_sebagai_pin(location_response):
    resolved_names = {item["location"] for item in location_response["resolved"]}
    for internal_name in ("GUDANG NI", "SERVICE CENTER", "DIPO DEPOK"):
        assert internal_name not in resolved_names
