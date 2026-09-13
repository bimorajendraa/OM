from __future__ import annotations

import argparse
import gc
import json
import logging
import statistics
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import psutil
from catboost import CatBoostClassifier, Pool
from sklearn.isotonic import IsotonicRegression

from partrisk.core import config
from partrisk.core import data_reader
from partrisk.core import features as feature_builder
from partrisk.engines import predict
from partrisk.serving import single as serving
from partrisk.serving import batch as serving_batch
from partrisk.engines.failure import train as training_failure
from partrisk.engines.failure import gate
from partrisk.predictive import model_store
from partrisk.predictive import scoring as predictive_scoring

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


_pipeline_logger = logging.getLogger("pipeline")


def _pipeline_main() -> int:
    started = time.time()
    _pipeline_logger.info("pipeline started")

    try:
        cycles = data_reader.get_cycles()
        events = data_reader.get_events()
        episodes = data_reader.get_failure_episodes()
        _pipeline_logger.info("database connected")
        _pipeline_logger.info(
            "rows extracted: %d siklus, %d event, %d kerusakan",
            len(cycles), len(events), len(episodes),
        )

        observations = feature_builder.current_observations(cycles, events)
        _pipeline_logger.info("rows transformed: %d PART aktif", len(observations))

        observations = feature_builder.attach_history(observations, events)
        observations = feature_builder.attach_fleet(observations, cycles, episodes)
        _pipeline_logger.info("features generated: %d kolom", len(observations.columns))
    except Exception:
        _pipeline_logger.exception("error saat menjalankan pipeline")
        return 1

    _pipeline_logger.info("pipeline selesai dalam %.1f detik", time.time() - started)
    return 0


_score_persist_logger = logging.getLogger("score_and_persist")


def _score_and_persist_main() -> int:
    started = time.time()
    try:
        result = predictive_scoring.run_and_persist()
    except Exception:
        _score_persist_logger.exception("score-and-persist gagal")
        return 1
    _score_persist_logger.info(
        "run_id=%s model_version=%s row_count=%d alert_baru=%d selesai dalam %.1f detik",
        result["run_id"], result["model_version"], result["row_count"],
        len(result["alert_flagged_prediction_ids"]), time.time() - started,
    )
    return 0


_resolve_closed_alerts_logger = logging.getLogger("resolve_closed_alerts")


def _resolve_closed_alerts_main() -> int:
    from partrisk.predictive import alerts as alert_engine

    started = time.time()
    try:
        resolved_ids = alert_engine.auto_resolve_closed_cycles()
    except Exception:
        _resolve_closed_alerts_logger.exception("resolve-closed-alerts gagal")
        return 1
    _resolve_closed_alerts_logger.info(
        "alert_resolved=%d selesai dalam %.1f detik: %s",
        len(resolved_ids), time.time() - started, resolved_ids,
    )
    return 0


_raw_data_cache_logger = logging.getLogger("refresh-raw-data-cache")


def _refresh_raw_data_cache_main() -> int:
    from partrisk.predictive import raw_data_cache

    started = time.time()
    try:
        counts = raw_data_cache.refresh()
    except Exception:
        _raw_data_cache_logger.exception("refresh-raw-data-cache gagal")
        return 1
    _raw_data_cache_logger.info(
        "events=%d cycles=%d episodes=%d selesai dalam %.1f detik",
        counts["events"], counts["cycles"], counts["episodes"], time.time() - started,
    )
    return 0


_import_model_artifacts_logger = logging.getLogger("import-model-artifacts")


def _import_model_artifacts_main() -> int:
    """Migrasi satu kali models/failure/v*/ -> predictive.model_artifact."""
    try:
        existing = set(model_store.list_versions())
        candidates = sorted(
            (
                path for path in config.FAILURE_MODEL_DIR.glob("v*")
                if path.is_dir() and (path / "metadata.json").exists()
            ),
            key=lambda path: int(path.name[1:]),
        )
        if not candidates:
            _import_model_artifacts_logger.info("tidak ada versi di %s untuk dimigrasikan", config.FAILURE_MODEL_DIR)
            return 0

        imported = 0
        for directory in candidates:
            version = directory.name
            if version in existing:
                _import_model_artifacts_logger.info("%s sudah ada di database, dilewati", version)
                continue

            model = CatBoostClassifier()
            model.load_model(str(directory / "model.cbm"))
            calibrator = joblib.load(directory / "calibrator.joblib")
            fleet = pd.read_csv(directory / "fleet_snapshot.csv", dtype={"item_model_code_clean": str})
            metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))

            model_store.save_version(version, model, calibrator, fleet, metadata)
            imported += 1
            _import_model_artifacts_logger.info("%s dimigrasikan ke database", version)

        pointer = config.FAILURE_MODEL_DIR / "CURRENT"
        if pointer.exists():
            current = pointer.read_text(encoding="utf-8").strip()
            model_store.set_current_version(current)
            _import_model_artifacts_logger.info("is_current diset ke %s", current)

        _import_model_artifacts_logger.info(
            "selesai: %d versi baru dimigrasikan, %d sudah ada sebelumnya", imported, len(candidates) - imported
        )
    except Exception:
        _import_model_artifacts_logger.exception("import-model-artifacts gagal")
        return 1
    return 0


_predict_logger = logging.getLogger("prediction")


def _predict_main(args: argparse.Namespace) -> int:
    started = time.time()
    _predict_logger.info("prediction started")

    try:
        _predict_logger.info("model loaded: %s", serving.versions())
        scores = serving_batch.score_active_parts()
    except Exception:
        _predict_logger.exception("error saat batch prediction")
        return 1

    frame = scores.frame
    _predict_logger.info(
        "prediction completed: %d PART, %d HIGH, %d MEDIUM (%.1f detik)",
        len(frame),
        int(frame["failure_risk_level"].eq("HIGH").sum()),
        int(frame["failure_risk_level"].eq("MEDIUM").sum()),
        time.time() - started,
    )

    columns = [
        "rank", "item_id", "item_type", "failure_risk_level",
        "failure_probability_30d", "priority", "recommended_action",
    ]
    print(frame[columns].head(args.top).to_string(index=False))

    if args.output:
        frame.to_csv(args.output, index=False)
        _predict_logger.info("hasil lengkap disimpan ke %s", args.output)

    return 0


_VOLATILE_COLUMNS = {"rank"}


def _load_batch():
    return serving_batch.score_active_parts(force_refresh=True)


def generate(out_path: Path) -> None:
    print("[1/2] Menjalankan batch_predictor.score_active_parts(force_refresh=True)...")
    t0 = time.time()
    batch = _load_batch()
    print(f"      selesai dalam {time.time()-t0:.1f} detik - {len(batch.frame):,} PART aktif")

    print(f"[2/2] Menyimpan ke {out_path}...")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    frame = batch.frame.copy()
    frame.attrs.clear()
    snapshot = batch.snapshot.reset_index().rename(columns={"index": "item_id"})

    frame.insert(0, "_table", "frame")
    snapshot.insert(0, "_table", "snapshot")
    combined = pd.concat([frame, snapshot], axis=0, ignore_index=True, sort=False)
    combined.to_parquet(out_path, index=False)

    meta_path = out_path.with_suffix(".meta.txt")
    meta_path.write_text(
        f"generated_at={pd.Timestamp.now(tz='UTC').isoformat()}\n"
        f"data_end={batch.data_end}\n"
        f"model_version={batch.model_version}\n"
        f"rows_frame={len(batch.frame)}\n"
        f"rows_snapshot={len(batch.snapshot)}\n",
        encoding="utf-8",
    )
    print(f"      OK - {meta_path}")


def _split(combined: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    frame = combined.loc[combined["_table"] == "frame"].drop(columns=["_table"]).dropna(axis=1, how="all")
    snapshot = combined.loc[combined["_table"] == "snapshot"].drop(columns=["_table"]).dropna(axis=1, how="all")
    return frame.reset_index(drop=True), snapshot.reset_index(drop=True)


def _relevant_columns(
    name: str, cols_a: set[str], cols_b: set[str], columns: set[str] | None,
) -> tuple[set[str] | None, bool]:
    if columns is not None:
        relevant = columns & cols_a & cols_b
        if not relevant:
            print(f"  (tidak ada kolom diminta yang relevan di tabel {name}, dilewati)")
            return None, True
        return relevant, True
    if cols_a != cols_b:
        print(f"  KOLOM BEDA: hanya di A={cols_a-cols_b}  hanya di B={cols_b-cols_a}")
        return None, False
    return cols_a, True


def _align_row_population(
    a_sorted: pd.DataFrame, b_sorted: pd.DataFrame, key: str,
) -> tuple[pd.DataFrame, pd.DataFrame, bool]:
    if list(a_sorted[key]) == list(b_sorted[key]):
        return a_sorted, b_sorted, True

    only_a = set(a_sorted[key]) - set(b_sorted[key])
    only_b = set(b_sorted[key]) - set(a_sorted[key])
    print(f"  POPULASI {key} BEDA: hanya di A={len(only_a)}  hanya di B={len(only_b)}")
    if only_a:
        print(f"    contoh hanya-A: {list(only_a)[:5]}")
    if only_b:
        print(f"    contoh hanya-B: {list(only_b)[:5]}")
    common = sorted(set(a_sorted[key]) & set(b_sorted[key]))
    a_sorted = a_sorted.set_index(key).loc[common].reset_index()
    b_sorted = b_sorted.set_index(key).loc[common].reset_index()
    return a_sorted, b_sorted, False


def _diff_columns(
    a_sorted: pd.DataFrame, b_sorted: pd.DataFrame, key: str, cols: set[str], rtol: float,
) -> bool:
    ok = True
    for col in sorted(cols - _VOLATILE_COLUMNS):
        sa, sb = a_sorted[col], b_sorted[col]
        if pd.api.types.is_numeric_dtype(sa) and pd.api.types.is_numeric_dtype(sb):
            diff_mask = ~np.isclose(
                sa.to_numpy(dtype=float), sb.to_numpy(dtype=float), rtol=rtol, equal_nan=True
            )
        else:
            diff_mask = (sa.astype(str) != sb.astype(str)).to_numpy()
        n_diff = int(diff_mask.sum())
        if n_diff:
            ok = False
            idx = np.flatnonzero(diff_mask)[:5]
            sample = [(a_sorted[key].iloc[i], sa.iloc[i], sb.iloc[i]) for i in idx]
            print(f"  KOLOM '{col}': {n_diff}/{len(a_sorted):,} baris beda. Contoh (id, A, B): {sample}")
    return ok


def compare(path_a: Path, path_b: Path, *, rtol: float = 1e-9, columns: set[str] | None = None) -> bool:
    frame_a, snap_a = _split(pd.read_parquet(path_a))
    frame_b, snap_b = _split(pd.read_parquet(path_b))

    ok = True
    for name, a, b, key in (("frame", frame_a, frame_b, "item_id"), ("snapshot", snap_a, snap_b, "item_id")):
        print(f"\n--- {name}: {path_a.name} ({len(a):,} baris) vs {path_b.name} ({len(b):,} baris) ---")
        cols, cols_ok = _relevant_columns(name, set(a.columns), set(b.columns), columns)
        ok = ok and cols_ok
        if cols is None:
            continue

        a_sorted = a.sort_values(key).reset_index(drop=True)
        b_sorted = b.sort_values(key).reset_index(drop=True)
        a_sorted, b_sorted, rows_ok = _align_row_population(a_sorted, b_sorted, key)
        ok = ok and rows_ok

        ok = _diff_columns(a_sorted, b_sorted, key, cols, rtol) and ok

    print(f"\n{'=== IDENTIK ===' if ok else '=== ADA PERBEDAAN - lihat di atas ==='}")
    return ok


def _golden_batch_main(args: argparse.Namespace) -> int:
    if args.golden_batch_command == "generate":
        generate(args.out)
        return 0
    if args.golden_batch_command == "compare":
        return 0 if compare(args.path_a, args.path_b) else 1
    return 1


def _rss_mb() -> float:
    return psutil.Process().memory_info().rss / 1e6


def _baseline_performance_main() -> int:
    from partrisk.engines import predict as failure_model

    print(f"RSS sebelum apa pun dimuat: {_rss_mb():.1f} MB")

    print("\n[1/4] Cold model load...")
    t0 = time.time()
    _, _, metadata = failure_model.load_failure_model()
    cold_load_s = time.time() - t0
    rss_after_load = _rss_mb()
    print(f"      cold load: {cold_load_s:.3f} detik")
    print(f"      model_version: {metadata['model_version']}")
    print(f"      RSS setelah load model: {rss_after_load:.1f} MB")

    print("\n[2/4] Ukuran artifact model failure...")
    total_bytes = model_store.artifact_size_bytes(metadata["model_version"])
    print(f"      TOTAL: {total_bytes / 1e6:.3f} MB")

    print("\n[3/4] Single predict() p50 (20 PART aktif)...")
    cycles = data_reader.get_cycles()
    active = cycles.loc[
        cycles["cycle_end_reason"].eq("RIGHT_CENSORED_AT_DATA_END")
        & cycles["is_initial_model_cohort"].fillna(False)
    ]
    sample_items = active["item_identifier_clean"].drop_duplicates().head(20).tolist()
    if not sample_items:
        print("      GAGAL: tidak ada PART aktif untuk diukur latensinya.")
        return 1
    if len(sample_items) < 20:
        print(f"      PERINGATAN: hanya {len(sample_items)} PART aktif ditemukan, bukan 20")

    failure_model.predict(sample_items[0])

    latencies = []
    for item_id in sample_items:
        t0 = time.time()
        failure_model.predict(item_id)
        latencies.append(time.time() - t0)
    latencies.sort()
    p50 = statistics.median(latencies)
    p90 = latencies[int(len(latencies) * 0.9)] if len(latencies) > 1 else latencies[0]
    print(f"      p50={p50*1000:.1f} ms  p90={p90*1000:.1f} ms  min={min(latencies)*1000:.1f} ms  max={max(latencies)*1000:.1f} ms")

    print("\n[4/4] Batch penuh (seluruh PART aktif)...")
    gc.collect()
    rss_before_batch = _rss_mb()

    t0 = time.time()
    batch = serving_batch.score_active_parts(force_refresh=True)
    batch_s = time.time() - t0
    rss_after_batch = _rss_mb()
    print(f"      {len(batch.frame):,} PART, {batch_s:.1f} detik")
    print(f"      RSS sebelum batch: {rss_before_batch:.1f} MB  sesudah: {rss_after_batch:.1f} MB  (+{rss_after_batch-rss_before_batch:.1f} MB)")

    report = f"""# Baseline performa CatBoost (v2) - SEBELUM restrukturisasi

Diukur {time.strftime('%Y-%m-%d %H:%M:%S')}. Ambang gerbang G5/G6 (Fase A) dihitung dari angka ini.

| Metrik | Nilai |
|---|---|
| model_version | {metadata['model_version']} |
| Ukuran artifact model failure (semua file) | {total_bytes/1e6:.3f} MB |
| Cold model load | {cold_load_s:.3f} s |
| RSS setelah load model | {rss_after_load:.1f} MB |
| Single predict() p50 (20 PART) | {p50*1000:.1f} ms |
| Single predict() p90 (20 PART) | {p90*1000:.1f} ms |
| Batch penuh ({len(batch.frame):,} PART) | {batch_s:.1f} s |
| RSS naik setelah batch penuh | {rss_after_batch-rss_before_batch:.1f} MB |

## Ambang turunan untuk gerbang Fase A

- **G5 (ukuran artifact)**: target keras <=100 MB (baseline CatBoost {total_bytes/1e6:.3f} MB - target ini BUKAN "boleh sebesar CatBoost x N", tapi batas keras production terlepas dari baseline, sesuai plan).
- **G6 (latency)**: cold load <=5s; single predict p50 <= {p50*1.5*1000:.1f} ms (1.5x baseline); batch penuh <= {batch_s*2:.1f}s (2x baseline).
"""
    print("\n" + report)
    return 0


def _capacity_table(
    contenders: dict[str, np.ndarray | None], target: np.ndarray, window_days: float
) -> dict[str, dict | None]:
    base_rate = float(target.mean())
    table: dict[str, dict | None] = {}
    for name, raw in contenders.items():
        if raw is None:
            table[name] = None
            continue
        metrics = training_failure.capacity_metrics(
            raw, target, window_days, config.FAILURE_CAPACITY_PER_MONTH
        )
        metrics["lift_vs_random"] = (
            metrics["precision_at_capacity"] / base_rate if base_rate else float("nan")
        )
        table[name] = metrics
    return table


def _baseline_comparison_main() -> int:
    print("[1/3] Menyusun dataset TEST (sama seperti training_failure.build_dataset)...")
    dataset, _, _, _, _, _, _ = (
        training_failure.build_dataset()
    )
    test_dataset = dataset.loc[dataset["split"].eq(training_failure.TEST)].reset_index(drop=True)
    target = test_dataset["target_failure"].astype(bool).to_numpy()
    test_observed = pd.to_datetime(test_dataset["observation_on"])
    window_days = float((test_observed.max() - test_observed.min()).days)
    print(
        f"      TEST: {len(test_dataset):,} baris, {int(target.sum()):,} kerusakan, "
        f"window={window_days:.0f} hari"
    )

    print("[2/3] Skor model production (dukungan BEKU dari metadata - sama seperti predict.py)...")
    model, _, metadata = predict.load_failure_model()
    support = feature_builder.part_model_support(test_dataset, metadata["part_model_support"])
    candidate_features = feature_builder.build_features(test_dataset, support)[metadata["features"]]
    candidate_raw = model.predict_proba(candidate_features)[:, 1]

    print("[3/3] Skor kebijakan baseline (tanpa model)...")
    model_label = f"Model production ({metadata['model_version']})"
    contenders: dict[str, np.ndarray | None] = {
        model_label: candidate_raw,
        "PART tertua dulu": test_dataset["days_since_installation"].to_numpy(dtype=float),

        "Corrective terbanyak 90 hari dulu": test_dataset["log_prior_corrective_90d"].to_numpy(dtype=float),
        "Urutan aktual tim (kalau terekam)": None,
    }
    table = _capacity_table(contenders, target, window_days)

    header = f"\n{'Kebijakan':<38}{'kapasitas':>11}{'precision@cap':>15}{'recall@cap':>12}{'lift vs acak':>14}"
    print(header)
    for name, metrics in table.items():
        if metrics is None:
            print(f"{name:<38}{'tidak terekam - lihat CLAUDE.md bagian 11.2':>52}")
            continue
        print(
            f"{name:<38}{metrics['capacity_evaluated']:>11,}"
            f"{metrics['precision_at_capacity']:>15.4f}{metrics['recall_at_capacity']:>12.4f}"
            f"{metrics['lift_vs_random']:>13.2f}x"
        )

    stored = metadata.get("promotion_comparison", {}).get("candidate") or {}
    recomputed = table[model_label]
    if stored and recomputed:
        stored_precision = stored.get("precision_at_capacity")
        diff = abs((stored_precision if stored_precision is not None else -1) - recomputed["precision_at_capacity"])
        match = "COCOK" if diff < 1e-6 else f"BEDA (selisih {diff:.6f})"
        stored_display = f"{stored_precision:.4f}" if stored_precision is not None else "tidak ada"
        print(
            f"\nSanity check vs metadata.json promotion_comparison.candidate: "
            f"precision@cap tersimpan={stored_display} "
            f"vs dihitung ulang di sini={recomputed['precision_at_capacity']:.4f} ({match})"
        )
    return 0


_ROLLING_BACKTEST_FOLDS = 6
_ROLLING_BACKTEST_STEP_DAYS = 60
_ROLLING_BACKTEST_VALIDATION_DAYS = 365


def _rolling_fold_windows(data_end: pd.Timestamp) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    windows = []
    end = data_end
    for _ in range(_ROLLING_BACKTEST_FOLDS):
        start = end - np.timedelta64(_ROLLING_BACKTEST_STEP_DAYS, "D")
        windows.append((start, end))
        end = start
    return list(reversed(windows))


def _assign_rolling_split(
    observations: pd.DataFrame, test_start: pd.Timestamp, test_end: pd.Timestamp
) -> pd.Series:

    observed = pd.to_datetime(observations["observation_on"])
    resolved = observed + np.timedelta64(config.TARGET_HORIZON_DAYS, "D")
    validation_start = test_start - np.timedelta64(_ROLLING_BACKTEST_VALIDATION_DAYS, "D")

    split = pd.Series("EXCLUDED", index=observations.index)
    split[
        (observed >= pd.Timestamp(config.MIN_OBSERVATION_DATE))
        & (resolved < validation_start)
    ] = training_failure.TRAIN
    split[(observed >= validation_start) & (resolved < test_start)] = training_failure.VALIDATION
    split[(observed >= test_start) & (observed < test_end)] = training_failure.TEST
    return split


def _fit_and_evaluate_fold(
    dataset: pd.DataFrame, features: pd.DataFrame, feature_columns: list[str], window_days: float
) -> dict:
    parts = {
        name: dataset["split"].eq(name).to_numpy()
        for name in (training_failure.TRAIN, training_failure.VALIDATION, training_failure.TEST)
    }
    target = dataset["target_failure"].astype(bool)
    train_x = features.loc[parts[training_failure.TRAIN], feature_columns]
    train_y = target[parts[training_failure.TRAIN]]
    val_x = features.loc[parts[training_failure.VALIDATION], feature_columns]
    val_y = target[parts[training_failure.VALIDATION]]
    test_x = features.loc[parts[training_failure.TEST], feature_columns]
    test_y = target[parts[training_failure.TEST]]

    model = CatBoostClassifier(random_seed=config.RANDOM_STATE, **config.CATBOOST_PARAMS)
    model.fit(
        Pool(train_x, train_y, cat_features=config.CATEGORICAL_FEATURES),
        eval_set=Pool(val_x, val_y, cat_features=config.CATEGORICAL_FEATURES),
    )
    raw_test = model.predict_proba(test_x)[:, 1]
    calibrator = IsotonicRegression(out_of_bounds="clip")
    calibrator.fit(model.predict_proba(val_x)[:, 1], val_y.astype(int))
    calibrated_test = calibrator.predict(raw_test)

    return training_failure.full_metrics(
        raw_test, calibrated_test, test_y.to_numpy(), window_days, config.FAILURE_CAPACITY_PER_MONTH
    )


def _run_rolling_folds(
    dataset: pd.DataFrame, features: pd.DataFrame,
    windows: list[tuple[pd.Timestamp, pd.Timestamp]], variants: dict[str, list[str]],
) -> dict[str, list[dict]]:
    results: dict[str, list[dict]] = {name: [] for name in variants}
    for i, (test_start, test_end) in enumerate(windows, start=1):
        dataset["split"] = _assign_rolling_split(dataset, test_start, test_end)
        window_days = float((test_end - test_start).days)
        test_mask = dataset["split"].eq(training_failure.TEST)
        n_test, n_pos = int(test_mask.sum()), int(dataset.loc[test_mask, "target_failure"].sum())
        print(
            f"      Fold {i}: TEST [{test_start.date()}, {test_end.date()}) - "
            f"{n_test:,} baris, {n_pos:,} kerusakan"
        )
        for name, feature_columns in variants.items():
            metrics = _fit_and_evaluate_fold(dataset, features, feature_columns, window_days)
            results[name].append(metrics)
            print(
                f"         {name:<26} ROC-AUC={metrics['roc_auc']:.4f} PR-AUC={metrics['pr_auc']:.4f} "
                f"Precision@cap={metrics['precision_at_capacity']:.4f} "
                f"Recall@cap={metrics['recall_at_capacity']:.4f}"
            )
    return results


def _print_rolling_summary(results: dict[str, list[dict]], summary_keys: list[str]) -> None:
    for name, fold_results in results.items():
        print(f"\n  {name}")
        for key in summary_keys:
            values = [r[key] for r in fold_results]
            mean = statistics.mean(values)
            sd = statistics.stdev(values) if len(values) > 1 else 0.0
            print(f"      {key:<24} {mean:.4f} +/- {sd:.4f}")


def _rolling_pairwise_verdict(mean_diff: float, sd_diff: float) -> str:
    if sd_diff == 0:
        return "sd=0, tidak bisa dinilai"
    if mean_diff > sd_diff:
        return "v4 > v3 (melebihi 1 sd)"
    if -mean_diff > sd_diff:
        return "v3 > v4 (melebihi 1 sd)"
    return "TIDAK signifikan (dalam 1 sd) - jangan klaim mana yang lebih baik"


def _print_rolling_pairwise_comparison(
    results: dict[str, list[dict]], v3_name: str, v4_name: str, summary_keys: list[str],
) -> None:
    print(f"\n  Perbandingan berpasangan per-fold ({v4_name} - {v3_name}):")
    print("  (klaim 'A > B' hanya kalau selisih rata-rata melebihi 1 sd selisih per-fold)")
    for key in summary_keys:
        diffs = [b[key] - a[key] for a, b in zip(results[v3_name], results[v4_name])]
        mean_diff = statistics.mean(diffs)
        sd_diff = statistics.stdev(diffs) if len(diffs) > 1 else 0.0
        verdict = _rolling_pairwise_verdict(mean_diff, sd_diff)
        print(f"      {key:<24} selisih={mean_diff:+.4f} +/- {sd_diff:.4f}   -> {verdict}")


def _rolling_backtest_main() -> int:
    print("[1/3] Menyusun dataset (sekali, dipakai ulang untuk semua fold)...")
    dataset, features, _, data_end, _, _, _ = (
        training_failure.build_dataset()
    )

    _, _, v3_metadata = model_store.load_version("v3")
    _, _, v4_metadata = model_store.load_version("v4")
    v3_name = f"v3 ({len(v3_metadata['features'])} fitur)"
    v4_name = f"v4 ({len(v4_metadata['features'])} fitur)"
    variants = {v3_name: v3_metadata["features"], v4_name: v4_metadata["features"]}

    windows = _rolling_fold_windows(data_end)
    print(
        f"[2/3] {len(windows)} fold, window {_ROLLING_BACKTEST_STEP_DAYS} hari masing-masing, "
        f"validasi {_ROLLING_BACKTEST_VALIDATION_DAYS} hari sebelum tiap fold, "
        f"embargo {config.TARGET_HORIZON_DAYS} hari (sama seperti training production)..."
    )

    results = _run_rolling_folds(dataset, features, windows, variants)

    print("\n[3/3] Ringkasan mean +/- sd lintas fold...")
    summary_keys = ["roc_auc", "pr_auc", "brier_calibrated", "precision_at_capacity", "recall_at_capacity"]
    _print_rolling_summary(results, summary_keys)
    _print_rolling_pairwise_comparison(results, v3_name, v4_name, summary_keys)
    return 0


_ROLLING_LIFECYCLE_TARGETS = (0.30, 0.40, 0.85)


def _fit_and_evaluate_fold_lifecycle(
    dataset: pd.DataFrame, features: pd.DataFrame, feature_columns: list[str],
    target_precisions: tuple[float, ...] = _ROLLING_LIFECYCLE_TARGETS,
) -> dict[float, dict]:
    parts = {
        name: dataset["split"].eq(name).to_numpy()
        for name in (training_failure.TRAIN, training_failure.VALIDATION, training_failure.TEST)
    }
    target = dataset["target_failure"].astype(bool)
    train_x = features.loc[parts[training_failure.TRAIN], feature_columns]
    train_y = target[parts[training_failure.TRAIN]]
    val_x = features.loc[parts[training_failure.VALIDATION], feature_columns]
    val_y = target[parts[training_failure.VALIDATION]]
    test_x = features.loc[parts[training_failure.TEST], feature_columns]

    model = CatBoostClassifier(random_seed=config.RANDOM_STATE, **config.CATBOOST_PARAMS)
    model.fit(
        Pool(train_x, train_y, cat_features=config.CATEGORICAL_FEATURES),
        eval_set=Pool(val_x, val_y, cat_features=config.CATEGORICAL_FEATURES),
    )
    calibrator = IsotonicRegression(out_of_bounds="clip")
    calibrator.fit(model.predict_proba(val_x)[:, 1], val_y.astype(int))
    val_calibrated = calibrator.predict(model.predict_proba(val_x)[:, 1])
    test_calibrated = calibrator.predict(model.predict_proba(test_x)[:, 1])

    val_dataset = dataset.loc[parts[training_failure.VALIDATION]]
    test_dataset = dataset.loc[parts[training_failure.TEST]]

    results: dict[float, dict] = {}
    for target_precision in target_precisions:
        selection = gate.select_lifecycle_threshold(val_dataset, val_calibrated, target_precision=target_precision)
        if not selection["feasible"]:
            results[target_precision] = {"feasible": False, "best_precision_achievable": selection["best_precision_achievable"]}
            continue
        results[target_precision] = {
            "feasible": True, **gate.lifecycle_metrics(test_dataset, test_calibrated, selection["threshold"])
        }
    return results


def _rolling_lifecycle_backtest_main() -> int:
    print("[1/3] Menyusun dataset (sekali, dipakai ulang semua fold)...")
    dataset, features, _, data_end, _, _, _ = (
        training_failure.build_dataset()
    )
    feature_columns = config.FEATURE_COLUMNS

    windows = _rolling_fold_windows(data_end)
    print(f"[2/3] {len(windows)} fold, window {_ROLLING_BACKTEST_STEP_DAYS} hari, "
          f"target presisi {_ROLLING_LIFECYCLE_TARGETS}...")

    fold_results: list[dict[float, dict]] = []
    for i, (test_start, test_end) in enumerate(windows, start=1):
        dataset["split"] = _assign_rolling_split(dataset, test_start, test_end)
        test_mask = dataset["split"].eq(training_failure.TEST)
        n_test, n_pos = int(test_mask.sum()), int(dataset.loc[test_mask, "target_failure"].sum())
        print(f"\n  Fold {i}: TEST [{test_start.date()}, {test_end.date()}) - {n_test:,} baris, {n_pos:,} kerusakan")

        result = _fit_and_evaluate_fold_lifecycle(dataset, features, feature_columns)
        fold_results.append(result)
        for target_precision in _ROLLING_LIFECYCLE_TARGETS:
            r = result[target_precision]
            if r["feasible"]:
                print(f"    target={target_precision:.2f}  presisi={r['precision']:.4f} "
                      f"recall={r['recall']:.4f} alert={r['promoted_cycles']}")
            else:
                print(f"    target={target_precision:.2f}  INFEASIBLE (maks VALIDATION={r['best_precision_achievable']:.4f})")

    print("\n[3/3] Ringkasan mean +/- sd lintas fold (fold feasible saja)...")
    for target_precision in _ROLLING_LIFECYCLE_TARGETS:
        precisions = [r[target_precision]["precision"] for r in fold_results if r[target_precision]["feasible"]]
        recalls = [r[target_precision]["recall"] for r in fold_results if r[target_precision]["feasible"]]
        n_feasible = len(precisions)
        print(f"\n  target={target_precision:.2f}: {n_feasible}/{len(windows)} fold feasible")
        if n_feasible == 0:
            continue
        p_mean = statistics.mean(precisions)
        p_sd = statistics.stdev(precisions) if n_feasible > 1 else 0.0
        r_mean = statistics.mean(recalls)
        r_sd = statistics.stdev(recalls) if n_feasible > 1 else 0.0
        print(f"      presisi={p_mean:.4f} +/- {p_sd:.4f}   recall={r_mean:.4f} +/- {r_sd:.4f}")

    return 0


_BOOTSTRAP_N = 1000
_BOOTSTRAP_SEED = 42


def _bootstrap_classification_ci(
    raw: np.ndarray, calibrated: np.ndarray, target: np.ndarray,
    window_days: float, capacity_per_month: float, cluster_ids: np.ndarray,
    days_per_month: float = 30.0,
) -> dict:
    """Bootstrap sadar-klaster (resample cluster_ids) - docs/DECISIONS.md §51."""
    rng = np.random.default_rng(_BOOTSTRAP_SEED)
    cluster_ids = np.asarray(cluster_ids)
    unique_clusters = np.unique(cluster_ids)
    n_clusters = len(unique_clusters)
    rows_by_cluster = {
        cluster: np.flatnonzero(cluster_ids == cluster) for cluster in unique_clusters
    }

    keys = ("roc_auc", "pr_auc", "precision_at_capacity", "recall_at_capacity")
    samples: dict[str, list[float]] = {key: [] for key in keys}
    for _ in range(_BOOTSTRAP_N):
        sampled_clusters = rng.choice(unique_clusters, size=n_clusters, replace=True)
        idx = np.concatenate([rows_by_cluster[cluster] for cluster in sampled_clusters])
        try:
            metrics = training_failure.full_metrics(
                raw[idx], calibrated[idx], target[idx], window_days, capacity_per_month, days_per_month,
            )
        except ValueError:

            continue
        for key in keys:
            samples[key].append(metrics[key])

    result: dict = {"n_boot": _BOOTSTRAP_N, "n_boot_valid": len(samples["pr_auc"])}
    for key in keys:
        values = np.asarray(samples[key])
        result[key] = (
            [round(float(np.percentile(values, 2.5)), 4), round(float(np.percentile(values, 97.5)), 4)]
            if len(values) else [None, None]
        )
    return result


def _bootstrap_ci_failure() -> dict:
    print("[failure] Menyusun TEST dan skor v4 (dukungan beku, sama seperti predict.py)...")
    dataset, _features, _support_totals, _data_end, _events, _cycles, _episodes = (
        training_failure.build_dataset()
    )
    test_dataset = dataset.loc[dataset["split"].eq(training_failure.TEST)].reset_index(drop=True)
    target = test_dataset["target_failure"].astype(bool).to_numpy()
    test_observed = pd.to_datetime(test_dataset["observation_on"])
    window_days = float((test_observed.max() - test_observed.min()).days)

    model, calibrator, metadata = predict.load_failure_model()
    support = feature_builder.part_model_support(test_dataset, metadata["part_model_support"])
    candidate_features = feature_builder.build_features(test_dataset, support)[metadata["features"]]
    raw = model.predict_proba(candidate_features)[:, 1]
    calibrated = calibrator.predict(raw)

    print(
        f"      TEST: {len(test_dataset):,} baris, {int(target.sum()):,} kerusakan - "
        f"bootstrap {_BOOTSTRAP_N}x..."
    )
    cluster_ids = test_dataset["installation_cycle_id"].to_numpy()
    ci = _bootstrap_classification_ci(
        raw, calibrated, target, window_days, config.FAILURE_CAPACITY_PER_MONTH, cluster_ids,
    )
    for key in ("roc_auc", "pr_auc", "precision_at_capacity", "recall_at_capacity"):
        print(f"      {key:<24} CI95=[{ci[key][0]}, {ci[key][1]}]")

    # Perkakas riset - tidak menyentuh metadata.json produksi (docs/DECISIONS.md §50).
    analysis_dir = config.FAILURE_MODEL_DIR / metadata["model_version"] / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    path = analysis_dir / "bootstrap_ci.json"
    document = {
        "model_version": metadata["model_version"],
        "computed_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "n_boot": _BOOTSTRAP_N,
        "bootstrap_ci_95": ci,
    }
    training_failure.atomic_write_text(path, json.dumps(document, indent=2, ensure_ascii=False))
    print(f"      Disimpan ke {path}")
    return ci


def _bootstrap_ci_main() -> int:
    """CI bootstrap 1000-resample, ditulis ke analysis/ - docs/DECISIONS.md §50."""
    _bootstrap_ci_failure()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="partrisk.cli", description="Entry point manual partrisk (dulu scripts/*.py terpisah)."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("pipeline", help="Uji jalur database -> fitur end-to-end.")

    p_predict = sub.add_parser(
        "predict",
        help="Batch prediction untuk seluruh PART aktif.",
        description="Batch prediction untuk seluruh PART aktif (cetak ke terminal, opsional simpan CSV).",
    )
    p_predict.add_argument("--output", help="Simpan seluruh hasil ke file CSV (opsional).")
    p_predict.add_argument("--top", type=int, default=10, help="Berapa baris teratas dicetak.")

    sub.add_parser(
        "score-and-persist",
        help="Milestone 2: skor seluruh PART aktif dan simpan sebagai model_run + "
        "item_prediction baru di schema predictive. Dipanggil scheduler eksternal berkala.",
    )

    sub.add_parser(
        "resolve-closed-alerts",
        help="Tutup alert OPEN yang cycle-nya sudah tertutup di data operasional, "
        "tanpa skor ulang seluruh armada. Ringan - bisa dijadwalkan lebih sering "
        "(mis. harian) daripada score-and-persist (docs/DECISIONS.md §34).",
    )

    sub.add_parser(
        "refresh-raw-data-cache",
        help="Tarik events/cycles/episodes dari database operasional dan "
        "simpan ke predictive.raw_*_cache (TRUNCATE + tulis ulang semua) - "
        "dipakai training lewat build_dataset(use_raw_cache=True), supaya "
        "eksperimen berulang tidak menembak database operasional tiap kali. "
        "Dijadwalkan TERPISAH dari jadwal training resmi (lihat "
        "predictive/raw_data_cache.py).",
    )

    sub.add_parser(
        "import-model-artifacts",
        help="Migrasi satu kali: pindahkan semua versi model kerusakan dari "
        "models/failure/v*/ ke predictive.model_artifact.",
    )

    p_golden = sub.add_parser(
        "golden-batch",
        help="Oracle golden batch (generate/compare).",
        description="Golden batch oracle - bandingkan output batch scoring sebelum/sesudah perubahan.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    golden_sub = p_golden.add_subparsers(dest="golden_batch_command", required=True)
    p_gen = golden_sub.add_parser("generate")
    p_gen.add_argument("--out", type=Path, required=True)
    p_cmp = golden_sub.add_parser("compare")
    p_cmp.add_argument("path_a", type=Path)
    p_cmp.add_argument("path_b", type=Path)

    sub.add_parser("baseline-performance", help="Ukur RSS/latency model failure.")
    sub.add_parser(
        "baseline-comparison",
        help="Bandingkan precision@kapasitas model vs kebijakan urutan kerja tanpa model.",
    )
    sub.add_parser(
        "rolling-backtest",
        help="Backtest temporal bergulir v3 vs v4 (precision@kapasitas, mean +/- sd).",
    )
    sub.add_parser(
        "rolling-lifecycle-backtest",
        help="Fase 8: stabilitas model failure production antar-periode, evaluasi lifecycle-based (E-49). Wajib sebelum klaim kandidat baru.",
    )
    sub.add_parser(
        "bootstrap-ci",
        help="CI bootstrap 1000-resample untuk metrik headline model failure.",
    )

    args = parser.parse_args()

    if args.command == "pipeline":
        return _pipeline_main()
    if args.command == "predict":
        return _predict_main(args)
    if args.command == "score-and-persist":
        return _score_and_persist_main()
    if args.command == "resolve-closed-alerts":
        return _resolve_closed_alerts_main()
    if args.command == "refresh-raw-data-cache":
        return _refresh_raw_data_cache_main()
    if args.command == "import-model-artifacts":
        return _import_model_artifacts_main()
    if args.command == "golden-batch":
        return _golden_batch_main(args)
    if args.command == "baseline-performance":
        return _baseline_performance_main()
    if args.command == "baseline-comparison":
        return _baseline_comparison_main()
    if args.command == "rolling-backtest":
        return _rolling_backtest_main()
    if args.command == "rolling-lifecycle-backtest":
        return _rolling_lifecycle_backtest_main()
    if args.command == "bootstrap-ci":
        return _bootstrap_ci_main()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
