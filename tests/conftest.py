from __future__ import annotations

import os

import pytest


def _database_available() -> bool:
    try:
        from partrisk.core import data_reader

        with data_reader.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        return True
    except Exception:  # noqa: BLE001
        return False


def _models_available() -> bool:
    try:
        from partrisk.serving import single as serving

        serving.versions()
        return True
    except Exception:  # noqa: BLE001
        return False


def _internet_available() -> bool:
    try:
        import requests

        requests.get("https://nominatim.openstreetmap.org/status", timeout=5)
        return True
    except Exception:  # noqa: BLE001
        return False


_STRICT = os.getenv("REQUIRE_DATABASE", "").lower() in ("1", "true", "yes")

_HAS_DATABASE = _database_available()
_HAS_MODELS = _models_available()

if _STRICT and not (_HAS_DATABASE and _HAS_MODELS):
    missing = []
    if not _HAS_DATABASE:
        missing.append("database")
    if not _HAS_MODELS:
        missing.append("model production")
    raise RuntimeError(
        "REQUIRE_DATABASE diaktifkan tetapi " + " dan ".join(missing) + " tidak tersedia."
    )

needs_database = pytest.mark.skipif(
    not _HAS_DATABASE, reason="database tidak bisa dihubungi"
)
needs_models = pytest.mark.skipif(
    not _HAS_MODELS, reason="model production belum ada di models/"
)
needs_internet = pytest.mark.skipif(
    not _internet_available(), reason="tidak ada akses internet (geocoding)"
)


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from partrisk.api.app import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="session")
def batch():
    from partrisk.serving import batch as serving_batch

    return serving_batch.score_active_parts()


@pytest.fixture(scope="session")
def scorable_item(batch) -> str:
    return str(batch.frame["item_id"].iloc[0])


@pytest.fixture(scope="session")
def not_scorable_item(batch) -> str:
    from partrisk.core import data_reader

    active = set(batch.frame["item_id"])
    events = data_reader.get_events()
    for item in events["item_identifier_clean"].dropna().unique():
        if item not in active:
            return str(item)
    pytest.skip("semua PART di database sedang aktif")
