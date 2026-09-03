from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


PACKAGE_DIR = Path(os.environ.get("PARTRISK_HOME", str(Path(__file__).resolve().parent.parent.parent.parent)))
MODEL_DIR = Path(os.environ.get("PARTRISK_MODEL_DIR", str(PACKAGE_DIR / "models")))
ENV_FILE = Path(os.environ.get("PARTRISK_ENV_FILE", str(PACKAGE_DIR / ".env")))
FAILURE_MODEL_DIR = MODEL_DIR / "failure"


def db_settings() -> dict[str, str]:
    load_dotenv(ENV_FILE)
    required = ["DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD"]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError(
            "Konfigurasi database belum lengkap "
            + ", ".join(missing)
        )
    return {
        "host": os.environ["DB_HOST"],
        "port": os.environ["DB_PORT"],
        "dbname": os.environ["DB_NAME"],
        "user": os.environ["DB_USER"],
        "password": os.environ["DB_PASSWORD"],
        "sslmode": os.getenv("DB_SSLMODE", "prefer"),
    }


CATEGORICAL_FEATURES = [
    "part_model_category",
    "client_category",
    "installation_age_band",
]
NUMERIC_FEATURES = [
    "log_days_since_installation",
    "log_total_prior_events",
    "log_prior_failure_count",
    "has_prior_failure",
    "log_prior_corrective_count",
    "has_prior_corrective",
    "log_days_since_last_corrective",
    "log_prior_distinct_places",
    "log_prior_corrective_30d",
    "log_prior_failure_365d",
    "log_prior_events_180d",
    "log_previous_cycle_lifetime_mean",
    "has_previous_cycle",
    "month_sin",
    "month_cos",
]
FLEET_FEATURES = [
    "log_model_failures_90d",
    "model_failure_rate_90d",
    "log_model_fleet_size",
]
FLEET_WINDOW_DAYS = 90

DEGRADATION_FEATURES = [
    "log_cumulative_prior_cycle_days",
    "log_previous_cycle_count",
    "has_failure_interval_trend",
    "log_failure_interval_mean_days",
    "failure_interval_trend_ratio",
    "log_prior_corrective_60d",
    "log_prior_corrective_90d",
]

LOCAL_DENSITY_FEATURES = [
    "log_item_type_failures_90d",
    "item_type_failure_rate_90d",
    "log_item_type_failures_180d",
    "item_type_failure_rate_180d",
]

FEATURE_COLUMNS = (
    CATEGORICAL_FEATURES + NUMERIC_FEATURES + FLEET_FEATURES
    + DEGRADATION_FEATURES + LOCAL_DENSITY_FEATURES
)

TARGET_HORIZON_DAYS = 30
OBSERVATION_STEP_DAYS = 30

MIN_OBSERVATION_DATE = "2014-01-01"

CATBOOST_PARAMS = {
    "iterations": 200,
    "depth": 4,
    "learning_rate": 0.03,
    "l2_leaf_reg": 10,
    "loss_function": "Logloss",
    "eval_metric": "AUC",
    "auto_class_weights": "Balanced",
    "use_best_model": False,
    "verbose": False,
    "thread_count": 1,
}
RANDOM_STATE = 42

MIN_PART_MODEL_SUPPORT = 300
LOW_SUPPORT_LABEL = "LOW_HISTORICAL_SUPPORT"
UNKNOWN_LABEL = "UNKNOWN"

AGE_BAND_THRESHOLDS = [91, 181, 366, 731, 1461]
AGE_BAND_LABELS = [
    "000_090_DAYS",
    "091_180_DAYS",
    "181_365_DAYS",
    "366_730_DAYS",
    "731_1460_DAYS",
    "1461_PLUS_DAYS",
]

PREDICTION_HORIZON_DAYS = [30, 60, 90, 120]

FAILURE_HIGH_PROBABILITY_THRESHOLD = 0.25
FAILURE_MEDIUM_PROBABILITY_THRESHOLD = 0.15

FAILURE_CAPACITY_PER_MONTH = 200


FAILURE_GATE_TARGET_PRECISION = 0.40


APPROVED_LOCATION_ALIAS = {"GUDANG NUTECH": "GUDANG NI"}
APPROVED_CLIENT_ALIAS: dict[str, str] = {}
TEXT_ABBREVIATION_MAPPING = {"JKT": "JAKARTA"}

FUZZY_MIN_SCORE = 0.90
FUZZY_MIN_MARGIN = 0.08
