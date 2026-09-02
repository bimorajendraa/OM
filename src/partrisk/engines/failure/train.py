from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from catboost import CatBoostClassifier, Pool
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

from partrisk.core import config
from partrisk.core import data_reader
from partrisk.core import features as feature_builder
from partrisk.engines.failure import gate


def next_version(model_dir: Path) -> str:
    existing = [
        int(path.name[1:])
        for path in model_dir.glob("v*")
        if path.is_dir() and path.name[1:].isdigit()
    ]
    return f"v{max(existing, default=0) + 1}"


def current_version(model_dir: Path) -> str | None:
    pointer = model_dir / "CURRENT"
    if not pointer.exists():
        return None
    version = pointer.read_text(encoding="utf-8").strip()
    return version if (model_dir / version / "metadata.json").exists() else None


def capacity_metrics(
    raw: np.ndarray,
    target: np.ndarray,
    window_days: float,
    capacity_per_month: float,
    days_per_month: float = 30.0,
) -> dict:

    months = max(window_days / days_per_month, 1e-9)
    capacity = max(int(round(capacity_per_month * months)), 1)
    capacity = min(capacity, len(raw))
    flagged = np.argsort(-raw)[:capacity]
    true_positive = int(target[flagged].sum())
    return {
        "capacity_evaluated": capacity,
        "precision_at_capacity": true_positive / capacity if capacity else 0.0,
        "recall_at_capacity": true_positive / max(int(target.sum()), 1),
    }


def full_metrics(
    raw: np.ndarray,
    calibrated: np.ndarray,
    target: np.ndarray,
    window_days: float,
    capacity_per_month: float,
    days_per_month: float = 30.0,
) -> dict:
    metrics = {
        "rows": int(len(target)),
        "positives": int(target.sum()),
        "roc_auc": float(roc_auc_score(target, raw)),
        "pr_auc": float(average_precision_score(target, raw)),
        "brier_calibrated": float(brier_score_loss(target, calibrated)),
    }
    metrics.update(
        capacity_metrics(raw, target, window_days, capacity_per_month, days_per_month)
    )
    return metrics


def decide_promotion(
    candidate: dict, incumbent: dict | None, previous_version: str | None, force: bool,
    split_label: str = "TEST",
) -> tuple[bool, str, dict]:

    if incumbent is None:
        return True, "belum ada model production sebelumnya", {"candidate": candidate}

    comparison = {
        "candidate": candidate,
        "incumbent": incumbent,
        "incumbent_version": previous_version,
        "decisive_split": split_label,
    }
    pr_ok = candidate["pr_auc"] >= incumbent["pr_auc"]
    recall_ok = candidate["recall_at_capacity"] >= incumbent["recall_at_capacity"]
    reason = (
        f"[{split_label}] PR-AUC {candidate['pr_auc']:.4f} vs {previous_version} {incumbent['pr_auc']:.4f} | "
        f"Recall@kapasitas {candidate['recall_at_capacity']:.4f} vs "
        f"{incumbent['recall_at_capacity']:.4f} | "
        f"ROC-AUC {candidate['roc_auc']:.4f} vs {incumbent['roc_auc']:.4f} | "
        f"Brier {candidate['brier_calibrated']:.4f} vs {incumbent['brier_calibrated']:.4f}"
    )
    if pr_ok and recall_ok:
        return True, reason, comparison
    if force:
        return True, f"{reason} - dipaksa lewat --force-promote", comparison
    return False, reason, comparison


def print_promotion_comparison(
    candidate_metrics: dict, incumbent_metrics: dict | None, previous_version: str | None,
    window_days: float, unit_label: str, note: str | None = None,
) -> None:
    if incumbent_metrics is None:
        return
    print(f"\n      {note}" if note else "")
    print(
        f"      Perbandingan pada window uji yang sama ({window_days:.0f} hari, "
        f"kapasitas setara {candidate_metrics['capacity_evaluated']} {unit_label}):"
    )
    print(
        f"      {'':10s} {'PR-AUC':>8s} {'ROC-AUC':>8s} {'Recall@cap':>11s} "
        f"{'Precision@cap':>14s} {'Brier':>8s}"
    )
    for label, values in (("kandidat", candidate_metrics), (previous_version, incumbent_metrics)):
        print(
            f"      {label:10s} {values['pr_auc']:>8.4f} {values['roc_auc']:>8.4f} "
            f"{values['recall_at_capacity']:>11.4f} {values['precision_at_capacity']:>14.4f} "
            f"{values['brier_calibrated']:>8.4f}"
        )


TRAIN, VALIDATION, TEST = "TRAIN", "VALIDATION", "TEST"
CURRENT_POINTER = config.FAILURE_MODEL_DIR / "CURRENT"


def assign_split(
    observations: pd.DataFrame,
    data_end: pd.Timestamp,
    horizon_days: int = config.TARGET_HORIZON_DAYS,
) -> pd.Series:
    observed = pd.to_datetime(observations["observation_on"])
    resolved = observed + np.timedelta64(horizon_days, "D")

    test_start = pd.Timestamp(year=data_end.year, month=1, day=1)
    validation_start = test_start - pd.DateOffset(years=1)

    split = pd.Series("EXCLUDED_EMBARGO", index=observations.index)
    split[observed < pd.Timestamp(config.MIN_OBSERVATION_DATE)] = "EXCLUDED_TOO_OLD"
    split[
        (observed >= pd.Timestamp(config.MIN_OBSERVATION_DATE))
        & (resolved < validation_start)
    ] = TRAIN
    split[(observed >= validation_start) & (resolved < test_start)] = VALIDATION
    split[observed >= test_start] = TEST
    return split


def build_dataset(horizon_days: int = config.TARGET_HORIZON_DAYS) -> tuple:
    print("[1/5] Membaca event dan siklus pemasangan dari database...")
    events = data_reader.get_events()
    cycles = data_reader.get_cycles(horizon_days=horizon_days)
    data_end = pd.Timestamp(cycles["dataset_max_event_on"].max())
    print(f"      {len(events):,} event, {len(cycles):,} siklus, data s/d {data_end}")

    print("[2/5] Menyusun observasi 30-harian dan target...")
    observations = feature_builder.training_observations(cycles, horizon_days=horizon_days)
    observations = feature_builder.attach_history(observations, events)
    observations = feature_builder.attach_degradation_history(observations, cycles, events)
    episodes = data_reader.get_failure_episodes()
    observations = feature_builder.attach_fleet(observations, cycles, episodes)
    observations = feature_builder.attach_item_type_density(observations, events, cycles, episodes)

    support = feature_builder.cumulative_support(observations)
    support_totals = feature_builder.support_totals(observations)

    eligible = observations["is_eligible"].to_numpy()
    dataset = observations.loc[eligible].reset_index(drop=True)
    dataset["split"] = assign_split(dataset, data_end, horizon_days=horizon_days)
    print(
        f"      {len(observations):,} observasi -> {len(dataset):,} layak dilatih "
        f"({int(dataset['target_failure'].sum()):,} kerusakan)"
    )

    print("[3/5] Menghitung fitur...")
    features = feature_builder.build_features(
        dataset, support.loc[eligible].reset_index(drop=True)
    )
    return dataset, features, support_totals, data_end, events, cycles, episodes


def evaluate(target: pd.Series, raw: np.ndarray, calibrated: np.ndarray | None = None) -> dict:
    metrics = {
        "rows": int(len(target)),
        "positives": int(target.sum()),
        "roc_auc": float(roc_auc_score(target, raw)),
        "pr_auc": float(average_precision_score(target, raw)),
    }
    if calibrated is not None:
        metrics["brier_raw"] = float(brier_score_loss(target, raw))
        metrics["brier_calibrated"] = float(brier_score_loss(target, calibrated))
    return metrics


def train_model(dataset: pd.DataFrame, features: pd.DataFrame) -> tuple:
    parts = {name: dataset["split"].eq(name).to_numpy() for name in (TRAIN, VALIDATION, TEST)}
    for name, mask in parts.items():
        if not mask.any():
            raise SystemExit(f"Tidak ada baris untuk bagian {name}. Data belum cukup.")

    target = dataset["target_failure"].astype(bool)
    train_x, train_y = features[parts[TRAIN]], target[parts[TRAIN]]
    val_x, val_y = features[parts[VALIDATION]], target[parts[VALIDATION]]
    test_x, test_y = features[parts[TEST]], target[parts[TEST]]

    print(
        f"[4/5] Melatih model: latih={len(train_x):,} validasi={len(val_x):,} uji={len(test_x):,}"
    )
    if test_y.sum() < 30:
        print(
            f"      PERINGATAN: hanya {int(test_y.sum())} kerusakan di data uji - "
            "metrik uji akan sangat berisik. Pertimbangkan menunggu data lebih banyak."
        )

    model = CatBoostClassifier(
        random_seed=config.RANDOM_STATE, **config.CATBOOST_PARAMS
    )
    model.fit(
        Pool(train_x, train_y, cat_features=config.CATEGORICAL_FEATURES),
        eval_set=Pool(val_x, val_y, cat_features=config.CATEGORICAL_FEATURES),
    )

    raw_train = model.predict_proba(train_x)[:, 1]
    raw_val = model.predict_proba(val_x)[:, 1]
    raw_test = model.predict_proba(test_x)[:, 1]

    calibrator = IsotonicRegression(out_of_bounds="clip")
    calibrator.fit(raw_val, val_y.astype(int))

    metrics = {
        "train": evaluate(train_y, raw_train),
        "validation": evaluate(val_y, raw_val),
        "test": evaluate(test_y, raw_test, calibrator.predict(raw_test)),
    }
    return model, calibrator, metrics, raw_test


def evaluate_incumbent(previous_version: str, dataset: pd.DataFrame, split: str = TEST) -> dict:
    metadata = load_metadata(previous_version)
    directory = config.FAILURE_MODEL_DIR / previous_version
    model = CatBoostClassifier()
    model.load_model(str(directory / "model.cbm"))
    calibrator = joblib.load(directory / "calibrator.joblib")

    test_dataset = dataset.loc[dataset["split"].eq(split)]
    incumbent_support = feature_builder.part_model_support(
        test_dataset, metadata["part_model_support"]
    )
    incumbent_features = feature_builder.build_features(test_dataset, incumbent_support)

    incumbent_features = incumbent_features[metadata["features"]]

    raw = model.predict_proba(incumbent_features)[:, 1]
    calibrated = calibrator.predict(raw)
    target = test_dataset["target_failure"].astype(bool).to_numpy()
    return {
        "model_version": previous_version,
        "raw": raw,
        "calibrated": calibrated,
        "target": target,
    }


def active_part_scores(
    model, calibrator, cycles: pd.DataFrame, events: pd.DataFrame,
    support_totals: dict[str, int], episodes: pd.DataFrame, fleet: pd.DataFrame,
    item_type_density: pd.DataFrame,
) -> np.ndarray:
    snapshot = feature_builder.current_observations(cycles, events)
    snapshot = feature_builder.attach_history(snapshot, events)
    snapshot = feature_builder.attach_degradation_history(snapshot, cycles, events)
    snapshot = feature_builder.attach_fleet_snapshot(snapshot, fleet)
    snapshot = feature_builder.attach_item_type_density_snapshot(snapshot, events, item_type_density)
    support = feature_builder.part_model_support(snapshot, support_totals)
    features = feature_builder.build_features(snapshot, support)
    raw = model.predict_proba(features)[:, 1]
    return calibrator.predict(raw)


def choose_cutoffs(calibrated_30d_score: np.ndarray) -> tuple[dict, dict]:
    high = config.FAILURE_HIGH_PROBABILITY_THRESHOLD
    medium = config.FAILURE_MEDIUM_PROBABILITY_THRESHOLD
    cutoffs = {"high": high, "medium": medium}
    basis = {
        "rule": "ambang probabilitas 30-hari tetap",
        "scale": "probabilitas kerusakan 30 hari terkalibrasi - sama seperti yang ditampilkan ke pengguna",
        "active_parts_scored": int(len(calibrated_30d_score)),
        "flagged_high": int((calibrated_30d_score >= high).sum()),
        "flagged_medium_band": int(
            ((calibrated_30d_score >= medium) & (calibrated_30d_score < high)).sum()
        ),
    }
    return cutoffs, basis


def compute_gate(
    calibrated_val: np.ndarray,
    target_val: np.ndarray,
    calibrated_test: np.ndarray,
    target_test: np.ndarray,
    horizon_days: int = config.TARGET_HORIZON_DAYS,
    target_precision: float = config.FAILURE_GATE_TARGET_PRECISION,
    validation_dataset: pd.DataFrame | None = None,
    test_dataset: pd.DataFrame | None = None,
) -> dict:

    selection = gate.select_precision_constrained_threshold(
        calibrated_val, target_val, target_precision
    )
    result = {
        "horizon_days": horizon_days,
        "target_precision": target_precision,
        "feasible": selection["feasible"],
        "threshold": selection["threshold"],
        "threshold_basis": (
            "sklearn.precision_recall_curve pada VALIDATION, recall dimaksimalkan "
            "dengan syarat presisi >= target_precision, diuji SEKALI di TEST - "
            "lihat docs/EXPERIMENTS.md E-46/E-47/E-48"
        ),
        "validation_metrics": {
            "precision": selection["precision"],
            "recall": selection["recall"],
            "alerts": selection["alerts"],
        },
        "test_metrics": None,
        "lifecycle": None,
    }
    if selection["feasible"]:
        result["test_metrics"] = gate.honest_test_evaluation(
            calibrated_test, target_test, selection["threshold"]
        )

    if validation_dataset is not None and test_dataset is not None:
        lifecycle_selection = gate.select_lifecycle_threshold(
            validation_dataset, calibrated_val, target_precision
        )
        lifecycle_result = {
            "target_precision": target_precision,
            "feasible": lifecycle_selection["feasible"],
            "threshold": lifecycle_selection.get("threshold"),
            "validation_metrics": (
                {
                    "precision": lifecycle_selection["precision"],
                    "recall": lifecycle_selection["recall"],
                    "promoted_cycles": lifecycle_selection["promoted_cycles"],
                    "failed_cycles": lifecycle_selection["failed_cycles"],
                }
                if lifecycle_selection["feasible"]
                else {"best_precision_achievable": lifecycle_selection["best_precision_achievable"]}
            ),
            "test_metrics": None,
        }
        if lifecycle_selection["feasible"]:
            lifecycle_result["test_metrics"] = gate.lifecycle_metrics(
                test_dataset, calibrated_test, lifecycle_selection["threshold"]
            )
        result["lifecycle"] = lifecycle_result

    return result


def load_metadata(version: str) -> dict:
    path = config.FAILURE_MODEL_DIR / version / "metadata.json"
    return json.loads(path.read_text(encoding="utf-8"))


def save_version(
    version: str,
    model: CatBoostClassifier,
    calibrator: IsotonicRegression,
    metrics: dict,
    support_totals: dict[str, int],
    dataset: pd.DataFrame,
    data_end: pd.Timestamp,
    cutoffs: dict,
    cutoff_basis: dict,
    fleet: pd.DataFrame,
    promotion_comparison: dict,
    gate_metadata: dict,
) -> dict:
    directory = config.FAILURE_MODEL_DIR / version
    directory.mkdir(parents=True, exist_ok=True)

    model.save_model(str(directory / "model.cbm"))
    joblib.dump(calibrator, directory / "calibrator.joblib")

    fleet.to_csv(directory / "fleet_snapshot.csv", index=False)

    observed = pd.to_datetime(dataset["observation_on"])
    validation = metrics["validation"]
    metadata = {
        "model_version": version,
        "training_date": datetime.now(timezone.utc).isoformat(),
        "fleet_snapshot_at": str(data_end),
        "training_period": {
            "observation_from": str(observed.min()),
            "observation_to": str(observed.max()),
            "dataset_max_event_on": str(data_end),
            "rows_by_split": dataset["split"].value_counts().to_dict(),
        },
        "target": (
            f"PART mengalami kerusakan dalam {config.TARGET_HORIZON_DAYS} hari "
            "setelah tanggal observasi"
        ),
        "features": config.FEATURE_COLUMNS,
        "categorical_features": config.CATEGORICAL_FEATURES,
        "hyperparameters": {**config.CATBOOST_PARAMS, "random_seed": config.RANDOM_STATE},
        "evaluation_metrics": metrics,
        "validation_base_rate": validation["positives"] / validation["rows"],
        "risk_cutoffs": cutoffs,
        "cutoff_basis": cutoff_basis,
        "part_model_support": support_totals,
        "promotion_comparison": promotion_comparison,
        "gate": gate_metadata,
    }
    (directory / "metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return metadata


def main() -> int:
    parser = argparse.ArgumentParser(description="Latih (atau latih ulang) model risiko kerusakan PART 30 hari.")
    parser.add_argument(
        "--force-promote",
        action="store_true",
        help="Pakai model baru sebagai production walaupun hasil ujinya lebih buruk.",
    )
    args = parser.parse_args()

    config.FAILURE_MODEL_DIR.mkdir(parents=True, exist_ok=True)
    dataset, features, support_totals, data_end, events, cycles, episodes = build_dataset()
    model, calibrator, metrics, raw_test = train_model(dataset, features)
    fleet = feature_builder.fleet_snapshot(cycles, episodes, data_end)
    item_type_density = feature_builder.item_type_density_snapshot(cycles, events, episodes, data_end)
    cutoffs, cutoff_basis = choose_cutoffs(
        active_part_scores(
            model, calibrator, cycles, events, support_totals, episodes, fleet, item_type_density,
        )
    )

    print("[5/5] Menyimpan dan mengevaluasi promosi...")
    for name in ("train", "validation", "test"):
        part = metrics[name]
        print(
            f"      {name:10s} baris={part['rows']:>7,} kerusakan={part['positives']:>5,} "
            f"ROC-AUC={part['roc_auc']:.4f} PR-AUC={part['pr_auc']:.4f}"
        )
    print(f"      Brier terkalibrasi (uji) = {metrics['test']['brier_calibrated']:.4f}")

    test_dataset = dataset.loc[dataset["split"].eq(TEST)]
    test_observed = pd.to_datetime(test_dataset["observation_on"])
    window_days = (
        float((test_observed.max() - test_observed.min()).days) if len(test_dataset) else 0.0
    )

    candidate_support = feature_builder.part_model_support(test_dataset, support_totals)
    candidate_features = feature_builder.build_features(test_dataset, candidate_support)
    candidate_raw = model.predict_proba(candidate_features)[:, 1]
    candidate_calibrated = calibrator.predict(candidate_raw)
    candidate_metrics = full_metrics(
        candidate_raw, candidate_calibrated,
        test_dataset["target_failure"].astype(bool).to_numpy(), window_days,
        config.FAILURE_CAPACITY_PER_MONTH,
    )

    validation_dataset = dataset.loc[dataset["split"].eq(VALIDATION)]
    validation_observed = pd.to_datetime(validation_dataset["observation_on"])
    validation_window_days = (
        float((validation_observed.max() - validation_observed.min()).days)
        if len(validation_dataset) else 0.0
    )
    validation_support = feature_builder.part_model_support(validation_dataset, support_totals)
    validation_features = feature_builder.build_features(validation_dataset, validation_support)
    validation_raw = model.predict_proba(validation_features)[:, 1]
    validation_calibrated = calibrator.predict(validation_raw)
    validation_candidate_metrics = full_metrics(
        validation_raw, validation_calibrated,
        validation_dataset["target_failure"].astype(bool).to_numpy(), validation_window_days,
        config.FAILURE_CAPACITY_PER_MONTH,
    )
    gate_metadata = compute_gate(
        validation_calibrated,
        validation_dataset["target_failure"].astype(bool).to_numpy(),
        candidate_calibrated,
        test_dataset["target_failure"].astype(bool).to_numpy(),
        validation_dataset=validation_dataset,
        test_dataset=test_dataset,
    )

    previous = current_version(config.FAILURE_MODEL_DIR)
    incumbent_metrics = None
    validation_incumbent_metrics = None
    if previous is not None:
        incumbent = evaluate_incumbent(previous, dataset)
        incumbent_metrics = full_metrics(
            incumbent["raw"], incumbent["calibrated"], incumbent["target"], window_days,
            config.FAILURE_CAPACITY_PER_MONTH,
        )
        incumbent_validation = evaluate_incumbent(previous, dataset, split=VALIDATION)
        validation_incumbent_metrics = full_metrics(
            incumbent_validation["raw"], incumbent_validation["calibrated"],
            incumbent_validation["target"], validation_window_days,
            config.FAILURE_CAPACITY_PER_MONTH,
        )

    promote, reason, comparison = decide_promotion(
        validation_candidate_metrics, validation_incumbent_metrics, previous, args.force_promote,
        split_label="VALIDATION",
    )
    comparison["test_informational"] = {
        "candidate": candidate_metrics,
        "incumbent": incumbent_metrics,
        "note": (
            "dihitung untuk laporan/audit SAJA - TIDAK dipakai untuk keputusan "
            "promosi (lihat docs/DECISIONS.md §13)"
        ),
    }

    version = next_version(config.FAILURE_MODEL_DIR)
    save_version(version, model, calibrator, metrics, support_totals, dataset,
                 data_end, cutoffs, cutoff_basis, fleet, comparison, gate_metadata)
    print(f"      Tersimpan sebagai {version} di {config.FAILURE_MODEL_DIR / version}")
    if gate_metadata["feasible"]:
        test_gate = gate_metadata["test_metrics"]
        print(
            f"      Gerbang presisi>={gate_metadata['target_precision']:.0%}: threshold="
            f"{gate_metadata['threshold']:.4f}, TEST presisi={test_gate['precision']:.4f} "
            f"recall={test_gate['recall']:.4f} alert={test_gate['alerts']}"
        )
    else:
        print(f"      Gerbang presisi>={gate_metadata['target_precision']:.0%}: INFEASIBLE di VALIDATION")

    print_promotion_comparison(
        validation_candidate_metrics, validation_incumbent_metrics, previous, validation_window_days,
        unit_label="PART", note="KEPUTUSAN PROMOSI (VALIDATION):",
    )
    print_promotion_comparison(
        candidate_metrics, incumbent_metrics, previous, window_days,
        unit_label="PART", note="INFORMASI SAJA (TEST) - tidak memengaruhi keputusan promosi:",
    )

    if promote:
        CURRENT_POINTER.write_text(version, encoding="utf-8")
        print(f"\n[OK] {version} dipakai sebagai model production ({reason}).")
    else:
        print(
            f"\n[TAHAN] Model production TETAP {previous} - {reason}.\n"
            f"        {version} tetap tersimpan untuk dibandingkan. Untuk tetap "
            f"memakainya: python -m partrisk.engines.failure.train --force-promote, "
            f"atau tulis '{version}' "
            f"ke {CURRENT_POINTER}."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
