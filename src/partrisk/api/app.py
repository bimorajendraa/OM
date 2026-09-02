from __future__ import annotations

import atexit
import logging
import os
import secrets
import sys
import threading
from contextlib import asynccontextmanager

import pandas as pd
import psycopg_pool
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Path, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from partrisk.core import config
from partrisk.core import data_reader
from partrisk.serving import single as serving
from partrisk.serving import batch as serving_batch
from partrisk.serving.single import (
    AlertNotFound,
    DataSourceUnavailable,
    ModelUnavailable,
    PartNotFound,
    PartNotScorable,
)
from partrisk.api import services as geocoding_service
from partrisk.api import services as monitoring_service
from partrisk.api.schemas import (
    AssessmentResponse,
    FailureResponse,
    FiltersResponse,
    HealthResponse,
    HistoryResponse,
    LocationMapResponse,
    OverviewResponse,
    RecommendationListResponse,
    ResolveAlertResponse,
    ScrapResponse,
    TerminalListResponse,
)


_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_configured = False


def _setup_logging() -> None:
    global _configured
    if _configured:
        return

    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATE_FORMAT))

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(handler)

    _configured = True


_setup_logging()
logger = logging.getLogger("production_ml.api")


load_dotenv(config.ENV_FILE)

WARMUP_BATCH_ON_STARTUP = os.getenv("WARMUP_BATCH_ON_STARTUP", "false").lower() in (
    "1", "true", "yes"
)

MAX_RECOMMENDATION_LIMIT = config._int("MAX_RECOMMENDATION_LIMIT", 500)

GEOCODE_BUDGET_SECONDS_DEFAULT = config._int("GEOCODE_BUDGET_SECONDS_DEFAULT", 60)
GEOCODE_BUDGET_SECONDS_MAX = config._int("GEOCODE_BUDGET_SECONDS_MAX", 90)

CORS_ALLOW_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ALLOW_ORIGINS", "").split(",")
    if origin.strip()
]
DEFAULT_RECOMMENDATION_LIMIT = config._int("DEFAULT_RECOMMENDATION_LIMIT", 50)

API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")


API_KEY = os.getenv("API_KEY", "").strip() or None


def require_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> None:
    if API_KEY is None:
        return
    if x_api_key is None or not secrets.compare_digest(x_api_key, API_KEY):
        raise HTTPException(
            status_code=401,
            detail="API key tidak valid atau tidak disertakan (header X-API-Key).",
        )


_db_pool_logger = logging.getLogger("partrisk.api.db_pool")

_pool: psycopg_pool.ConnectionPool | None = None
_pool_lock = threading.Lock()

DB_POOL_MIN_SIZE = config._int("DB_POOL_MIN_SIZE", 1)
DB_POOL_MAX_SIZE = config._int("DB_POOL_MAX_SIZE", 8)


def db_pool_install() -> None:
    global _pool
    with _pool_lock:
        if _pool is not None and not _pool.closed:
            return

        _pool = psycopg_pool.ConnectionPool(
            conninfo="",
            kwargs={
                **config.db_settings(),
                "application_name": "production_ml_api",
                "options": "-c default_transaction_read_only=on",
            },
            min_size=DB_POOL_MIN_SIZE,
            max_size=DB_POOL_MAX_SIZE,
            open=True,
        )
        data_reader.connect = _pool.connection
        _db_pool_logger.info(
            "Connection pool database siap (min=%d, max=%d)", DB_POOL_MIN_SIZE, DB_POOL_MAX_SIZE
        )

        atexit.register(db_pool_teardown)


def db_pool_teardown() -> None:
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.close()
            _pool = None


def db_pool_stats() -> dict:
    if _pool is None:
        return {"installed": False}
    info = _pool.get_stats()
    return {
        "installed": True,
        "pool_min": DB_POOL_MIN_SIZE,
        "pool_max": DB_POOL_MAX_SIZE,
        "connections_in_use": info.get("pool_size", 0) - info.get("pool_available", 0),
        "connections_idle": info.get("pool_available", 0),
    }


health_router = APIRouter(tags=["health"])

API_VERSION = "1.0.0"


@health_router.get("/health", response_model=HealthResponse)
def health(check_database: bool = False) -> dict:
    """Status aplikasi.

    Pemeriksaan database TIDAK dijalankan secara default: /health sering
    dipanggil health checker setiap beberapa detik, dan satu query per
    panggilan hanya membebani database tanpa menambah informasi. Pakai
    ?check_database=true kalau memang ingin memastikan koneksinya hidup.
    """
    try:
        versions: dict[str, str | None] = dict(serving.versions())
        model_ok = True
    except ModelUnavailable:
        versions = {"failure": None, "scrap": None}
        model_ok = False

    database = "unchecked"
    if check_database:
        try:
            with data_reader.connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
            database = "reachable"
        except Exception:  # noqa: BLE001
            database = "unreachable"

    cached = serving_batch.cached_scores()
    return {
        "status": "ok" if model_ok and database != "unreachable" else "degraded",
        "api_version": API_VERSION,
        "model_version": versions,
        "database": database,
        "connection_pool": db_pool_stats(),
        "batch_cache": {
            "ready": cached is not None,
            "rows": int(len(cached.frame)) if cached else 0,
            "computed_seconds_ago": int(cached.age_seconds) if cached else None,
            "data_through": str(cached.data_end) if cached else None,
        },
    }


model_info_router = APIRouter(prefix="/api/v1", tags=["model"])


@model_info_router.get("/model")
def model_info() -> dict:
    """Versi, target, fitur, ambang risiko, dan metrik uji kedua model.

    Seluruhnya dibaca dari metadata.json yang ditulis train.py /
    train_scrap.py - tidak ada angka yang dihitung ulang di sini.
    """
    return serving.describe()


prediction_router = APIRouter(prefix="/api/v1/parts", tags=["parts"])

_ITEM_ID = Path(
    description="ID PART, mis. 011201100101164. Fitur ML dibangun otomatis.",
    min_length=1,
    max_length=100,
)


def _not_scorable(error: PartNotScorable) -> dict:
    return {
        "item_id": error.item_id,
        "status": "NOT_SCORABLE",
        "reason": error.reason,
    }


@prediction_router.get("/{item_id}/failure", response_model=FailureResponse)
def failure(item_id: str = _ITEM_ID) -> dict:
    """Peluang PART rusak dalam 30/60/90/120 hari ke depan.

    Ini PELUANG, bukan perkiraan tanggal kerusakan.
    """
    try:
        prediction = serving.predict_failure(item_id)
    except PartNotScorable as error:
        return _not_scorable(error)
    return {"item_id": prediction["item_id"], "status": "SCORED", "failure": prediction}


@prediction_router.get("/{item_id}/scrap", response_model=ScrapResponse)
def scrap(item_id: str = _ITEM_ID) -> dict:
    """Kalau PART ini rusak, peluang tidak bisa diperbaiki.

    BERSYARAT terhadap kerusakan - bukan peluang PART ini rusak.
    """
    try:
        prediction = serving.predict_scrap(item_id)
    except PartNotScorable as error:
        return _not_scorable(error)
    return {"item_id": prediction["item_id"], "status": "SCORED", "scrap": prediction}


@prediction_router.get("/{item_id}/history", response_model=HistoryResponse)
def history(item_id: str = _ITEM_ID) -> dict:
    """Tanggal kerusakan dan lokasi yang pernah tercatat untuk satu PART.

    Dari catatan event apa adanya, bukan dihitung ulang - mendukung faktor
    risiko di /assessment yang berupa hitungan (mis. "2 kerusakan dalam 365
    hari terakhir") dengan tanggal sesungguhnya.
    """
    return serving.item_history(item_id)


@prediction_router.post("/{item_id}/resolve-alert", response_model=ResolveAlertResponse)
def resolve_alert(item_id: str = _ITEM_ID) -> dict:
    """Tandai alert PART ini selesai diinspeksi/dimaintenance.

    Dipanggil tim ops setelah inspeksi/perbaikan selesai - PART keluar dari
    antrian resmi sampai siklus batch berikutnya menilainya ulang dari data
    terbaru, dan hanya masuk lagi (alert baru) kalau memang masih memenuhi
    aturan risiko.
    """
    return serving.resolve_alert(item_id)


@prediction_router.get("/{item_id}/assessment", response_model=AssessmentResponse)
def assessment(
    item_id: str = _ITEM_ID,
    explain: bool = Query(
        True,
        description=(
            "Sertakan faktor risiko. Perlu satu putaran pembacaan riwayat "
            "tambahan, matikan kalau hanya butuh angkanya."
        ),
    ),
) -> dict:
    """Gabungan risiko kerusakan + risiko scrap + rekomendasi tindakan."""
    try:
        return serving.get_part_assessment(item_id, include_explanation=explain)
    except PartNotScorable as error:
        return _not_scorable(error)


recommendations_router = APIRouter(prefix="/api/v1", tags=["recommendations"])

_INTERNAL_COLUMNS = ["tier_score", "in_official_queue"]


def _rows(frame: pd.DataFrame) -> list[dict]:
    clean = frame.drop(columns=_INTERNAL_COLUMNS, errors="ignore")
    return [
        {key: (None if pd.isna(value) else value) for key, value in record.items()}
        for record in clean.to_dict(orient="records")
    ]


@recommendations_router.get("/recommendations", response_model=RecommendationListResponse)
def recommendations(
    risk: str | None = Query(None, description="Saring kelompok risiko kerusakan: LOW/MEDIUM/HIGH"),
    priority: str | None = Query(None, description="Saring prioritas: LOW/MEDIUM/HIGH/CRITICAL"),
    item_type: str | None = Query(None, description="Saring jenis PART, mis. MOTOR"),
    client: str | None = Query(None, description="Saring client"),
    location: str | None = Query(None, description="Saring lokasi terakhir tercatat"),
    terminal_id: str | None = Query(
        None, description="Saring PART milik satu Terminal (terminal_id dari /api/v1/terminals)"
    ),
    search: str | None = Query(
        None, description="Cari sebagian ID PART, mis. 0112011 (tidak harus lengkap)"
    ),
    replacement_candidates_only: bool = Query(
        False,
        description=(
            "Hanya PART dengan risiko kerusakan MEDIUM/HIGH sekaligus risiko "
            "scrap HIGH - kandidat perencanaan penggantian."
        ),
    ),
    official_queue_only: bool = Query(
        True,
        description=(
            "True (default): antrian RESMI - hanya PART yang lolos gerbang "
            "presisi (docs/DECISIONS.md §11), ukurannya dinamis dan bisa "
            "kosong kalau memang tidak ada yang cukup meyakinkan. False: "
            "seluruh armada terurut skor, untuk eksplorasi manual."
        ),
    ),
    limit: int = Query(DEFAULT_RECOMMENDATION_LIMIT, ge=1),
    offset: int = Query(0, ge=0),
) -> dict:
    """PART yang paling perlu diperhatikan, terurut dari yang paling berisiko."""
    limit = min(limit, MAX_RECOMMENDATION_LIMIT)
    scores = serving_batch.score_active_parts()
    selected = serving_batch.filter_scores(
        scores.frame,
        risk=risk,
        priority=priority,
        item_type=item_type,
        client=client,
        location=location,
        terminal_id=terminal_id,
        search=search,
        replacement_candidates_only=replacement_candidates_only,
        official_queue_only=official_queue_only,
    )
    page = selected.iloc[offset : offset + limit]
    return {
        "total": int(len(selected)),
        "returned": int(len(page)),
        "offset": offset,
        "scored_at": scores.scored_at,
        "items": _rows(page),
    }


@recommendations_router.get("/overview", response_model=OverviewResponse)
def overview(
    top: int = Query(10, ge=1, le=100, description="Berapa PART teratas yang ikut dikirim"),
) -> dict:
    """Angka ringkas seluruh armada + daftar teratas, untuk halaman overview."""
    scores = serving_batch.score_active_parts()
    return {
        "summary": serving_batch.summary(scores.frame),
        "scored_at": scores.scored_at,
        "top_priority": _rows(scores.frame.head(top)),
    }


@recommendations_router.get("/filters", response_model=FiltersResponse)
def filters() -> dict:
    """Nilai filter yang benar-benar ada di data, untuk dropdown dashboard."""
    scores = serving_batch.score_active_parts()
    return serving_batch.facets(scores.frame)


@recommendations_router.get("/terminals", response_model=TerminalListResponse)
def terminals() -> dict:
    """Ringkasan risiko per Terminal, dari AGREGASI prediction PART yang
    sudah ada - bukan model baru khusus Terminal (docs/DECISIONS.md §14).

    Pakai `terminal_id` hasil di sini sebagai filter `/recommendations`
    untuk melihat seluruh PART dalam satu Terminal. `parts_without_terminal`
    dilaporkan APA ADANYA - PART yang relasi parent-Terminal-nya di database
    tidak bisa dipastikan (lihat `data_reader.get_terminal_context`) TIDAK
    dipaksakan masuk kelompok manapun.
    """
    scores = serving_batch.score_active_parts()
    overview_counts = serving_batch.terminal_overview(scores.frame)
    summary = serving_batch.terminal_summary(scores.frame)
    rows = summary.reset_index().to_dict(orient="records")
    return {
        "terminals": [
            {key: (None if pd.isna(value) else value) for key, value in row.items()}
            for row in rows
        ],
        "terminals_total": overview_counts["terminals"],
        "parts_with_terminal": overview_counts["parts_with_terminal"],
        "parts_without_terminal": overview_counts["parts_without_terminal"],
        "scored_at": scores.scored_at,
    }


locations_router = APIRouter(prefix="/api/v1", tags=["locations"])


@locations_router.get("/locations/map", response_model=LocationMapResponse)
def locations_map(
    resolve: bool = Query(
        True,
        description=(
            "Coba geocode lokasi yang belum ada di cache. Matikan untuk "
            "jawaban instan dari cache saja."
        ),
    ),
    budget_seconds: float = Query(
        GEOCODE_BUDGET_SECONDS_DEFAULT,
        ge=0,
        description="Anggaran waktu untuk geocoding lokasi baru pada panggilan ini.",
    ),
) -> dict:
    """Ringkasan risiko per lokasi, dipasangkan dengan koordinat kalau ada.

    Cache geocoding disk mengingat lokasi yang sudah pernah dicoba, jadi
    panggilan berikutnya untuk lokasi yang sama tidak memanggil jaringan lagi.
    Lokasi baru (belum pernah dicoba) di-geocode di sini, dibatasi
    `budget_seconds` supaya satu request tidak menggantung lama; sisanya baru
    diproses pada panggilan berikutnya.
    """
    scores = serving_batch.score_active_parts()
    summary = serving_batch.location_summary(scores.frame)
    locations = summary.index.tolist()

    if resolve and locations:
        capped = min(budget_seconds, GEOCODE_BUDGET_SECONDS_MAX)
        geocoding_service.resolve_missing(locations, budget_seconds=capped)

    coordinates = geocoding_service.known_coordinates(locations)

    resolved, unresolved = [], []
    for location, row in summary.iterrows():
        counts = row.to_dict()
        entry = coordinates.get(location)
        if entry and entry.get("resolved"):
            resolved.append({"location": location, "lat": entry["lat"], "lon": entry["lon"], **counts})
        else:
            checked = entry is not None and not entry.get("retry")
            unresolved.append({"location": location, "checked": checked, **counts})

    return {
        "resolved": resolved,
        "unresolved": unresolved,
        "scored_at": scores.scored_at,
    }


monitoring_router = APIRouter(prefix="/api/v1/monitoring", tags=["monitoring"])


@monitoring_router.get("/metrics")
def metrics() -> dict:
    """Snapshot metrik monitoring untuk kedua model."""
    return monitoring_service.summary()


@monitoring_router.get("/metrics/failure")
def failure_metrics() -> dict:
    return monitoring_service.failure_monitoring()


@monitoring_router.get("/metrics/scrap")
def scrap_metrics() -> dict:
    return monitoring_service.scrap_monitoring()


DESCRIPTION = """
API risiko kerusakan dan risiko scrap untuk PART.

**Yang perlu diketahui saat membaca angkanya**

- `failure_probability_Nd` adalah PELUANG PART rusak dalam N hari ke depan.
  Model tidak memperkirakan tanggal kerusakan.
- `scrap_probability` BERSYARAT: peluang PART tidak bisa diperbaiki JIKA
  rusak - bukan peluang PART ini rusak.
- Kelompok risiko (LOW/MEDIUM/HIGH) memakai ambang yang ditetapkan saat
  training dari kapasitas kerja tim, bukan angka bulat yang dikarang.
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    db_pool_install()

    try:
        serving.warmup()
        logger.info("Model dimuat: %s", serving.versions())
    except ModelUnavailable as error:
        logger.error("Model production belum tersedia: %s", error)

    if WARMUP_BATCH_ON_STARTUP:
        try:
            scores = serving_batch.score_active_parts()
            logger.info("Batch scoring awal selesai: %d PART aktif", len(scores.frame))
        except Exception as error:  # noqa: BLE001
            logger.error("Batch scoring awal gagal: %s", error)

    yield


app = FastAPI(
    title="Predictive Maintenance API",
    description=DESCRIPTION,
    version=API_VERSION,
    lifespan=lifespan,
)

if CORS_ALLOW_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ALLOW_ORIGINS,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

app.include_router(health_router)
app.include_router(model_info_router, dependencies=[Depends(require_api_key)])
app.include_router(prediction_router, dependencies=[Depends(require_api_key)])
app.include_router(recommendations_router, dependencies=[Depends(require_api_key)])
app.include_router(locations_router, dependencies=[Depends(require_api_key)])
app.include_router(monitoring_router, dependencies=[Depends(require_api_key)])


@app.exception_handler(PartNotFound)
async def handle_part_not_found(request: Request, error: PartNotFound) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={
            "status": "NOT_FOUND",
            "item_id": error.item_id,
            "message": error.message,
        },
    )


@app.exception_handler(AlertNotFound)
async def handle_alert_not_found(request: Request, error: AlertNotFound) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={
            "status": "NOT_FOUND",
            "item_id": error.item_id,
            "message": error.message,
        },
    )


@app.exception_handler(PartNotScorable)
async def handle_not_scorable(request: Request, error: PartNotScorable) -> JSONResponse:
    return JSONResponse(
        status_code=200,
        content={
            "status": "NOT_SCORABLE",
            "item_id": error.item_id,
            "reason": error.reason,
        },
    )


@app.exception_handler(ModelUnavailable)
async def handle_model_unavailable(request: Request, error: ModelUnavailable) -> JSONResponse:
    logger.error("Model tidak tersedia: %s", error)
    return JSONResponse(
        status_code=503,
        content={
            "status": "MODEL_UNAVAILABLE",
            "message": "Model production belum tersedia di server.",
        },
    )


@app.exception_handler(DataSourceUnavailable)
async def handle_data_source(request: Request, error: DataSourceUnavailable) -> JSONResponse:
    logger.exception("Database tidak bisa dibaca")
    return JSONResponse(
        status_code=503,
        content={
            "status": "DATA_SOURCE_UNAVAILABLE",
            "message": "Sumber data sedang tidak bisa dibaca. Coba lagi nanti.",
        },
    )


@app.exception_handler(Exception)
async def handle_unexpected(request: Request, error: Exception) -> JSONResponse:
    logger.exception("Kesalahan tak terduga pada %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "status": "INTERNAL_ERROR",
            "message": "Terjadi kesalahan di server.",
        },
    )
