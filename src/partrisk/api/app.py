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
from partrisk.api.schemas import (
    AssessmentResponse,
    FailureResponse,
    FiltersResponse,
    HealthResponse,
    HistoryResponse,
    OverviewResponse,
    RecommendationListResponse,
    ResolveAlertResponse,
    TerminalListResponse,
    TerminalPartListResponse,
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
    try:
        versions: dict[str, str | None] = dict(serving.versions())
        model_ok = True
    except ModelUnavailable:
        versions = {"failure": None}
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


@prediction_router.get("/{item_id}/history", response_model=HistoryResponse)
def history(item_id: str = _ITEM_ID) -> dict:
    return serving.item_history(item_id)


@prediction_router.post("/{item_id}/resolve-alert", response_model=ResolveAlertResponse)
def resolve_alert(item_id: str = _ITEM_ID) -> dict:
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
    """Risiko kerusakan + rekomendasi tindakan."""
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
    priority: str | None = Query(None, description="Saring prioritas: LOW/MEDIUM/HIGH"),
    item_type: str | None = Query(None, description="Saring jenis PART, mis. MOTOR"),
    client: str | None = Query(None, description="Saring client"),
    location: str | None = Query(None, description="Saring lokasi terakhir tercatat"),
    terminal_id: str | None = Query(
        None, description="Saring PART milik satu Terminal (terminal_id dari /api/v1/terminals)"
    ),
    search: str | None = Query(
        None, description="Cari sebagian ID PART, mis. 0112011 (tidak harus lengkap)"
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


@recommendations_router.get(
    "/terminals/{terminal_id}/parts", response_model=TerminalPartListResponse
)
def terminal_parts(terminal_id: str) -> dict:
    scores = serving_batch.score_active_parts()
    summary = serving_batch.terminal_part_summary(scores.frame, terminal_id)
    rows = summary.reset_index().to_dict(orient="records")
    return {
        "terminal_id": terminal_id,
        "parts": [
            {key: (None if pd.isna(value) else value) for key, value in row.items()}
            for row in rows
        ],
        "scored_at": scores.scored_at,
    }


@recommendations_router.get(
    "/terminals/{terminal_id}/parts/{part_type}", response_model=RecommendationListResponse
)
def terminal_part_items(
    terminal_id: str,
    part_type: str,
    limit: int = Query(DEFAULT_RECOMMENDATION_LIMIT, ge=1),
    offset: int = Query(0, ge=0),
) -> dict:
    limit = min(limit, MAX_RECOMMENDATION_LIMIT)
    scores = serving_batch.score_active_parts()
    selected = serving_batch.filter_scores(
        scores.frame,
        terminal_id=terminal_id,
        part_type=part_type,
        official_queue_only=False,
    )
    page = selected.iloc[offset : offset + limit]
    return {
        "total": int(len(selected)),
        "returned": int(len(page)),
        "offset": offset,
        "scored_at": scores.scored_at,
        "items": _rows(page),
    }


DESCRIPTION = """
API risiko kerusakan untuk PART.

**Yang perlu diketahui saat membaca angkanya**

- `failure_probability_Nd` adalah PELUANG PART rusak dalam N hari ke depan.
  Model tidak memperkirakan tanggal kerusakan.
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
