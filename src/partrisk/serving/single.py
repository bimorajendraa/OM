from __future__ import annotations

from partrisk.core import config
from partrisk.engines import predict


class PartNotFound(LookupError):
    def __init__(self, item_id: str, message: str | None = None) -> None:
        self.item_id = item_id
        self.message = message or f"PART '{item_id}' tidak ditemukan di database."
        super().__init__(self.message)


class ModelUnavailable(RuntimeError):
    pass


class DataSourceUnavailable(RuntimeError):
    pass


BATCH_CACHE_TTL_SECONDS = config._int("BATCH_CACHE_TTL_SECONDS", 3600)

DATA_FRESHNESS_TTL_SECONDS = config._int("DATA_FRESHNESS_TTL_SECONDS", 60)


RISK_LEVELS = ("LOW", "MEDIUM", "HIGH")

_RECOMMENDATION_TABLE: dict[str, tuple[str, str, str]] = {
    "HIGH": (
        "HIGH",
        "PRIORITIZE_INSPECTION",
        "Risiko kerusakan tinggi. Dahulukan pemeriksaan.",
    ),
    "MEDIUM": (
        "MEDIUM",
        "SCHEDULE_INSPECTION",
        "Risiko kerusakan sedang. Jadwalkan pemeriksaan pada siklus terdekat.",
    ),
    "LOW": (
        "LOW",
        "MONITOR",
        "Risiko kerusakan rendah. Cukup dipantau.",
    ),
}


def recommend(failure_risk_level: str) -> dict:
    if failure_risk_level not in RISK_LEVELS:
        raise ValueError(f"Kelompok risiko kerusakan tidak dikenal: {failure_risk_level!r}")

    priority, action, message = _RECOMMENDATION_TABLE[failure_risk_level]

    return {
        "priority": priority,
        "action": action,
        "message": message,
        "based_on": {
            "failure_risk_level": failure_risk_level,
        },
    }


def failure_metadata() -> dict:
    try:
        return predict.load_failure_model()[2]
    except FileNotFoundError as error:
        raise ModelUnavailable(str(error)) from error


def versions() -> dict[str, str]:
    return {
        "failure": failure_metadata()["model_version"],
    }


def warmup() -> None:
    failure_metadata()
