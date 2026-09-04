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
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from partrisk.core import config
from partrisk.core import data_reader
from partrisk.predictive import alerts as alert_engine
from partrisk.predictive.cycles import ItemNotInstalled
from partrisk.serving import single as serving
from partrisk.serving import batch as serving_batch
from partrisk.serving.single import ModelUnavailable, PartNotFound
from partrisk.api.schemas import (
    HealthResponse,
    InspectionRequest,
    InspectionResponse,
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

CORS_ALLOW_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ALLOW_ORIGINS", "").split(",")
    if origin.strip()
]


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


inspections_router = APIRouter(prefix="/api/v1", tags=["inspections"])


def _stringify_datetimes(row: dict) -> dict:
    return {
        key: (value.isoformat() if hasattr(value, "isoformat") else value)
        for key, value in row.items()
    }


@inspections_router.post("/inspections", response_model=InspectionResponse)
def record_inspection(payload: InspectionRequest) -> dict:
    """Catat satu perbaikan terhadap satu PART, diidentifikasi lewat
    `host_serial_code`. Kalau PART ini sedang punya alert OPEN, alert itu
    ikut di-RESOLVE; kalau tidak, inspection tetap dicatat tanpa alert."""
    item_id = data_reader.resolve_item_by_host_serial_code(payload.host_serial_code)
    if item_id is None:
        raise PartNotFound(
            payload.host_serial_code,
            message=f"PART dengan serial code '{payload.host_serial_code}' tidak ditemukan.",
        )
    result = alert_engine.resolve_by_item(item_id, pd.Timestamp.now(tz="UTC"))
    return {
        "inspection": _stringify_datetimes(result["inspection"]),
        "alert": _stringify_datetimes(result["alert"]) if result["alert"] else None,
    }


DESCRIPTION = """
API predictive maintenance - satu-satunya endpoint publik adalah
`POST /api/v1/inspections`. Tidak ada endpoint GET - aplikasi eksternal
baca schema `predictive` langsung dari database.
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
app.include_router(inspections_router, dependencies=[Depends(require_api_key)])


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


@app.exception_handler(alert_engine.AlertNotFound)
async def handle_alert_not_found(request: Request, error: alert_engine.AlertNotFound) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"status": "NOT_FOUND", "message": str(error)},
    )


@app.exception_handler(alert_engine.AlertNotOpen)
async def handle_alert_not_open(request: Request, error: alert_engine.AlertNotOpen) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={"status": "ALERT_NOT_OPEN", "message": str(error)},
    )


@app.exception_handler(alert_engine.AlertCycleMismatch)
async def handle_alert_cycle_mismatch(
    request: Request, error: alert_engine.AlertCycleMismatch
) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={"status": "ALERT_CYCLE_MISMATCH", "message": str(error)},
    )


@app.exception_handler(ItemNotInstalled)
async def handle_item_not_installed(request: Request, error: ItemNotInstalled) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"status": "NOT_FOUND", "message": str(error)},
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
