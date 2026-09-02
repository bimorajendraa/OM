"""Kandidat model failure - window 2025+ TERBATAS + fitur MTBF.

WHY MODUL TERPISAH DARI train.py: bukan pengganti model production (v4),
artifact-nya (models/failure_mtbf_2025plus/) TIDAK PERNAH dibaca predict.py/
serving apa pun - murni jalur eksperimen yang dijalankan ULANG secara
berkala untuk MEMANTAU apakah kandidat ini sudah melampaui v4 (docs/
EXPERIMENTS.md E-66: MTBF menang konsisten di 3 fold TAPI PR-AUC-nya masih
~10% di bawah v4 karena TRAIN jauh lebih kecil - window 2025+ tumbuh tiap
bulan, jarak itu diperkirakan mengecil seiring waktu). Reuse langsung
training_failure.train_model()/save di bawah, gate.py (E-49) - TIDAK
menduplikasi logic training/evaluasi, cuma skema dataset & fitur MTBF yang
baru. `journal.t_mtbf` HANYA berisi data sejak 2025-01-15 (docs/
EXPERIMENTS.md E-48) - TRAIN/VALIDATION/TEST di sini SENGAJA seluruhnya di
dalam window itu, beda dari skema production (2014-sekarang) yang TIDAK
BISA mengujinya (TRAIN cakupan 0%).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from partrisk.core import config
from partrisk.core import data_reader
from partrisk.core import features as feature_builder
from partrisk.engines.failure import gate
from partrisk.engines.failure import train as training_failure
from partrisk.engines import predict as failure_model


MTBF_COVERAGE_START = pd.Timestamp("2025-01-15")
TEST_WINDOW_DAYS = 120
VALIDATION_WINDOW_DAYS = 90

CANDIDATE_MODEL_DIR = config.MODEL_DIR / "failure_mtbf_2025plus"
NEW_COLUMNS = [
    "has_mtbf_reading", "log_days_since_mtbf_reading", "log_last_time_operation_minutes",
]


def default_split_boundaries(data_end: pd.Timestamp) -> tuple[pd.Timestamp, pd.Timestamp]:

    test_start = data_end - pd.Timedelta(days=TEST_WINDOW_DAYS)
    validation_start = test_start - pd.Timedelta(days=VALIDATION_WINDOW_DAYS)
    return validation_start, test_start


def assign_restricted_split(
    observations: pd.DataFrame, validation_start: pd.Timestamp, test_start: pd.Timestamp,
) -> pd.Series:
    observed = pd.to_datetime(observations["observation_on"])
    resolved = observed + np.timedelta64(config.TARGET_HORIZON_DAYS, "D")
    split = pd.Series("EXCLUDED", index=observations.index)
    split[
        (observed >= MTBF_COVERAGE_START) & (resolved < validation_start)
    ] = training_failure.TRAIN
    split[(observed >= validation_start) & (resolved < test_start)] = training_failure.VALIDATION
    split[observed >= test_start] = training_failure.TEST
    return split


def _sn_to_item_mapping(conn) -> dict[str, str]:
    rows = data_reader._query(
        conn,
        """
        SELECT DISTINCT sn_ref, item_pairing_code FROM inventory.t_item
        WHERE sn_ref IS NOT NULL AND item_pairing_code IS NOT NULL
        """,
    )
    return {
        data_reader._normalize(sn): data_reader._normalize(pairing)
        for sn, pairing in rows.itertuples(index=False, name=None)
    }


def _mtbf_records_by_item(conn, sn_to_item: dict[str, str]) -> dict[str, tuple]:
    rows = data_reader._query(
        conn,
        """
        SELECT sn_ref, time_operation, created_on FROM journal.t_mtbf
        WHERE sn_ref IS NOT NULL AND time_operation IS NOT NULL
        ORDER BY sn_ref, created_on
        """,
    )
    rows["item_identifier_clean"] = rows["sn_ref"].map(
        lambda s: sn_to_item.get(data_reader._normalize(s))
    )
    rows = rows.dropna(subset=["item_identifier_clean"]).sort_values(
        ["item_identifier_clean", "created_on"], kind="stable"
    )
    return {
        item: (sub["created_on"].to_numpy("datetime64[ns]"), sub["time_operation"].to_numpy(dtype=float))
        for item, sub in rows.groupby("item_identifier_clean", sort=False)
    }


def attach_mtbf_features(observations: pd.DataFrame, mtbf_by_item: dict[str, tuple]) -> pd.DataFrame:
    """Untuk tiap baris, ambil bacaan `time_operation` PALING BARU yang
    `created_on`-nya SUDAH terjadi sebelum `observation_on` - tidak pernah
    membaca bacaan yang belum ada pada titik waktu itu."""
    observations = observations.reset_index(drop=True)
    n = len(observations)
    has_reading = np.zeros(n, dtype=bool)
    days_since = np.full(n, np.nan)
    last_value = np.full(n, np.nan)
    at = observations["observation_on"].to_numpy("datetime64[ns]")

    for item, rows in observations.groupby("item_identifier_clean", sort=False).indices.items():
        pair = mtbf_by_item.get(item)
        if pair is None:
            continue
        times, values = pair
        rows_arr = np.asarray(rows)
        pos = np.searchsorted(times, at[rows_arr], side="right")
        ok = pos > 0
        if ok.any():
            idx = pos[ok] - 1
            has_reading[rows_arr[ok]] = True
            days_since[rows_arr[ok]] = (at[rows_arr[ok]] - times[idx]) / np.timedelta64(1, "D")
            last_value[rows_arr[ok]] = values[idx]

    out = pd.DataFrame(index=observations.index)
    out["has_mtbf_reading"] = has_reading.astype(float)
    out["log_days_since_mtbf_reading"] = np.log1p(np.nan_to_num(days_since, nan=0.0).clip(min=0))
    out["log_last_time_operation_minutes"] = np.log1p(np.nan_to_num(last_value, nan=0.0).clip(min=0))
    return out


def build_dataset(
    validation_start: pd.Timestamp | None = None, test_start: pd.Timestamp | None = None,
) -> dict:
    print("[1/4] Membaca cycles/events/episodes (sama seperti train.py::build_dataset)...")
    cycles = data_reader.get_cycles()
    events = data_reader.get_events()
    episodes = data_reader.get_failure_episodes()
    data_end = pd.Timestamp(cycles["dataset_max_event_on"].max())

    if validation_start is None or test_start is None:
        validation_start, test_start = default_split_boundaries(data_end)

    observations = feature_builder.training_observations(cycles)
    observations = feature_builder.attach_history(observations, events)
    observations = feature_builder.attach_degradation_history(observations, cycles, events)
    observations = feature_builder.attach_fleet(observations, cycles, episodes)
    observations = feature_builder.attach_item_type_density(observations, events, cycles, episodes)

    eligible = observations.loc[observations["is_eligible"]].reset_index(drop=True)
    eligible = eligible.loc[eligible["observation_on"] >= MTBF_COVERAGE_START].reset_index(drop=True)
    eligible["split"] = assign_restricted_split(eligible, validation_start, test_start)
    dataset = eligible.loc[
        eligible["split"].isin([training_failure.TRAIN, training_failure.VALIDATION, training_failure.TEST])
    ].reset_index(drop=True)

    print(f"      window {MTBF_COVERAGE_START.date()} s/d {data_end.date()} "
          f"(VALIDATION>={validation_start.date()}, TEST>={test_start.date()})")
    for name in (training_failure.TRAIN, training_failure.VALIDATION, training_failure.TEST):
        n = int(dataset["split"].eq(name).sum())
        pos = int(dataset.loc[dataset["split"].eq(name), "target_failure"].sum())
        print(f"        {name:10s}: {n:,} baris, {pos:,} kerusakan")

    support = feature_builder.cumulative_support(dataset)
    support_totals = feature_builder.support_totals(dataset)
    baseline_features = feature_builder.build_features(dataset, support)

    print("\n[2/4] Menghitung fitur MTBF (point-in-time safe)...")
    with data_reader.connect() as conn:
        sn_to_item = _sn_to_item_mapping(conn)
        mtbf_by_item = _mtbf_records_by_item(conn, sn_to_item)
    mtbf_features = attach_mtbf_features(dataset, mtbf_by_item)
    print(f"      cakupan has_mtbf_reading: {mtbf_features['has_mtbf_reading'].mean():.2%}")

    candidate_features = pd.concat(
        [baseline_features.reset_index(drop=True), mtbf_features.reset_index(drop=True)], axis=1
    )
    return {
        "dataset": dataset,
        "baseline_features": baseline_features,
        "candidate_features": candidate_features,
        "baseline_columns": list(baseline_features.columns),
        "candidate_columns": list(baseline_features.columns) + NEW_COLUMNS,
        "support_totals": support_totals,
        "data_end": data_end,
        "validation_start": validation_start,
        "test_start": test_start,
    }


def train_and_compare(built: dict) -> dict:
    dataset = built["dataset"]

    print("\n[3/4] Melatih kandidat (+MTBF, fitur/hyperparameter identik production)...")
    candidate_x = built["candidate_features"][built["candidate_columns"]]
    model, calibrator, metrics, raw_test = training_failure.train_model(dataset, candidate_x)

    val_mask = dataset["split"].eq(training_failure.VALIDATION).to_numpy()
    test_mask = dataset["split"].eq(training_failure.TEST).to_numpy()
    val_dataset, test_dataset = dataset.loc[val_mask], dataset.loc[test_mask]
    val_raw = model.predict_proba(candidate_x[val_mask])[:, 1]
    val_calibrated = calibrator.predict(val_raw)
    test_calibrated = calibrator.predict(raw_test)

    print("      metrik row-level:")
    for name in ("train", "validation", "test"):
        m = metrics[name]
        print(f"        {name:10s} baris={m['rows']:>7,} kerusakan={m['positives']:>5,} "
              f"ROC-AUC={m['roc_auc']:.4f} PR-AUC={m['pr_auc']:.4f}")

    print("\n[4/4] Bandingkan vs v4 production (dukungan beku v4, skor pada POPULASI TEST YANG SAMA)...")
    v4_model, v4_calibrator, v4_metadata = failure_model.load_failure_model()
    v4_support = feature_builder.part_model_support(test_dataset, v4_metadata["part_model_support"])
    v4_features_frame = feature_builder.build_features(test_dataset, v4_support)[v4_metadata["features"]]
    v4_raw_test = v4_model.predict_proba(v4_features_frame)[:, 1]
    v4_test_calibrated = v4_calibrator.predict(v4_raw_test)

    from sklearn.metrics import roc_auc_score, average_precision_score
    target_test = dataset.loc[test_mask, "target_failure"].astype(bool).to_numpy()
    v4_roc = roc_auc_score(target_test, v4_raw_test)
    v4_pr = average_precision_score(target_test, v4_raw_test)
    print(f"      v4 pada window TEST kandidat ini: ROC-AUC={v4_roc:.4f} PR-AUC={v4_pr:.4f}")
    print(f"      kandidat (+MTBF)                : ROC-AUC={metrics['test']['roc_auc']:.4f} PR-AUC={metrics['test']['pr_auc']:.4f}")
    beats_v4 = metrics["test"]["pr_auc"] > v4_pr and metrics["test"]["roc_auc"] > v4_roc
    print(f"      kandidat mengalahkan v4 di window TEST ini (ROC-AUC DAN PR-AUC)? {'YA' if beats_v4 else 'BELUM'}")

    print("\n      Gerbang presisi LIFECYCLE (E-49), kandidat vs v4, target 0,30 dan 0,40...")
    gate_comparison = {}
    for target_precision in (0.30, 0.40):
        selection = gate.select_lifecycle_threshold(val_dataset, val_calibrated, target_precision=target_precision)
        cand_eval = (
            gate.lifecycle_metrics(test_dataset, test_calibrated, selection["threshold"])
            if selection["feasible"] else None
        )
        v4_selection = gate.select_lifecycle_threshold(val_dataset, v4_calibrator.predict(
            v4_model.predict_proba(feature_builder.build_features(
                val_dataset, feature_builder.part_model_support(val_dataset, v4_metadata["part_model_support"])
            )[v4_metadata["features"]])[:, 1]
        ), target_precision=target_precision)
        v4_eval = (
            gate.lifecycle_metrics(test_dataset, v4_test_calibrated, v4_selection["threshold"])
            if v4_selection["feasible"] else None
        )
        gate_comparison[target_precision] = {"candidate": cand_eval, "v4": v4_eval}
        cand_str = (
            f"presisi={cand_eval['precision']:.4f} recall={cand_eval['recall']:.4f} alert={cand_eval['promoted_cycles']}"
            if cand_eval else "INFEASIBLE"
        )
        v4_str = (
            f"presisi={v4_eval['precision']:.4f} recall={v4_eval['recall']:.4f} alert={v4_eval['promoted_cycles']}"
            if v4_eval else "INFEASIBLE"
        )
        print(f"        target={target_precision:.2f}  kandidat: {cand_str}  |  v4: {v4_str}")

    return {
        "model": model, "calibrator": calibrator, "metrics": metrics,
        "v4_comparison": {"roc_auc": v4_roc, "pr_auc": v4_pr, "beats_v4": beats_v4},
        "gate_comparison": gate_comparison,
    }


def next_version() -> str:
    existing = [
        int(p.name[1:]) for p in CANDIDATE_MODEL_DIR.glob("v*")
        if p.is_dir() and p.name[1:].isdigit()
    ]
    return f"v{max(existing, default=0) + 1}"


def save_candidate(built: dict, trained: dict) -> Path:
    version = next_version()
    directory = CANDIDATE_MODEL_DIR / version
    directory.mkdir(parents=True, exist_ok=True)

    trained["model"].save_model(str(directory / "model.cbm"))
    joblib.dump(trained["calibrator"], directory / "calibrator.joblib")

    metadata = {
        "candidate_version": version,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "note": (
            "KANDIDAT EKSPERIMEN - TIDAK PERNAH dibaca predict.py/serving. "
            "docs/EXPERIMENTS.md E-66, docs/DECISIONS.md §12 update."
        ),
        "window": {
            "mtbf_coverage_start": str(MTBF_COVERAGE_START),
            "validation_start": str(built["validation_start"]),
            "test_start": str(built["test_start"]),
            "data_end": str(built["data_end"]),
        },
        "features": built["candidate_columns"],
        "evaluation_metrics": trained["metrics"],
        "v4_comparison": trained["v4_comparison"],
        "gate_comparison_target_0_30": {
            k: v for k, v in (trained["gate_comparison"].get(0.30) or {}).items()
        },
        "gate_comparison_target_0_40": {
            k: v for k, v in (trained["gate_comparison"].get(0.40) or {}).items()
        },
    }
    (directory / "metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    (CANDIDATE_MODEL_DIR / "LATEST").write_text(version, encoding="utf-8")
    return directory


def main() -> int:

    CANDIDATE_MODEL_DIR.mkdir(parents=True, exist_ok=True)
    built = build_dataset()
    trained = train_and_compare(built)
    directory = save_candidate(built, trained)

    print(f"\n[selesai] Disimpan ke {directory} (TIDAK memengaruhi model production).")
    if trained["v4_comparison"]["beats_v4"]:
        print("      >>> Kandidat SUDAH mengalahkan v4 (ROC-AUC dan PR-AUC) di window TEST ini. "
              "Layak dipertimbangkan untuk keputusan promosi manual - lihat docs/DECISIONS.md §12.")
    else:
        print("      Kandidat BELUM mengalahkan v4 di kedua metrik - lanjut pantau, jalankan ulang "
              "perintah ini secara berkala (mis. bulanan) seiring window 2025+ bertambah data.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())