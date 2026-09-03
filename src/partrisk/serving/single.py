from __future__ import annotations

import numpy as np
import pandas as pd
import psycopg

from partrisk.core import config
from partrisk.core import data_reader
from partrisk.core import features as feature_builder
from partrisk.engines import predict
from partrisk.serving import alerts as alert_store
from partrisk.serving import batch as serving_batch


class PartNotFound(LookupError):
    def __init__(self, item_id: str, message: str | None = None) -> None:
        self.item_id = item_id
        self.message = message or f"PART '{item_id}' tidak ditemukan di database."
        super().__init__(self.message)


class PartNotScorable(RuntimeError):
    def __init__(self, item_id: str, reason: str) -> None:
        self.item_id = item_id
        self.reason = reason
        super().__init__(reason)


class ModelUnavailable(RuntimeError):
    pass


class DataSourceUnavailable(RuntimeError):
    pass


class AlertNotFound(LookupError):
    def __init__(self, item_id: str) -> None:
        self.item_id = item_id
        self.message = f"Tidak ada alert terbuka untuk PART '{item_id}'."
        super().__init__(self.message)


BATCH_CACHE_TTL_SECONDS = config._int("BATCH_CACHE_TTL_SECONDS", 3600)

DATA_FRESHNESS_TTL_SECONDS = config._int("DATA_FRESHNESS_TTL_SECONDS", 60)


def failure_history(events: pd.DataFrame) -> list[dict]:
    failures = events.loc[events["is_failure_onset"].fillna(False).astype(bool)].copy()
    if failures.empty:
        return []
    failures = failures.sort_values("created_on", ascending=False)
    return [
        {
            "date": str(pd.Timestamp(row["created_on"])),
            "location": (
                row["place_canonical_clean"]
                if pd.notna(row["place_canonical_clean"])
                else None
            ),
            "status": row["status_clean"],
            "wo_type": row["wo_type_clean"] if pd.notna(row["wo_type_clean"]) else None,
        }
        for _, row in failures.iterrows()
    ]


def location_history(events: pd.DataFrame) -> list[dict]:
    known = events.loc[events["place_canonical_clean"].notna()].copy()
    if known.empty:
        return []
    known["created_on"] = pd.to_datetime(known["created_on"])
    grouped = known.groupby("place_canonical_clean")["created_on"].agg(
        first_seen="min", last_seen="max", events="count"
    )
    grouped = grouped.sort_values("last_seen", ascending=False)
    return [
        {
            "location": location,
            "first_seen": str(row["first_seen"]),
            "last_seen": str(row["last_seen"]),
            "events": int(row["events"]),
        }
        for location, row in grouped.iterrows()
    ]


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

PRIORITY_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


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


DISCLAIMER = (
    "Faktor di bawah adalah kondisi PART yang menjadi masukan model, bukan "
    "kontribusi terukur terhadap skor. Analisis kontribusi per-fitur (SHAP) "
    "belum tersedia."
)

CORRECTIVE_NOTE = (
    "Aktivitas korektif dihitung per CATATAN kejadian, bukan per pekerjaan: "
    "satu work order umumnya menghasilkan beberapa catatan (permintaan, "
    "pengeluaran, pengiriman, pemasangan)."
)

FAILURE_HISTORY_NOTE = (
    "Kerusakan yang tercatat bukan berarti PART berhenti dipakai: PART yang "
    "rusak masuk bengkel dan dipasang kembali kalau bisa diperbaiki. PART ini "
    "sedang terpasang."
)

RISK, MITIGATING, CONTEXT = "RISK_FACTOR", "MITIGATING", "CONTEXT"

SOURCE_COLUMNS = [
    "item_model_code_clean",
    "days_since_installation",
    "total_prior_events",
    "prior_failure_count",
    "prior_failure_365d",
    "prior_corrective_count",
    "prior_corrective_30d",
    "days_since_last_corrective",
    "prior_distinct_places",
    "previous_cycle_lifetime_mean",
    "has_previous_cycle",
    "log_model_failures_90d",
    "model_failure_rate_90d",
    "log_model_fleet_size",
]


def _number(row: pd.Series, column: str, default: float = 0.0) -> float:
    value = pd.to_numeric(row.get(column), errors="coerce")
    return default if pd.isna(value) else float(value)


def _factor(code: str, direction: str, label: str, value) -> dict:
    return {"code": code, "direction": direction, "label": label, "value": value}


def risk_factors(row: pd.Series) -> list[dict]:
    factors: list[dict] = []

    failures_365 = _number(row, "prior_failure_365d")
    failures_total = _number(row, "prior_failure_count")
    if failures_365 > 0:
        factors.append(_factor(
            "RECENT_FAILURE_HISTORY", RISK,
            f"{int(failures_365)} kerusakan tercatat dalam 365 hari terakhir",
            int(failures_365),
        ))
    elif failures_total > 0:
        factors.append(_factor(
            "OLDER_FAILURE_HISTORY", CONTEXT,
            f"{int(failures_total)} kerusakan tercatat sepanjang riwayat PART, "
            "tidak ada dalam 365 hari terakhir",
            int(failures_total),
        ))
    else:
        factors.append(_factor(
            "NO_FAILURE_HISTORY", MITIGATING,
            "Belum pernah tercatat rusak sama sekali", 0,
        ))

    days_since_corrective = pd.to_numeric(
        row.get("days_since_last_corrective"), errors="coerce"
    )
    corrective_total = _number(row, "prior_corrective_count")
    if corrective_total > 0 and not pd.isna(days_since_corrective):
        factors.append(_factor(
            "CORRECTIVE_HISTORY", CONTEXT,
            f"{int(corrective_total)} catatan aktivitas korektif sepanjang "
            f"riwayat, terakhir {int(days_since_corrective)} hari lalu",
            int(days_since_corrective),
        ))

    age = _number(row, "days_since_installation")
    oldest_band = config.AGE_BAND_THRESHOLDS[-1]
    factors.append(_factor(
        "INSTALLATION_AGE", RISK if age >= oldest_band else CONTEXT,
        f"Terpasang {int(age)} hari",
        int(age),
    ))

    fleet_failures = int(round(np.expm1(_number(row, "log_model_failures_90d"))))
    fleet_size = int(round(np.expm1(_number(row, "log_model_fleet_size"))))
    if fleet_failures > 0:
        rate = _number(row, "model_failure_rate_90d")
        factors.append(_factor(
            "FLEET_CONDITION", RISK,
            f"Model PART ini mengalami {fleet_failures} kerusakan dalam "
            f"{config.FLEET_WINDOW_DAYS} hari terakhir dari {fleet_size} unit "
            f"terpasang ({rate:.1%} per unit)",
            round(rate, 4),
        ))

    if bool(row.get("has_previous_cycle", False)):
        lifetime = _number(row, "previous_cycle_lifetime_mean")
        factors.append(_factor(
            "PREVIOUS_CYCLE_LIFETIME", CONTEXT,
            f"Rata-rata umur pemasangan pada siklus sebelumnya {int(lifetime)} hari, "
            f"dibandingkan {int(age)} hari pada siklus pemasangan saat ini",
            int(lifetime),
        ))

    places = _number(row, "prior_distinct_places")
    if places > 1:
        factors.append(_factor(
            "LOCATION_CHANGES", CONTEXT,
            f"Pernah tercatat di {int(places)} lokasi berbeda", int(places),
        ))

    return factors


def caveats(row: pd.Series, support_by_model: dict[str, int]) -> list[str]:
    notes: list[str] = []
    model_code = row.get("item_model_code_clean")
    support = support_by_model.get(str(model_code), 0) if model_code else 0
    if not model_code:
        notes.append(
            "Model PART tidak diketahui, sehingga fitur identitas PART masuk "
            "kategori UNKNOWN."
        )
    elif support < config.MIN_PART_MODEL_SUPPORT:
        notes.append(
            f"Riwayat model PART '{model_code}' masih sedikit ({support} observasi "
            f"saat training, ambang {config.MIN_PART_MODEL_SUPPORT}), jadi model "
            "menilainya bersama kelompok berdukungan rendah."
        )
    return notes


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


def describe() -> dict:
    failure = failure_metadata()
    return {
        "failure": {
            "model_version": failure["model_version"],
            "training_date": failure["training_date"],
            "target": failure["target"],
            "horizons_days": config.PREDICTION_HORIZON_DAYS,
            "features": failure["features"],
            "risk_cutoffs": failure["risk_cutoffs"],
            "cutoff_basis": failure["cutoff_basis"],
            "test_metrics": failure["evaluation_metrics"]["test"],
            "data_through": failure["training_period"]["dataset_max_event_on"],
            "gate": failure.get("gate"),
        },
        "notes": {
            "failure_probability": (
                "Peluang PART mengalami kerusakan dalam N hari ke depan. "
                "Model tidak memperkirakan tanggal kerusakan."
            ),
        },
    }


def _guard(call, *args, **kwargs):
    try:
        return call(*args, **kwargs)
    except psycopg.Error as error:
        raise DataSourceUnavailable(
            f"Database tidak bisa dibaca ({type(error).__name__})."
        ) from error


def _exists(item_id: str) -> bool:
    return not _guard(data_reader.get_events, item_id).empty


def _translate(item_id: str, error: Exception) -> Exception:
    if not _exists(item_id):
        return PartNotFound(item_id)

    reason = str(error)
    if "tidak ditemukan" in reason:
        reason = (
            f"PART '{item_id}' ada di catatan, tetapi tidak punya siklus "
            "pemasangan sebagai PART yang bisa dinilai model."
        )
    return PartNotScorable(item_id, reason)


def predict_failure(item_id: str) -> dict:
    with serving_batch.request_scope():
        serving_batch.current_data_end()
        try:
            return _guard(predict.predict, item_id)
        except predict.FailureNotScorable as error:
            raise _translate(item_id, error) from error


def resolve_alert(item_id: str) -> dict:
    """Tandai alert PART ini selesai diinspeksi/dimaintenance.

    PART bisa dinilai ulang pada siklus batch berikutnya dan hanya
    dipromosikan lagi (alert baru) kalau kondisi terkini masih memenuhi
    aturan risiko - bukan otomatis dihapus dari antrian selamanya.
    """
    normalized = data_reader.normalize(item_id)
    if not alert_store.resolve(normalized):
        raise AlertNotFound(item_id)
    return {"item_id": item_id, "status": "RESOLVED"}


def get_part_assessment(item_id: str, include_explanation: bool = True) -> dict:
    with serving_batch.request_scope():
        serving_batch.current_data_end()
        failure = predict_failure(item_id)

        assessment = {
            "item_id": failure["item_id"],
            "status": "SCORED",
            "as_of": failure["as_of"],
            "failure": failure,
            "recommendation": recommend(failure["risk_level"]),
            "model_version": {
                "failure": failure["model_version"],
            },
        }

        if include_explanation:
            assessment["explanation"] = explain(item_id)
        return assessment


def explain(item_id: str) -> dict:
    metadata = failure_metadata()
    row = _feature_row(item_id)
    factors = risk_factors(row)
    notes = [FAILURE_HISTORY_NOTE]
    if any(factor["code"].endswith("CORRECTIVE_MAINTENANCE") or
           factor["code"] == "CORRECTIVE_HISTORY" for factor in factors):
        notes.append(CORRECTIVE_NOTE)
    return {
        "disclaimer": DISCLAIMER,
        "factors": factors,
        "notes": notes,
        "caveats": caveats(row, metadata["part_model_support"]),
    }


def _feature_row(item_id: str) -> pd.Series:
    cached = serving_batch.cached_scores()
    if cached is not None and not cached.is_stale(serving_batch.generation()):
        for key in (item_id, data_reader.normalize(item_id)):
            if key in cached.snapshot.index:
                return _single_row(cached.snapshot.loc[key])

    return _active_snapshot(item_id).iloc[0]


def _single_row(selection: pd.Series | pd.DataFrame) -> pd.Series:
    return selection.iloc[0] if isinstance(selection, pd.DataFrame) else selection


def item_history(item_id: str) -> dict:
    with serving_batch.request_scope():
        serving_batch.current_data_end()
        events = _guard(data_reader.get_events, item_id)
        if events.empty:
            raise PartNotFound(item_id)
        return {
            "item_id": item_id,
            "failures": failure_history(events),
            "locations": location_history(events),
        }


def _active_snapshot(item_id: str) -> pd.DataFrame:
    with serving_batch.request_scope():
        data_end = _guard(data_reader.get_dataset_max_event_on)
        cycles = _guard(data_reader.get_cycles, item_id, data_end)
        if cycles.empty:
            raise _translate(item_id, LookupError(f"PART '{item_id}' tidak ditemukan."))

        events = _guard(data_reader.get_events, item_id)
        snapshot = feature_builder.current_observations(cycles, events)
        if snapshot.empty:
            raise PartNotScorable(
                item_id,
                f"PART '{item_id}' sedang tidak terpasang, jadi tidak ada fitur "
                "kondisi terkini yang bisa dijelaskan.",
            )
        snapshot = feature_builder.attach_history(snapshot, events)
        return feature_builder.attach_fleet_snapshot(
            snapshot, predict.fleet_snapshot(data_end)
        )
