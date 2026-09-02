from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from partrisk.core import config
from partrisk.engines import predict as failure_model
from partrisk.engines import predict as scrap_model
from partrisk.serving import alerts as alert_store
from partrisk.serving import batch as serving_batch

logger = logging.getLogger(__name__)


CACHE_PATH = config.PACKAGE_DIR / ".cache" / "geocode.json"


INDONESIA_BBOX = {"south": -11.05, "north": 6.05, "west": 94.75, "east": 141.05}

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "production-ml-predictive-maintenance/1.0 (internal tool)"
MIN_SECONDS_BETWEEN_REQUESTS = 1.1

_lock = threading.Lock()
_last_request_at = 0.0


def _load_cache() -> dict:
    if not CACHE_PATH.exists():
        return {}
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("Cache geocoding rusak, mulai dari kosong: %s", CACHE_PATH)
        return {}


def _save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = CACHE_PATH.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp_path.replace(CACHE_PATH)


def _within_service_area(lat: float, lon: float) -> bool:
    box = INDONESIA_BBOX
    return box["south"] <= lat <= box["north"] and box["west"] <= lon <= box["east"]


def _throttle() -> None:
    global _last_request_at
    elapsed = time.time() - _last_request_at
    wait = MIN_SECONDS_BETWEEN_REQUESTS - elapsed
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.time()


def _query_nominatim(name: str) -> list[dict]:
    _throttle()
    response = requests.get(
        NOMINATIM_URL,
        params={
            "q": f"{_search_query(name)}, Indonesia",
            "format": "json",
            "limit": 3,
            "countrycodes": "id",
            "viewbox": (
                f"{INDONESIA_BBOX['west']},{INDONESIA_BBOX['north']},"
                f"{INDONESIA_BBOX['east']},{INDONESIA_BBOX['south']}"
            ),
        },
        headers={"User-Agent": USER_AGENT},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def _looks_like_public_station(name: str) -> bool:
    upper = name.strip().upper()
    return upper.startswith("STASIUN ") or upper.endswith("(KA BANDARA)")


def _search_query(name: str) -> str:
    upper = name.strip().upper()
    if upper.endswith("(KA BANDARA)"):
        base = name[: -len("(KA BANDARA)")].strip()
        return f"Stasiun {base}"
    return name


def _resolve_one(name: str) -> dict:
    if not _looks_like_public_station(name):
        return {
            "resolved": False,
            "retry": False,
            "reason": "bukan nama stasiun publik (fasilitas internal atau typo)",
            "checked_at": time.time(),
        }

    try:
        results = _query_nominatim(name)
    except requests.RequestException as error:
        logger.warning("Geocoding gagal untuk %r: %s", name, error)
        return {"resolved": False, "retry": True}

    for candidate in results:
        try:
            lat, lon = float(candidate["lat"]), float(candidate["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        if _within_service_area(lat, lon):
            return {
                "resolved": True,
                "lat": lat,
                "lon": lon,
                "matched_name": candidate.get("display_name"),
                "checked_at": time.time(),
            }

    return {"resolved": False, "retry": False, "checked_at": time.time()}


def known_coordinates(locations: list[str]) -> dict[str, dict | None]:
    cache = _load_cache()
    return {name: cache.get(name) for name in locations}


def resolve_missing(locations: list[str], budget_seconds: float) -> int:
    with _lock:
        cache = _load_cache()
        pending = [
            name for name in locations
            if name not in cache
            or not isinstance(cache[name], dict)
            or cache[name].get("retry")
        ]
        if not pending:
            return 0

        started = time.time()
        processed = 0
        for name in pending:
            if time.time() - started >= budget_seconds:
                break
            cache[name] = _resolve_one(name)
            processed += 1
            _save_cache(cache)
        return processed


_DRIFT_COLUMNS = [
    "days_since_installation",
    "total_prior_events",
    "prior_failure_count",
    "prior_failure_365d",
    "prior_corrective_count",
    "model_failure_rate_90d",
]


def _score_distribution(scores: np.ndarray) -> dict:
    if len(scores) == 0:
        return {}
    percentiles = np.percentile(scores, [5, 25, 50, 75, 95])
    return {
        "min": round(float(scores.min()), 6),
        "p05": round(float(percentiles[0]), 6),
        "p25": round(float(percentiles[1]), 6),
        "median": round(float(percentiles[2]), 6),
        "p75": round(float(percentiles[3]), 6),
        "p95": round(float(percentiles[4]), 6),
        "max": round(float(scores.max()), 6),
        "mean": round(float(scores.mean()), 6),
    }


def _unknown_category_share(snapshot: pd.DataFrame, support_by_model: dict[str, int]) -> dict:
    codes = snapshot["item_model_code_clean"]
    support = codes.map(support_by_model).fillna(0)
    unknown = codes.isna() | (support < config.MIN_PART_MODEL_SUPPORT)
    return {
        "unknown_or_low_support_parts": int(unknown.sum()),
        "unknown_or_low_support_share": (
            round(float(unknown.mean()), 4) if len(codes) else 0.0
        ),
        "distinct_model_codes_active": int(codes.dropna().nunique()),
        "distinct_model_codes_in_training": len(support_by_model),
    }


def _feature_summary(snapshot: pd.DataFrame) -> dict:
    summary = {}
    for column in _DRIFT_COLUMNS:
        if column not in snapshot.columns:
            continue
        values = pd.to_numeric(snapshot[column], errors="coerce").dropna()
        if values.empty:
            continue
        summary[column] = {
            "mean": round(float(values.mean()), 4),
            "median": round(float(values.median()), 4),
            "missing_share": round(float(snapshot[column].isna().mean()), 4),
        }
    return summary


def failure_monitoring() -> dict:
    metadata = failure_model.load_failure_model()[2]
    scores = serving_batch.score_active_parts()
    frame = scores.frame

    offline = {
        "model_version": metadata["model_version"],
        "training_date": metadata["training_date"],
        "test_metrics": metadata["evaluation_metrics"]["test"],
        "validation_base_rate": metadata["validation_base_rate"],
        "last_promotion_comparison": metadata.get("promotion_comparison"),

        "gate": metadata.get("gate"),
    }

    tier_score = frame["tier_score"].to_numpy(dtype=float)
    level_counts = frame["failure_risk_level"].value_counts().to_dict()
    expected_high = metadata["cutoff_basis"]["flagged_high"]
    actual_high = int(level_counts.get("HIGH", 0))

    live = {
        "active_parts": int(len(frame)),
        "score_distribution": _score_distribution(tier_score),
        "risk_level_counts": {
            "HIGH": actual_high,
            "MEDIUM": int(level_counts.get("MEDIUM", 0)),
            "LOW": int(level_counts.get("LOW", 0)),
        },
        "expected_high_from_training": expected_high,
        "high_count_ratio_vs_training": (
            round(actual_high / expected_high, 3) if expected_high else None
        ),
        "category_coverage": _unknown_category_share(
            scores.snapshot, metadata["part_model_support"]
        ),
        "feature_summary": _feature_summary(scores.snapshot),
        "data_through": str(scores.data_end),

        "official_queue_size": int(frame["in_official_queue"].sum()),
        "open_alerts": alert_store.open_count(),
        "open_alert_age_days": _score_distribution(
            np.asarray(alert_store.open_lead_times_days())
        ),
    }

    return {"offline": offline, "live": live}


def scrap_monitoring() -> dict:
    metadata = scrap_model.load_scrap_model()[2]
    scores = serving_batch.score_active_parts()
    frame = scores.frame

    offline = {
        "model_version": metadata["model_version"],
        "training_date": metadata["training_date"],
        "evaluation_metrics": metadata["evaluation_metrics"],
        "cutoff_basis": metadata["cutoff_basis"],
        "last_promotion_comparison": metadata.get("promotion_comparison"),
    }

    scrap_probability = frame["scrap_probability"].dropna().to_numpy(dtype=float)
    known_types = set(metadata["known_item_types"])
    item_types = frame["item_type"].dropna()
    unknown_types = ~item_types.isin(known_types)

    live = {
        "parts_with_scrap_score": int(len(scrap_probability)),
        "predicted_scrap_probability_distribution": _score_distribution(scrap_probability),
        "predicted_scrap_probability_mean": (
            round(float(scrap_probability.mean()), 4) if len(scrap_probability) else None
        ),
        "risk_level_counts": {
            str(name): int(count)
            for name, count in frame["scrap_risk_level"].value_counts().items()
        },
        "unknown_item_type_share": (
            round(float(unknown_types.mean()), 4) if len(item_types) else 0.0
        ),
        "data_through": str(scores.data_end),
    }

    return {"offline": offline, "live": live}


def summary() -> dict:
    return {
        "failure": failure_monitoring(),
        "scrap": scrap_monitoring(),
    }
