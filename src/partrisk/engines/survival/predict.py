from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from partrisk.core import config
from partrisk.core import data_reader
from partrisk.core import features as feature_builder
from partrisk.core import features_survival
from partrisk.engines import predict as predict_failure
from partrisk.engines.survival import curve as curves
from partrisk.engines.survival import train as landmark_eval

def _current_artifacts_dir() -> Path:

    pointer = config.SURVIVAL_MODEL_DIR / "CURRENT"
    if not pointer.exists():
        return config.SURVIVAL_MODEL_DIR / "CURRENT_BELUM_ADA"
    return config.SURVIVAL_MODEL_DIR / pointer.read_text(encoding="utf-8").strip()


ARTIFACTS_DIR = _current_artifacts_dir()
HORIZONS_DAYS = [30, 60, 90, 120]
CURVE_STEP_DAYS = 30
CURVE_MAX_DAYS = 1080
BATCH_CHUNK_SIZE = 2000


class ItemNotScorable(Exception):
    pass


def load_model() -> tuple:
    if not (ARTIFACTS_DIR / "models.joblib").exists():
        raise FileNotFoundError(f"Artifacts belum ada di {ARTIFACTS_DIR}")
    models = joblib.load(ARTIFACTS_DIR / "models.joblib")
    metadata = json.loads((ARTIFACTS_DIR / "metadata.json").read_text(encoding="utf-8"))
    model = models[metadata["primary_model"]]
    if hasattr(model, "n_jobs"):

        model.n_jobs = 1
    encoder = joblib.load(ARTIFACTS_DIR / "encoder.joblib")
    calibrators_path = ARTIFACTS_DIR / "calibrators.joblib"
    calibrators = joblib.load(calibrators_path) if calibrators_path.exists() else None
    return model, encoder, metadata, calibrators


def _load_primary_model():
    try:
        model, encoder, metadata, calibrators = load_model()
    except FileNotFoundError as exc:
        raise SystemExit(
            f"Model event-based belum dilatih. Jalankan dulu: "
            f"python -m partrisk.engines.survival.train ({exc})"
        ) from exc
    support_totals = {k: int(v) for k, v in metadata["support_totals"].items()}
    item_type_support_totals = {k: int(v) for k, v in metadata["item_type_support_totals"].items()}
    terminal_support_totals = {k: int(v) for k, v in metadata["terminal_support_totals"].items()}
    return (
        model, metadata["primary_model"], encoder, support_totals,
        item_type_support_totals, terminal_support_totals, calibrators,
    )


def _calibrate_risk(raw_risk: dict[int, float], calibrators) -> dict[int, float | None]:
    if calibrators is None:
        return {h: None for h in raw_risk}
    ordered_horizons = sorted(raw_risk)
    calibrated: dict[int, float | None] = {}
    running_max = 0.0
    for h in ordered_horizons:
        raw = raw_risk[h]
        if raw is None or h not in calibrators:
            calibrated[h] = None
            continue
        value = float(calibrators[h].predict([raw])[0])
        running_max = max(running_max, value)
        calibrated[h] = round(running_max, 4)
    return calibrated


def predict(item_id: str) -> dict:
    (
        model, model_name, encoder, support_totals,
        item_type_support_totals, terminal_support_totals, calibrators,
    ) = _load_primary_model()

    dataset_max_event_on = data_reader.get_dataset_max_event_on()

    cycles_for_item = data_reader.get_cycles(item_id, dataset_max_event_on)
    active = cycles_for_item.loc[cycles_for_item["cycle_end_reason"].eq("RIGHT_CENSORED_AT_DATA_END")]
    if active.empty:
        raise ItemNotScorable(f"{item_id}: tidak ada siklus yang sedang aktif (PART tidak terpasang sekarang).")
    if not bool(active.iloc[0]["is_initial_model_cohort"]):
        raise ItemNotScorable(f"{item_id}: identitas tipe PART tidak bisa dipastikan dari inventory - tidak diskor.")

    active_cycle = active.iloc[[0]].reset_index(drop=True)
    installed_on = pd.Timestamp(active_cycle.loc[0, "installed_on"])
    age_days = float((dataset_max_event_on - installed_on).total_seconds() / 86400.0)

    events = data_reader.get_events(item_id)

    observations = active_cycle.copy()
    observations["observation_on"] = dataset_max_event_on
    observations["days_since_installation"] = age_days
    observations["landmark_age_days"] = age_days
    observations = features_survival.attach_install_context(observations, events)
    terminal_raw = data_reader.get_terminal_context(item_id)
    observations = features_survival.attach_terminal_extra(observations, terminal_raw)
    observations = feature_builder.attach_history(observations, events)

    observations = feature_builder.attach_fleet_snapshot(observations, predict_failure.fleet_snapshot(dataset_max_event_on))
    observations = features_survival.attach_dynamic_extra(observations, cycles_for_item, events)
    observations["log_previous_cycle_count"] = np.log1p(observations["previous_cycle_count"].astype(float))
    observations = feature_builder.attach_item_type_density_snapshot(
        observations, events, predict_failure.item_type_density_snapshot(dataset_max_event_on)
    )

    pc = features_survival.audit_previous_cycle_features(cycles_for_item)
    observations = observations.merge(
        pc[[
            "installation_cycle_id", "previous_cycle_confirmed_failure_lifetime_mean", "last_confirmed_failure_lifetime",
        ]],
        on="installation_cycle_id", how="left",
    )
    transform = features_survival.transform_for_model(observations)[
        ["log_previous_cycle_confirmed_failure_lifetime_mean", "has_previous_cycle_confirmed_failure_lifetime_mean"]
    ]
    observations = pd.concat([observations, transform], axis=1)

    support = observations["item_model_code_clean"].map(support_totals).fillna(0).astype("int64")
    item_type_support = observations["item_type_at_install"].map(item_type_support_totals).fillna(0).astype("int64")
    terminal_support = observations["terminal_type_context"].map(terminal_support_totals).fillna(0).astype("int64")
    feature_frame = features_survival.compute_features(observations, support, item_type_support, terminal_support)
    x = features_survival.encode(feature_frame, encoder)

    times_grid, curve_values = curves.survival_curve_arrays(model, x)
    curve = curve_values[0]
    curve_calibrated = curves.calibrate_curve(times_grid, curve_values, calibrators)[0] if calibrators is not None else curve

    beyond_training_followup = bool(times_grid.max() <= 0)
    if beyond_training_followup:
        raw_risk = {h: None for h in HORIZONS_DAYS}
    else:
        raw_risk = {
            h: 1.0 - curves.eval_survival_at(times_grid, curve, float(h))
            for h in HORIZONS_DAYS
        }
    risk = {f"risk_{h}d": (round(v, 4) if v is not None else None) for h, v in raw_risk.items()}
    calibrated = _calibrate_risk(raw_risk, calibrators)
    calibrated_risk = {f"calibrated_risk_{h}d": calibrated[h] for h in HORIZONS_DAYS}
    median_days_remaining = curves.median_survival_time(times_grid, curve_calibrated)
    days_until_90pct_remaining = curves.survival_time_at_threshold(times_grid, curve_calibrated, 0.9)
    days_until_risk_medium = curves.survival_time_at_threshold(
        times_grid, curve_calibrated, 1.0 - config.FAILURE_MEDIUM_PROBABILITY_THRESHOLD
    )
    days_until_risk_high = curves.survival_time_at_threshold(
        times_grid, curve_calibrated, 1.0 - config.FAILURE_HIGH_PROBABILITY_THRESHOLD
    )

    curve_days = list(range(0, int(min(times_grid.max(), CURVE_MAX_DAYS)) + 1, CURVE_STEP_DAYS))
    curve_points = [
        {
            "days_from_now": d,
            "survival_probability": round(curves.eval_survival_at(times_grid, curve_calibrated, d), 4),
        }
        for d in curve_days
    ]

    return {
        "item_id": data_reader.normalize(item_id),
        "installed_on": str(installed_on),
        "as_of": str(dataset_max_event_on),
        "age_days": round(age_days, 1),
        **risk,
        **calibrated_risk,
        "median_days_remaining_from_now": (
            round(median_days_remaining, 1) if median_days_remaining is not None else None
        ),
        "days_until_survival_90pct_from_now": (
            round(days_until_90pct_remaining, 1) if days_until_90pct_remaining is not None else None
        ),
        "days_until_risk_medium_from_now": (
            round(days_until_risk_medium, 1) if days_until_risk_medium is not None else None
        ),
        "days_until_risk_high_from_now": (
            round(days_until_risk_high, 1) if days_until_risk_high is not None else None
        ),
        "estimated_survival_curve_from_now": curve_points,
        "curve_is_calibrated": calibrators is not None,
        "model_name": model_name,
        "note": (
            "Fitur dihitung pada KONDISI SEKARANG (observation_on=as_of), TERMASUK riwayat/armada "
            "terbaru sampai hari ini - beda dari predict/failure.py (baseline instalasi) yang fiturnya "
            "beku di installed_on. risk_Nd = P(gagal dalam N hari ke depan | kondisi sekarang)."
        ),
    }


def score_batch(
    rows: pd.DataFrame, events: pd.DataFrame, cycles: pd.DataFrame, episodes: pd.DataFrame,
    terminal_raw: pd.DataFrame, model, encoder, metadata: dict, calibrators=None,
) -> pd.DataFrame:
    feature_frame = landmark_eval.build_landmark_features_at_observation(
        rows, events, cycles, episodes, terminal_raw, metadata
    )
    n = len(feature_frame)
    median_days = np.full(n, np.nan)
    days_until_90pct = np.full(n, np.nan)
    days_until_medium = np.full(n, np.nan)
    days_until_high = np.full(n, np.nan)
    n_chunks = math.ceil(n / BATCH_CHUNK_SIZE)
    for i in range(n_chunks):
        lo, hi = i * BATCH_CHUNK_SIZE, min((i + 1) * BATCH_CHUNK_SIZE, n)
        chunk = feature_frame.iloc[lo:hi]
        x_chunk = features_survival.encode(chunk, encoder)
        times_grid, curve_values = curves.survival_curve_arrays(model, x_chunk)
        curve_values_used = curves.calibrate_curve(times_grid, curve_values, calibrators) if calibrators is not None else curve_values
        for k in range(curve_values_used.shape[0]):
            median = curves.median_survival_time(times_grid, curve_values_used[k])
            median_days[lo + k] = np.nan if median is None else median
            at_90pct = curves.survival_time_at_threshold(times_grid, curve_values_used[k], 0.9)
            days_until_90pct[lo + k] = np.nan if at_90pct is None else at_90pct
            at_medium = curves.survival_time_at_threshold(
                times_grid, curve_values_used[k], 1.0 - config.FAILURE_MEDIUM_PROBABILITY_THRESHOLD
            )
            days_until_medium[lo + k] = np.nan if at_medium is None else at_medium
            at_high = curves.survival_time_at_threshold(
                times_grid, curve_values_used[k], 1.0 - config.FAILURE_HIGH_PROBABILITY_THRESHOLD
            )
            days_until_high[lo + k] = np.nan if at_high is None else at_high
        del curve_values, curve_values_used

    return pd.DataFrame({
        "item_id": rows["item_identifier_clean"].to_numpy(),
        "median_days_to_failure": median_days,
        "days_until_survival_90pct": days_until_90pct,
        "days_until_risk_medium": days_until_medium,
        "days_until_risk_high": days_until_high,
    })


def main() -> int:
    if len(sys.argv) < 2:
        print("Pemakaian: python -m partrisk.engines.survival.predict <item_id>")
        return 1
    try:
        result = predict(sys.argv[1])
    except ItemNotScorable as exc:
        print(f"[TIDAK BISA DISKOR] {exc}")
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
