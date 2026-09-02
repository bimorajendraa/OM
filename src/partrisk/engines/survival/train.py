from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import joblib
import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sksurv.util import Surv

from partrisk.core import config
from partrisk.core import data_reader
from partrisk.core import features as feature_builder
from partrisk.core import features_survival as features
from partrisk.engines.survival import curve as survival
from partrisk.engines.failure import train as training_failure


_DEV_CACHE_PATH = config.PACKAGE_DIR / ".cache" / "survival_build_dataset.joblib"


def build() -> dict:
    if os.environ.get("SURVIVAL_BUILD_CACHE") and _DEV_CACHE_PATH.exists():
        print("      [dev cache] memuat training_survival.build() dari cache lokal...")
        return joblib.load(_DEV_CACHE_PATH)

    events = data_reader.get_events()
    cycles = data_reader.get_cycles()
    episodes = data_reader.get_failure_episodes()
    data_end = pd.Timestamp(cycles["dataset_max_event_on"].max())

    cohort = features.cohort_cycles(cycles)
    outcome = features.assign_lifecycle_outcome(cohort, data_end)

    print("      Membangun landmark (INSTALL + event organik + anchor jarang)...")
    landmarks = features.build_landmarks(outcome, events)

    landmarks = features.attach_install_context(landmarks, events)
    terminal_raw = data_reader.get_terminal_context()
    landmarks = features.attach_terminal_extra(landmarks, terminal_raw)

    cohort_with_type = features.attach_install_context(cohort, events)
    cohort_with_terminal = features.attach_terminal_extra(cohort, terminal_raw)
    support = features.point_in_time_support(landmarks, cohort, "item_model_code_clean")
    item_type_support = features.point_in_time_support(landmarks, cohort_with_type, "item_type_at_install")
    terminal_support = features.point_in_time_support(landmarks, cohort_with_terminal, "terminal_type_context")

    landmarks["days_since_installation"] = landmarks["landmark_age_days"]
    landmarks = feature_builder.attach_history(landmarks, events)
    landmarks = feature_builder.attach_fleet(landmarks, cycles, episodes)

    pc = features.audit_previous_cycle_features(cycles)

    landmarks = landmarks.merge(
        pc[[
            "installation_cycle_id",
            "previous_cycle_confirmed_failure_lifetime_mean",
            "last_confirmed_failure_lifetime",
        ]],
        on="installation_cycle_id", how="left",
    )
    transform = features.transform_for_model(landmarks)[
        ["log_previous_cycle_confirmed_failure_lifetime_mean", "has_previous_cycle_confirmed_failure_lifetime_mean"]
    ]
    landmarks = pd.concat([landmarks, transform], axis=1)

    landmarks = features.attach_dynamic_extra(landmarks, cycles, events)
    landmarks["log_previous_cycle_count"] = np.log1p(landmarks["previous_cycle_count"].astype(float))
    landmarks = feature_builder.attach_item_type_density(landmarks, events, cycles, episodes)

    feature_frame = features.compute_features(landmarks, support, item_type_support, terminal_support)

    dataset = landmarks[[
        "installation_cycle_id", "item_identifier_clean", "installed_on", "observation_on",
        "landmark_age_days", "landmark_source", "item_model_code_clean", "failure_onset_on",
        "cycle_end_on", "cycle_end_reason", "split", "cutoff_on", "duration_days", "event_observed",
    ]].reset_index(drop=True)

    result = {
        "dataset": dataset,
        "features": feature_frame,
        "support_totals": _support_totals(cohort, "item_model_code_clean"),
        "item_type_support_totals": _support_totals(cohort_with_type, "item_type_at_install"),
        "terminal_support_totals": _support_totals(cohort_with_terminal, "terminal_type_context"),
        "data_end": data_end,
        "events": events,
        "cycles": cycles,
        "episodes": episodes,
        "outcome": outcome,
        "landmarks": landmarks,
    }
    if os.environ.get("SURVIVAL_BUILD_CACHE"):
        _DEV_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(result, _DEV_CACHE_PATH)
    return result


def _support_totals(baseline: pd.DataFrame, column: str) -> dict[str, int]:
    totals = baseline.groupby(column).size()
    return {str(key): int(count) for key, count in totals.items()}


def build_landmark_features_at_observation(
    rows: pd.DataFrame, events: pd.DataFrame, cycles: pd.DataFrame, episodes: pd.DataFrame,
    terminal_raw: pd.DataFrame, metadata: dict,
) -> pd.DataFrame:
    landmarks = rows.reset_index(drop=True).copy()
    landmarks["landmark_age_days"] = landmarks["days_since_installation"]

    landmarks = features.attach_install_context(landmarks, events)
    landmarks = features.attach_terminal_extra(landmarks, terminal_raw)

    landmarks["days_since_installation"] = landmarks["landmark_age_days"]
    landmarks = feature_builder.attach_history(landmarks, events)
    landmarks = feature_builder.attach_fleet(landmarks, cycles, episodes)

    pc = features.audit_previous_cycle_features(cycles)
    landmarks = landmarks.merge(
        pc[[
            "installation_cycle_id",
            "previous_cycle_confirmed_failure_lifetime_mean",
            "last_confirmed_failure_lifetime",
        ]],
        on="installation_cycle_id", how="left",
    )
    transform = features.transform_for_model(landmarks)[
        ["log_previous_cycle_confirmed_failure_lifetime_mean", "has_previous_cycle_confirmed_failure_lifetime_mean"]
    ]
    landmarks = pd.concat([landmarks, transform], axis=1)

    landmarks = features.attach_dynamic_extra(landmarks, cycles, events)
    landmarks["log_previous_cycle_count"] = np.log1p(landmarks["previous_cycle_count"].astype(float))
    landmarks = feature_builder.attach_item_type_density(landmarks, events, cycles, episodes)

    support_totals = {k: int(v) for k, v in metadata["support_totals"].items()}
    item_type_support_totals = {k: int(v) for k, v in metadata["item_type_support_totals"].items()}
    terminal_support_totals = {k: int(v) for k, v in metadata["terminal_support_totals"].items()}

    support = landmarks["item_model_code_clean"].map(support_totals).fillna(0).astype("int64")
    item_type_support = landmarks["item_type_at_install"].map(item_type_support_totals).fillna(0).astype("int64")
    terminal_support = landmarks["terminal_type_context"].map(terminal_support_totals).fillna(0).astype("int64")

    feature_frame = features.compute_features(landmarks, support, item_type_support, terminal_support)
    return feature_frame.reset_index(drop=True)


def load_classification_test_rows() -> tuple:
    c_dataset, _c_features, _support, _data_end, _events, _cycles, _episodes = (
        training_failure.build_dataset()
    )
    test_rows = c_dataset.loc[c_dataset["split"] == training_failure.TEST].copy()
    observed = pd.to_datetime(test_rows["observation_on"])
    window_days = float((observed.max() - observed.min()).days) if len(test_rows) else 0.0
    return test_rows, window_days


def compute_risk_30d(
    model, feature_frame_by_cycle_id: pd.DataFrame, encoder, test_rows: pd.DataFrame,
    *, numeric_columns: list[str] | None = None,
) -> tuple[pd.DataFrame, np.ndarray, np.ndarray] | None:
    matched_mask = test_rows["installation_cycle_id"].isin(feature_frame_by_cycle_id.index)
    if int(matched_mask.sum()) == 0:
        return None
    rows = test_rows.loc[matched_mask]
    ages = pd.to_numeric(rows["days_since_installation"], errors="coerce").to_numpy()
    target = rows["target_failure"].astype(bool).to_numpy()

    unique_ids = rows["installation_cycle_id"].drop_duplicates().to_numpy()
    unique_features = feature_frame_by_cycle_id.loc[unique_ids]
    x_unique = features.encode(unique_features, encoder, numeric_columns)

    times_grid, curve_values = survival.survival_curve_arrays(model, x_unique)
    curve_by_cycle = dict(zip(unique_ids, curve_values))
    risk_30d = np.array(
        [
            survival.conditional_risk(times_grid, curve_by_cycle[cid], age, 30.0)
            for cid, age in zip(rows["installation_cycle_id"].to_numpy(), ages)
        ]
    )
    return rows, risk_30d, target


def score_operational(
    model, feature_frame_by_cycle_id: pd.DataFrame, encoder, test_rows: pd.DataFrame, window_days: float,
    *, numeric_columns: list[str] | None = None,
) -> dict | None:
    computed = compute_risk_30d(model, feature_frame_by_cycle_id, encoder, test_rows, numeric_columns=numeric_columns)
    if computed is None:
        return None
    rows, risk_30d, target = computed
    metrics = training_failure.full_metrics(
        risk_30d, risk_30d, target, window_days, config.FAILURE_CAPACITY_PER_MONTH
    )
    metrics["rows_matched"] = len(rows)
    metrics["rows_total_classification_test"] = len(test_rows)
    return metrics


CALIBRATION_HORIZONS_DAYS = [30, 60, 90, 120]

COMPACT_RSF_PARAMS = dict(
    n_estimators=50,
    min_samples_split=140,
    min_samples_leaf=100,
    max_features="sqrt",
    n_jobs=1,
    random_state=42,
)


def coarsen_duration_days(days: np.ndarray) -> np.ndarray:
    days = np.asarray(days, dtype=float)
    near = np.round(days)
    far = 120.0 + np.round((days - 120.0) / 60.0) * 60.0
    return np.maximum(np.where(days <= 120.0, near, far), 1.0)


def _label_at_horizon(duration_days: np.ndarray, event_observed: np.ndarray, horizon: float) -> np.ndarray:
    label = np.full(len(duration_days), np.nan)
    label[event_observed & (duration_days <= horizon)] = 1.0
    label[duration_days >= horizon] = 0.0
    return label


def fit_calibrators(model, x_val, val_duration: np.ndarray, val_event: np.ndarray) -> dict[int, IsotonicRegression]:
    times_grid, curve_values = survival.survival_curve_arrays(model, x_val)
    surv_at_horizons = survival.step_eval_matrix(times_grid, curve_values, CALIBRATION_HORIZONS_DAYS)
    raw_risk = 1.0 - surv_at_horizons

    calibrators: dict[int, IsotonicRegression] = {}
    for j, h in enumerate(CALIBRATION_HORIZONS_DAYS):
        label = _label_at_horizon(val_duration, val_event, float(h))
        usable = ~np.isnan(label)
        calibrator = IsotonicRegression(out_of_bounds="clip")
        calibrator.fit(raw_risk[usable, j], label[usable])
        calibrators[h] = calibrator
    return calibrators


def decide_survival_promotion(
    candidate_metrics: dict, incumbent_metrics: dict | None, split_label: str = "VALIDATION",
    candidate_mae_median: dict | None = None, incumbent_mae_median: dict | None = None,
) -> tuple[bool, str]:

    if incumbent_metrics is None:
        return True, "belum ada artifact production sebelumnya"

    def brier(metrics: dict, horizon: int) -> float:
        table = metrics["brier_at_horizon"]
        return float(table.get(horizon, table.get(str(horizon))))

    b30_candidate, b30_incumbent = brier(candidate_metrics, 30), brier(incumbent_metrics, 30)
    b90_candidate, b90_incumbent = brier(candidate_metrics, 90), brier(incumbent_metrics, 90)
    brier_ok = b30_candidate <= b30_incumbent and b90_candidate <= b90_incumbent
    reason = (
        f"[{split_label}] Brier@30d {b30_candidate:.4f} vs incumbent {b30_incumbent:.4f} | "
        f"Brier@90d {b90_candidate:.4f} vs incumbent {b90_incumbent:.4f}"
    )

    mae_ok = True
    if candidate_mae_median is not None:
        candidate_mae = candidate_mae_median.get("mae_days")
        incumbent_mae = (incumbent_mae_median or {}).get("mae_days")
        if incumbent_mae is None or candidate_mae is None:
            reason += " | [TEST] MAE median: tidak ada baseline incumbent, dilewati"
        else:
            mae_ok = candidate_mae <= incumbent_mae
            reason += (
                f" | [TEST] MAE median {candidate_mae:.1f}hari (n={candidate_mae_median.get('n_usable')}) "
                f"vs incumbent {incumbent_mae:.1f}hari"
            )

    return (brier_ok and mae_ok), reason


def main() -> int:
    print("[1/5] Menyusun dataset event-based (baca database, landmark)...")
    built = build()
    dataset, feature_frame = built["dataset"], built["features"]

    train_mask = (dataset["split"] == "TRAIN").to_numpy()
    val_mask = (dataset["split"] == "VALIDATION").to_numpy()
    test_mask = (dataset["split"] == "TEST").to_numpy()
    print(
        f"      TRAIN={int(train_mask.sum()):,} ({dataset.loc[train_mask,'installation_cycle_id'].nunique():,} lifecycle)  "
        f"VALIDATION={int(val_mask.sum()):,} ({dataset.loc[val_mask,'installation_cycle_id'].nunique():,} lifecycle)  "
        f"TEST={int(test_mask.sum()):,} ({dataset.loc[test_mask,'installation_cycle_id'].nunique():,} lifecycle)"
    )

    print("[2/5] Encoding fitur (one-hot kategorikal, fit di TRAIN saja)...")
    encoder = features.fit_encoder(feature_frame.loc[train_mask])
    x_train = features.encode(feature_frame.loc[train_mask], encoder)
    x_val = features.encode(feature_frame.loc[val_mask], encoder)
    x_test = features.encode(feature_frame.loc[test_mask], encoder)
    y_train = survival.make_survival_target(dataset, train_mask)
    y_val = survival.make_survival_target(dataset, val_mask)
    y_test = survival.make_survival_target(dataset, test_mask)

    print("[3/5] Melatih RSF (kandidat compact A2) + Cox PH (landmark, banyak baris/lifecycle)...")
    y_train_coarse = Surv.from_arrays(event=y_train["event"], time=coarsen_duration_days(y_train["time"]))
    models = survival.fit_models(
        x_train, y_train_coarse, params={"random_survival_forest": COMPACT_RSF_PARAMS}
    )

    metrics = survival.evaluate_models(models, y_train, x_val, y_val, x_test, y_test)
    for name in models:
        print(
            f"      {name:24s} C-index(full landmark) val={metrics[name]['validation']['c_index']:.4f}  "
            f"test={metrics[name]['test']['c_index']:.4f}"
        )

    print("[4/5] Kalibrasi RSF (4 isotonic, populasi VALIDATION)...")
    val_duration = dataset.loc[val_mask, "duration_days"].to_numpy()
    val_event = dataset.loc[val_mask, "event_observed"].to_numpy().astype(bool)
    calibrators = fit_calibrators(models["random_survival_forest"], x_val, val_duration, val_event)
    for h, calibrator in calibrators.items():
        print(f"      horizon={h}d - {len(calibrator.X_thresholds_)} titik kalibrasi")

    test_duration = dataset.loc[test_mask, "duration_days"].to_numpy()
    test_event = dataset.loc[test_mask, "event_observed"].to_numpy().astype(bool)
    times_grid_test, curve_values_test = survival.survival_curve_arrays(models["random_survival_forest"], x_test)
    calibrated_test = survival.calibrate_curve(times_grid_test, curve_values_test, calibrators)
    candidate_mae_median = survival.mae_median_days(
        times_grid_test, calibrated_test, test_duration, test_event
    )
    print(
        f"      MAE median (TEST, kalibrasi) = {candidate_mae_median['mae_days']} hari "
        f"(n_usable={candidate_mae_median['n_usable']}/{candidate_mae_median['n_event_observed']} event)"
    )

    print("[Gate R3] Membandingkan kandidat dengan artifact production (kalau ada)...")

    previous = training_failure.current_version(config.SURVIVAL_MODEL_DIR)
    incumbent_validation_metrics = None
    incumbent_mae_median = None
    if previous is not None:
        incumbent_metadata = json.loads(
            (config.SURVIVAL_MODEL_DIR / previous / "metadata.json").read_text(encoding="utf-8")
        )
        incumbent_validation_metrics = (
            incumbent_metadata["evaluation_metrics_full_landmark_rows"]
            ["random_survival_forest"]["validation"]
        )
        incumbent_mae_median = incumbent_metadata.get("mae_median_test")

    approved, gate_reason = decide_survival_promotion(
        metrics["random_survival_forest"]["validation"], incumbent_validation_metrics,
        candidate_mae_median=candidate_mae_median, incumbent_mae_median=incumbent_mae_median,
    )
    print(f"      {gate_reason}")

    print("[5/5] Menyimpan versi baru...")
    version = training_failure.next_version(config.SURVIVAL_MODEL_DIR)
    directory = config.SURVIVAL_MODEL_DIR / version
    directory.mkdir(parents=True, exist_ok=True)
    joblib.dump(models, directory / "models.joblib")
    joblib.dump(encoder, directory / "encoder.joblib")
    joblib.dump(y_train, directory / "y_train.joblib")
    joblib.dump(calibrators, directory / "calibrators.joblib")

    metadata = {
        "model_version": version,
        "training_date": datetime.now(timezone.utc).isoformat(),
        "data_end": str(built["data_end"]),
        "primary_model": "random_survival_forest",
        "unit_of_observation": (
            "one row per (lifecycle, landmark) - features/age RE-ANCHORED at each landmark's "
            "observation_on (install / organic operational event / sparse 90-365d anchor), "
            "NOT frozen at installed_on like a baseline-installation model"
        ),
        "target": "duration_days (residual time from landmark's observation_on to failure/censoring), event_observed",
        "landmark_design": {
            "sources": ["INSTALL (age=0, always)", "ORGANIC_EVENT (operational event mid-cycle)", "ANCHOR (90/180/365d then +365d, capped)"],
            "split_assignment": "follows the LIFECYCLE's installed_on (NOT per-landmark L) - see features/survival/landmarks.py docstring",
        },
        "feature_columns": features.FEATURE_COLUMNS,
        "categorical_features": features.CATEGORICAL_FEATURES,
        "category_thresholds": features.FINAL_CATEGORY_THRESHOLDS,
        "rows_by_split": dataset["split"].value_counts().to_dict(),
        "lifecycles_by_split": dataset.groupby("split")["installation_cycle_id"].nunique().to_dict(),
        "events_by_split": dataset.groupby("split")["event_observed"].sum().to_dict(),
        "support_totals": built["support_totals"],
        "item_type_support_totals": built["item_type_support_totals"],
        "terminal_support_totals": built["terminal_support_totals"],
        "hyperparameters": {
            "random_survival_forest": COMPACT_RSF_PARAMS,
            "random_survival_forest_target_coarsening": (
                "daily resolution <=120 days, 60-day steps beyond - fit() target only, "
                "NOT applied to evaluation (native_metrics uses original duration_days)"
            ),
            "cox_ph": survival.DEFAULT_COX_PARAMS,
        },
        "calibration": {
            "method": "isotonic per horizon, VALIDATION landmark rows, definite binary label "
            "(event<=horizon=1, survived-to-horizon=0, censored-before-horizon excluded)",
            "horizons_days": CALIBRATION_HORIZONS_DAYS,
            "cummax_required": True,
            "applied_to_advisory_fields": True,
        },
        "evaluation_metrics_full_landmark_rows": metrics,
        "mae_median_test": candidate_mae_median,
    }
    (directory / "metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    print(f"      Tersimpan sebagai {version} di {directory}")

    if approved:
        (config.SURVIVAL_MODEL_DIR / "CURRENT").write_text(version, encoding="utf-8")
        print(f"      [OK] {version} dipakai sebagai artifact production ({gate_reason}).")
    else:
        print(
            f"      [TAHAN] Artifact production TETAP {previous} - gagal gerbang promosi "
            f"({gate_reason}).\n"
            f"              {version} tetap tersimpan untuk dibandingkan. Untuk tetap memakainya: "
            f"tulis '{version}' ke {config.SURVIVAL_MODEL_DIR / 'CURRENT'}."
        )
    print()
    print("      PERINGATAN: C-index di atas dihitung dari SEMUA baris landmark (repeated")
    print("      measures per lifecycle - BUKAN apples-to-apples dengan populasi t0-only).")
    print("      Jalankan `python -m partrisk.cli evaluate-survival` untuk perbandingan t0-only yang adil.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
