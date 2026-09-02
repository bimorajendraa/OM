from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import precision_recall_curve


def select_precision_constrained_threshold(
    scores: np.ndarray, labels: np.ndarray, target_precision: float = 0.85
) -> dict:
    labels = np.asarray(labels).astype(bool)
    if labels.sum() == 0:
        return {
            "feasible": False,
            "threshold": None,
            "precision": None,
            "recall": None,
            "alerts": None,
            "target_precision": target_precision,
            "best_precision_achievable": 0.0,
            "reason": "Tidak ada label positif pada data ini - threshold tidak bisa dicari.",
        }

    precision, recall, thresholds = precision_recall_curve(labels, scores)
    precision, recall = precision[:-1], recall[:-1]

    qualifying = precision >= target_precision
    if not qualifying.any():
        best = float(precision.max()) if len(precision) else 0.0
        return {
            "feasible": False,
            "threshold": None,
            "precision": None,
            "recall": None,
            "alerts": None,
            "target_precision": target_precision,
            "best_precision_achievable": best,
            "reason": (
                f"Tidak ada threshold yang mencapai presisi >= {target_precision:.2f} "
                f"pada data ini; presisi tertinggi yang bisa dicapai {best:.4f}."
            ),
        }

    idx = np.flatnonzero(qualifying)[np.argmax(recall[qualifying])]
    threshold = float(thresholds[idx])
    alerts = int((scores >= threshold).sum())
    return {
        "feasible": True,
        "threshold": threshold,
        "precision": float(precision[idx]),
        "recall": float(recall[idx]),
        "alerts": alerts,
        "target_precision": target_precision,
        "best_precision_achievable": float(precision.max()),
        "reason": (
            f"Threshold {threshold:.4f} memberi presisi {precision[idx]:.4f} "
            f"(>= target {target_precision:.2f}) dengan recall {recall[idx]:.4f}, "
            f"{alerts} alert."
        ),
    }


def honest_test_evaluation(scores: np.ndarray, labels: np.ndarray, threshold: float) -> dict:
    labels = np.asarray(labels).astype(bool)
    flagged = scores >= threshold
    alerts = int(flagged.sum())
    true_positive = int(labels[flagged].sum()) if alerts else 0
    false_positive = alerts - true_positive
    return {
        "threshold": float(threshold),
        "alerts": alerts,
        "true_positive": true_positive,
        "false_positive": false_positive,
        "precision": true_positive / alerts if alerts else 0.0,
        "recall": true_positive / max(int(labels.sum()), 1),
        "positives": int(labels.sum()),
        "rows": int(len(labels)),
    }


def _first_alert_per_cycle(dataset: pd.DataFrame, scores: np.ndarray, threshold: float) -> pd.DataFrame:
    frame = dataset[
        ["installation_cycle_id", "observation_on", "target_failure", "failure_onset_on"]
    ].copy()
    frame["score"] = np.asarray(scores)
    flagged = frame.loc[frame["score"] >= threshold].sort_values(
        ["installation_cycle_id", "observation_on"], kind="stable"
    )
    return flagged.drop_duplicates("installation_cycle_id", keep="first").set_index(
        "installation_cycle_id"
    )


def lifecycle_metrics(dataset: pd.DataFrame, scores: np.ndarray, threshold: float) -> dict:
    """Precision/recall/lead-time DI TINGKAT LIFECYCLE untuk satu threshold.

    `dataset` wajib punya kolom installation_cycle_id/observation_on/
    target_failure/failure_onset_on (skema `training_observations()` -
    persis yang dipakai `train.py::build_dataset()`).
    """
    per_cycle_failed = dataset.groupby("installation_cycle_id")["target_failure"].any()
    failed_cycles = int(per_cycle_failed.sum())
    total_cycles = int(len(per_cycle_failed))

    first_alert = _first_alert_per_cycle(dataset, scores, threshold)
    promoted_cycles = int(len(first_alert))
    true_positive_cycles = int(first_alert["target_failure"].sum())
    false_positive_cycles = promoted_cycles - true_positive_cycles
    false_negative_cycles = failed_cycles - true_positive_cycles

    caught = first_alert.loc[first_alert["target_failure"]]
    lead_time_days = (
        pd.to_datetime(caught["failure_onset_on"]) - pd.to_datetime(caught["observation_on"])
    ).dt.total_seconds() / 86400.0

    return {
        "threshold": float(threshold),
        "total_cycles": total_cycles,
        "failed_cycles": failed_cycles,
        "promoted_cycles": promoted_cycles,
        "true_positive_cycles": true_positive_cycles,
        "false_positive_cycles": false_positive_cycles,
        "false_negative_cycles": false_negative_cycles,
        "precision": true_positive_cycles / promoted_cycles if promoted_cycles else 0.0,
        "recall": true_positive_cycles / failed_cycles if failed_cycles else 0.0,
        "lead_time_days_mean": float(lead_time_days.mean()) if len(lead_time_days) else None,
        "lead_time_days_median": float(lead_time_days.median()) if len(lead_time_days) else None,
    }


def select_lifecycle_threshold(
    dataset: pd.DataFrame, scores: np.ndarray, target_precision: float = 0.85,
) -> dict:
    """Cari threshold yang MEMAKSIMALKAN recall lifecycle dengan syarat
    presisi lifecycle >= target_precision - metodologi sama seperti
    `select_precision_constrained_threshold()` (dicari HANYA di split yang
    diberikan, diuji sekali jujur di split lain lewat `lifecycle_metrics()`
    langsung), hanya metrik dasarnya diganti ke tingkat lifecycle.

    WHY: kandidat threshold = NILAI SKOR UNIK yang benar-benar muncul,
    BUKAN grid quantile - kalibrator isotonic biasanya cuma menghasilkan
    puluhan nilai unik dan yang paling berguna sering di baris paling
    jarang (mis. hanya 26/49.660 baris VALIDATION >= 0,375 pada model v4).
    Grid quantile merata melewatkan celah sesempit itu (percentile gap-nya
    lebih lebar dari spacing grid), diam-diam melompat ke threshold
    degenerate berikutnya - persis bug yang ditemukan saat menguji fungsi
    ini pertama kali.
    """
    candidates = np.unique(np.asarray(scores))
    best_feasible: dict | None = None
    best_precision_overall = 0.0
    for candidate in candidates:
        metrics = lifecycle_metrics(dataset, scores, float(candidate))
        if metrics["promoted_cycles"] == 0:
            continue
        best_precision_overall = max(best_precision_overall, metrics["precision"])
        if metrics["precision"] >= target_precision and (
            best_feasible is None or metrics["recall"] > best_feasible["recall"]
        ):
            best_feasible = metrics

    if best_feasible is None:
        return {
            "feasible": False,
            "threshold": None,
            "target_precision": target_precision,
            "best_precision_achievable": best_precision_overall,
        }
    return {"feasible": True, "target_precision": target_precision, **best_feasible}


def reliability_table(calibrated: np.ndarray, labels: np.ndarray, n_bins: int = 10) -> pd.DataFrame:
    frame = pd.DataFrame({"predicted": calibrated, "observed": np.asarray(labels).astype(int)})
    frame["bucket"] = pd.qcut(frame["predicted"], q=n_bins, duplicates="drop")
    return (
        frame.groupby("bucket", observed=True)
        .agg(
            mean_predicted=("predicted", "mean"),
            observed_rate=("observed", "mean"),
            n=("observed", "size"),
        )
        .reset_index()
    )
