from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import (
    ExtraTreesClassifier,
    GradientBoostingClassifier,
    RandomForestClassifier,
    VotingClassifier,
)
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    balanced_accuracy_score,
    confusion_matrix,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from partrisk.core import config
from partrisk.core import features as feature_builder
from partrisk.core import data_reader
from partrisk.engines.failure import train as training_utils

CURRENT_POINTER = config.SCRAP_MODEL_DIR / "CURRENT"


def candidate_models() -> dict[str, Pipeline | VotingClassifier]:
    def scaled() -> ColumnTransformer:
        return ColumnTransformer([
            ("num", StandardScaler(), config.SCRAP_NUMERIC_FEATURES),
            ("cat", OneHotEncoder(handle_unknown="ignore", sparse_output=False),
             config.SCRAP_CATEGORICAL_FEATURES),
        ])

    def plain() -> ColumnTransformer:
        return ColumnTransformer([
            ("num", "passthrough", config.SCRAP_NUMERIC_FEATURES),
            ("cat", OneHotEncoder(handle_unknown="ignore", sparse_output=False),
             config.SCRAP_CATEGORICAL_FEATURES),
        ])

    def logistic() -> Pipeline:
        return Pipeline([("prep", scaled()), ("model", LogisticRegression(
            max_iter=5000, class_weight="balanced", C=0.3,
            random_state=config.SCRAP_RANDOM_STATE))])

    def forest() -> Pipeline:
        return Pipeline([("prep", plain()), ("model", RandomForestClassifier(
            n_estimators=500, max_depth=4, min_samples_leaf=10, class_weight="balanced",
            random_state=config.SCRAP_RANDOM_STATE, n_jobs=-1))])

    return {
        "Selalu 'bisa diperbaiki'": Pipeline([
            ("prep", plain()), ("model", DummyClassifier(strategy="prior"))]),
        "Regresi Logistik": logistic(),
        "Random Forest": forest(),
        "Extra Trees": Pipeline([("prep", plain()), ("model", ExtraTreesClassifier(
            n_estimators=500, max_depth=4, min_samples_leaf=10, class_weight="balanced",
            random_state=config.SCRAP_RANDOM_STATE, n_jobs=-1))]),
        "Gradient Boosting": Pipeline([("prep", plain()), ("model", GradientBoostingClassifier(
            n_estimators=100, max_depth=2, learning_rate=0.05,
            random_state=config.SCRAP_RANDOM_STATE))]),
        "Gabungan LogReg + RF": VotingClassifier(
            [("logreg", logistic()), ("forest", forest())], voting="soft"),
    }


def build_dataset() -> tuple[pd.DataFrame, pd.DataFrame, np.ndarray, list[str]]:
    print("[1/5] Membaca kerusakan, event, dan siklus dari database...")
    episodes = data_reader.get_failure_episodes()
    events = data_reader.get_events()
    cycles = data_reader.get_cycles()
    data_end = pd.Timestamp(cycles["dataset_max_event_on"].max())
    print(f"      {len(episodes):,} kerusakan, data s/d {data_end}")

    print("[2/5] Menentukan nasib tiap kerusakan...")
    labeled = feature_builder.resolve_outcomes(episodes, events, cycles, data_end)
    era = labeled["failure_onset_on"] >= pd.Timestamp(config.SCRAP_ERA_START)
    dataset = labeled.loc[
        era & labeled["is_initial_model_cohort"].fillna(False) & labeled["is_labeled"]
    ].reset_index(drop=True)
    unlabeled = int((era & labeled["is_initial_model_cohort"].fillna(False) & ~labeled["is_labeled"]).sum())
    print(f"      {len(dataset):,} kerusakan bisa dilabeli, {unlabeled:,} nasibnya tidak diketahui")

    print("[3/5] Menghitung fitur...")
    known_types = feature_builder.known_item_types(dataset)
    features = feature_builder.build_scrap_features(dataset, known_types)
    return dataset, features, dataset["is_scrap"].to_numpy(), known_types


def compare_models(features: pd.DataFrame, target: np.ndarray, onset: pd.Series) -> pd.DataFrame:
    is_test = (onset >= pd.Timestamp(config.SCRAP_TEST_START)).to_numpy()
    late = [c for c in config.SCRAP_ROLLING_CUTOFFS if c >= config.SCRAP_TEST_START]
    if late:
        raise SystemExit(
            f"Titik potong {late} berada di dalam periode uji. Pemilihan model "
            "tidak boleh melihat data uji - perbaiki SCRAP_ROLLING_CUTOFFS."
        )
    rows = []
    for name, pipeline in candidate_models().items():
        rolling_roc, rolling_pr = [], []
        for cutoff in config.SCRAP_ROLLING_CUTOFFS:
            future = (
                (onset >= pd.Timestamp(cutoff))
                & (onset < pd.Timestamp(config.SCRAP_TEST_START))
            ).to_numpy()
            pipeline.fit(features[~future], target[~future])
            probability = pipeline.predict_proba(features[future])[:, 1]
            rolling_roc.append(roc_auc_score(target[future], probability))
            rolling_pr.append(average_precision_score(target[future], probability))

        pipeline.fit(features[~is_test], target[~is_test])
        probability = pipeline.predict_proba(features[is_test])[:, 1]
        rows.append({
            "model": name,
            "rolling_roc": float(np.mean(rolling_roc)),
            "rolling_pr": float(np.mean(rolling_pr)),
            "test_roc_auc": roc_auc_score(target[is_test], probability),
            "test_pr_auc": average_precision_score(target[is_test], probability),
        })
    return pd.DataFrame(rows)


def fit_calibrator(out_of_fold: np.ndarray, target: np.ndarray) -> LogisticRegression:
    return LogisticRegression().fit(out_of_fold.reshape(-1, 1), target)


def choose_cutoffs(
    calibrated_score: np.ndarray, onset: pd.Series
) -> tuple[float, float, dict]:
    span_months = max((onset.max() - onset.min()).days / 30.44, 1.0)
    per_month = len(onset) / span_months
    high_share = min(config.SCRAP_CAPACITY_PER_MONTH / per_month, 1.0)
    medium_share = min(
        config.SCRAP_MEDIUM_CAPACITY_MULTIPLIER * config.SCRAP_CAPACITY_PER_MONTH / per_month, 1.0
    )
    high = float(np.quantile(calibrated_score, 1 - high_share))
    medium = float(np.quantile(calibrated_score, 1 - medium_share))
    basis = {
        "rule": "kapasitas kerja per bulan yang ditetapkan bisnis",
        "scale": "probabilitas terkalibrasi",
        "capacity_per_month": config.SCRAP_CAPACITY_PER_MONTH,
        "failures_per_month_in_training": round(per_month, 1),
        "flagged_share_high": round(high_share, 4),
    }
    return high, medium, basis


def evaluate_incumbent(previous_version: str, dataset: pd.DataFrame, is_test: np.ndarray) -> dict:
    metadata = json.loads(
        (config.SCRAP_MODEL_DIR / previous_version / "metadata.json").read_text(encoding="utf-8")
    )
    pipeline = joblib.load(config.SCRAP_MODEL_DIR / previous_version / "model.joblib")
    calibrator = joblib.load(config.SCRAP_MODEL_DIR / previous_version / "calibrator.joblib")

    test_dataset = dataset.loc[is_test]
    test_features = feature_builder.build_scrap_features(test_dataset, metadata["known_item_types"])
    raw = pipeline.predict_proba(test_features)[:, 1]
    calibrated = calibrator.predict_proba(raw.reshape(-1, 1))[:, 1]
    target = test_dataset["is_scrap"].to_numpy()
    return {"model_version": previous_version, "raw": raw, "calibrated": calibrated, "target": target}


def main() -> int:
    parser = argparse.ArgumentParser(description="Latih (atau latih ulang) model risiko scrap.")
    parser.add_argument("--force-promote", action="store_true",
                        help="Pakai model baru walaupun hasil ujinya lebih buruk.")
    args = parser.parse_args()

    config.SCRAP_MODEL_DIR.mkdir(parents=True, exist_ok=True)
    dataset, features, target, known_types = build_dataset()
    onset = dataset["failure_onset_on"]
    is_test = (onset >= pd.Timestamp(config.SCRAP_TEST_START)).to_numpy()

    print(f"      LATIH {(~is_test).sum():,} / {target[~is_test].sum()} dibuang "
          f"({target[~is_test].mean():.1%})   "
          f"UJI {is_test.sum():,} / {target[is_test].sum()} dibuang ({target[is_test].mean():.1%})")

    print("[4/5] Membandingkan kandidat model...")
    comparison = compare_models(features, target, onset)
    print(comparison.to_string(index=False, float_format=lambda v: f"{v:.3f}"))

    best_name = config.SCRAP_MODEL_NAME
    ranked = comparison[comparison.model != "Selalu 'bisa diperbaiki'"].sort_values(
        "rolling_pr", ascending=False)
    print(f"\n      Model ditetapkan di muka: {best_name}")
    leader = ranked.iloc[0]
    if leader["model"] != best_name:
        own = comparison.set_index("model").loc[best_name, "rolling_pr"]
        print(f"      Catatan: {leader['model']} memimpin tabel pemeriksaan "
              f"(PR {leader['rolling_pr']:.3f} vs {own:.3f}), tetapi selisih pada")
        print("      fold sekecil ini belum bisa dibedakan dari derau.")

    pipeline = candidate_models()[best_name]
    folds = StratifiedKFold(n_splits=5, shuffle=True, random_state=config.SCRAP_RANDOM_STATE)
    out_of_fold = cross_val_predict(
        pipeline, features[~is_test], target[~is_test], cv=folds,
        method="predict_proba", n_jobs=1)[:, 1]
    calibrator = fit_calibrator(out_of_fold, target[~is_test])
    calibrated_oof = calibrator.predict_proba(out_of_fold.reshape(-1, 1))[:, 1]
    high_cutoff, medium_cutoff, cutoff_basis = choose_cutoffs(
        calibrated_oof, onset[~is_test])
    pipeline.fit(features[~is_test], target[~is_test])

    raw = pipeline.predict_proba(features[is_test])[:, 1]
    probability = calibrator.predict_proba(raw.reshape(-1, 1))[:, 1]
    predicted = (probability >= high_cutoff).astype(int)
    actual = target[is_test]
    metrics = {
        "rows": int(is_test.sum()),
        "positives": int(actual.sum()),
        "roc_auc": float(roc_auc_score(actual, probability)),
        "pr_auc": float(average_precision_score(actual, probability)),
        "pr_auc_baseline": float(actual.mean()),
        "accuracy": float(accuracy_score(actual, predicted)),
        "balanced_accuracy": float(balanced_accuracy_score(actual, predicted)),
        "precision": float(precision_score(actual, predicted, zero_division=0)),
        "recall": float(recall_score(actual, predicted)),
        "confusion_matrix": confusion_matrix(actual, predicted).tolist(),
    }

    test_onset = onset[is_test]
    window_days = float((test_onset.max() - test_onset.min()).days) if is_test.sum() else 0.0
    candidate_metrics = training_utils.full_metrics(
        raw, probability, actual, window_days,
        config.SCRAP_CAPACITY_PER_MONTH, days_per_month=30.44,
    )

    previous = training_utils.current_version(config.SCRAP_MODEL_DIR)
    incumbent_metrics = None
    if previous is not None:
        incumbent = evaluate_incumbent(previous, dataset, is_test)
        incumbent_metrics = training_utils.full_metrics(
            incumbent["raw"], incumbent["calibrated"], incumbent["target"], window_days,
            config.SCRAP_CAPACITY_PER_MONTH, days_per_month=30.44,
        )

    promote, reason, promotion_comparison = training_utils.decide_promotion(
        candidate_metrics, incumbent_metrics, previous, args.force_promote
    )

    version = training_utils.next_version(config.SCRAP_MODEL_DIR)
    directory = config.SCRAP_MODEL_DIR / version
    directory.mkdir(parents=True, exist_ok=True)
    joblib.dump(pipeline, directory / "model.joblib")
    joblib.dump(calibrator, directory / "calibrator.joblib")
    metadata = {
        "model_version": version,
        "training_date": datetime.now(timezone.utc).isoformat(),
        "question": "Saat sebuah PART rusak, apakah kerusakan itu berakhir dibuang?",
        "selected_model": best_name,
        "selection_rule": ("model ditetapkan di muka, bukan dipilih dari data - "
                           "fold pemeriksaan terlalu sedikit kejadiannya"),
        "training_period": {
            "era_start": config.SCRAP_ERA_START,
            "embargo_days": config.SCRAP_EMBARGO_DAYS,
            "onset_from": str(onset.min()),
            "onset_to": str(onset.max()),
            "test_start": config.SCRAP_TEST_START,
        },
        "target": "vonis UNREPAIRABLE atau BROKEN setelah kerusakan",
        "features": config.SCRAP_FEATURE_COLUMNS,
        "categorical_features": config.SCRAP_CATEGORICAL_FEATURES,
        "known_item_types": known_types,
        "risk_cutoffs": {"high": high_cutoff, "medium": medium_cutoff},
        "cutoff_basis": cutoff_basis,
        "rows": {"total": int(len(dataset)), "scrap": int(target.sum())},
        "model_comparison": comparison.to_dict(orient="records"),
        "evaluation_metrics": metrics,
        "promotion_comparison": promotion_comparison,
        "limitations": [
            "Kejadian scrap sedikit - metrik uji berisik dan rentang ketidakpastiannya lebar.",
            "Kerusakan tanpa vonis DAN tidak pernah dipasang lagi tidak bisa dilabeli, jadi tidak dipelajari.",
            "Base rate masih naik antar-kuartal; peringkat risiko lebih bisa dipercaya daripada probabilitas absolut.",
            "Jenis PART di luar known_item_types masuk kelompok LOW_SUPPORT dan cenderung diberi risiko tinggi.",
        ],
    }
    (directory / "metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\n[5/5] Tersimpan sebagai {version} di {directory}")
    print(f"      ROC-AUC={metrics['roc_auc']:.3f}  "
          f"PR-AUC={metrics['pr_auc']:.3f} (acak {metrics['pr_auc_baseline']:.3f}, "
          f"lift {metrics['pr_auc'] / metrics['pr_auc_baseline']:.1f}x)")
    print(f"      Ambang={high_cutoff:.2f} (dari kapasitas "
          f"{config.SCRAP_CAPACITY_PER_MONTH}/bulan)  akurasi={metrics['accuracy']:.1%}  "
          f"balanced={metrics['balanced_accuracy']:.1%}  "
          f"presisi={metrics['precision']:.1%}  recall={metrics['recall']:.1%}")

    training_utils.print_promotion_comparison(
        candidate_metrics, incumbent_metrics, previous, window_days, unit_label="kerusakan"
    )

    if promote:
        CURRENT_POINTER.write_text(version, encoding="utf-8")
        print(f"\n[OK] {version} dipakai sebagai model production ({reason}).")
    else:
        print(f"\n[TAHAN] Model production TETAP {previous} - {reason}.\n"
              f"        {version} tetap tersimpan untuk dibandingkan.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
