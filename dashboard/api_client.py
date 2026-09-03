from __future__ import annotations

import os

import pandas as pd
import requests
import streamlit as st
from dotenv import load_dotenv


load_dotenv()

API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")


API_KEY = os.getenv("API_KEY", "").strip() or None

REQUEST_TIMEOUT = (5, 180)
CACHE_TTL_SECONDS = 300


class ApiError(RuntimeError):
    pass


def _get(path: str, params: dict | None = None) -> dict:
    headers = {"X-API-Key": API_KEY} if API_KEY else None
    try:
        response = requests.get(
            f"{API_BASE_URL}{path}", params=params, headers=headers, timeout=REQUEST_TIMEOUT
        )
    except requests.RequestException as error:
        raise ApiError(
            f"Tidak bisa menghubungi API di {API_BASE_URL}. "
            f"Pastikan `uvicorn partrisk.api.app:app` sedang jalan. ({type(error).__name__})"
        ) from error

    is_json = response.headers.get("content-type", "").startswith("application/json")
    try:
        body = response.json() if is_json else None
    except ValueError:
        body = None

    if response.status_code == 404 and isinstance(body, dict) and body.get("status") == "NOT_FOUND":
        return body
    if response.status_code >= 400:
        message = (body.get("message") or body.get("detail")) if isinstance(body, dict) else None
        raise ApiError(message or f"API menjawab {response.status_code}.")
    if body is None:
        raise ApiError("API mengembalikan respons yang tidak bisa dibaca (bukan JSON).")
    return body


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def health() -> dict:
    return _get("/health")


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def model_info() -> dict:
    return _get("/api/v1/model")


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner="Menghitung risiko seluruh PART aktif...")
def overview(top: int = 10) -> dict:
    return _get("/api/v1/overview", {"top": top})


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner="Menghitung risiko seluruh PART aktif...")
def filters() -> dict:
    return _get("/api/v1/filters")


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner="Mengambil daftar prioritas...")
def recommendations(**params) -> dict:
    return _get("/api/v1/recommendations", {k: v for k, v in params.items() if v not in (None, "", "Semua")})


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner="Menilai PART...")
def assessment(item_id: str, explain: bool = True) -> dict:
    return _get(f"/api/v1/parts/{item_id}/assessment", {"explain": explain})


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def history(item_id: str) -> dict:
    return _get(f"/api/v1/parts/{item_id}/history")


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner="Mengelompokkan PART per Terminal...")
def terminals() -> dict:
    return _get("/api/v1/terminals")


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner="Mengelompokkan PART per jenis...")
def terminal_parts(terminal_id: str) -> dict:
    return _get(f"/api/v1/terminals/{terminal_id}/parts")


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner="Mengambil daftar PART...")
def terminal_part_items(terminal_id: str, part_type: str) -> dict:
    return _get(f"/api/v1/terminals/{terminal_id}/parts/{part_type}", {"limit": 500})


def percent(value: float | None) -> str:
    return "-" if value is None or pd.isna(value) else f"{value:.1%}"
