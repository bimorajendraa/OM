from __future__ import annotations

import argparse
import gc
import json
import logging
import statistics
import time
from pathlib import Path

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
        len(result["opened_alert_ids"]), time.time() - started,
    )
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
    print(f"[1/2] Menjalankan batch_predictor.score_active_parts(force_refresh=True)...")
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


def compare(path_a: Path, path_b: Path, *, rtol: float = 1e-9, columns: set[str] | None = None) -> bool:
    """`columns=None` (default): kolom A dan B harus SAMA PERSIS (perilaku lama).

    `columns={...}`: per tabel (frame/snapshot), dibandingkan hanya irisan
    `columns` dengan kolom yang benar-benar ada di tabel itu di KEDUA file -
    tabel yang tidak punya kolom relevan sama sekali dilewati (bukan gagal).
    Dipakai untuk membuktikan angka Q2 tidak berubah lintas refactor yang
    SENGAJA mengubah skema (mis. penghapusan Survival/Scrap), bukan untuk
    pure-move.
    """
    frame_a, snap_a = _split(pd.read_parquet(path_a))
    frame_b, snap_b = _split(pd.read_parquet(path_b))

    ok = True
    for name, a, b, key in (("frame", frame_a, frame_b, "item_id"), ("snapshot", snap_a, snap_b, "item_id")):
        print(f"\n--- {name}: {path_a.name} ({len(a):,} baris) vs {path_b.name} ({len(b):,} baris) ---")
        cols_a, cols_b = set(a.columns), set(b.columns)
        if columns is not None:
            relevant = columns & cols_a & cols_b
            if not relevant:
                print(f"  (tidak ada kolom diminta yang relevan di tabel {name}, dilewati)")
                continue
            cols_a = cols_b = relevant
        elif cols_a != cols_b:
            print(f"  KOLOM BEDA: hanya di A={cols_a-cols_b}  hanya di B={cols_b-cols_a}")
            ok = False
            continue

        a_sorted = a.sort_values(key).reset_index(drop=True)
        b_sorted = b.sort_values(key).reset_index(drop=True)
        if list(a_sorted[key]) != list(b_sorted[key]):
            only_a = set(a_sorted[key]) - set(b_sorted[key])
            only_b = set(b_sorted[key]) - set(a_sorted[key])
            print(f"  POPULASI {key} BEDA: hanya di A={len(only_a)}  hanya di B={len(only_b)}")
            if only_a:
                print(f"    contoh hanya-A: {list(only_a)[:5]}")
            if only_b:
                print(f"    contoh hanya-B: {list(only_b)[:5]}")
            ok = False
            common = sorted(set(a_sorted[key]) & set(b_sorted[key]))
            a_sorted = a_sorted.set_index(key).loc[common].reset_index()
            b_sorted = b_sorted.set_index(key).loc[common].reset_index()

        for col in sorted(cols_a - _VOLATILE_COLUMNS):
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
                sample = [
                    (a_sorted[key].iloc[i], sa.iloc[i], sb.iloc[i]) for i in idx
                ]
                print(f"  KOLOM '{col}': {n_diff}/{len(a_sorted):,} baris beda. Contoh (id, A, B): {sample}")

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
    model, calibrator, metadata = failure_model.load_failure_model()
    cold_load_s = time.time() - t0
    rss_after_load = _rss_mb()
    print(f"      cold load: {cold_load_s:.3f} detik")
    print(f"      model_version: {metadata['model_version']}")
    print(f"      RSS setelah load model: {rss_after_load:.1f} MB")

    print("\n[2/4] Ukuran artifact model failure...")
    failure_dir = config.FAILURE_MODEL_DIR / metadata["model_version"]
    total_bytes = sum(f.stat().st_size for f in failure_dir.glob("*") if f.is_file())
    for f in sorted(failure_dir.glob("*")):
        if f.is_file():
            print(f"      {f.name}: {f.stat().st_size / 1e6:.3f} MB")
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
    """FASE 7 P0-6: precision@kapasitas model production dibandingkan
    dengan kebijakan urutan kerja yang bisa berjalan TANPA model sama
    sekali - jawaban paling meyakinkan untuk "lebih baik dari cara kerja
    sekarang?", bukan cuma "lebih baik dari tebakan acak?"."""
    print("[1/3] Menyusun dataset TEST (sama seperti training_failure.build_dataset)...")
    dataset, _features, support_totals, data_end, events, cycles, episodes = (
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
    model, calibrator, metadata = predict.load_failure_model()
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


def _rolling_backtest_main() -> int:
    """FASE 7 P0-1: ganti satu split TEST dengan fold temporal bergulir,
    laporkan mean +/- sd - dan pakai itu untuk menjawab pertanyaan yang
    memicu P0-1: v4 (32 fitur) sungguh lebih baik dari v3 (28 fitur), atau
    itu cuma adaptasi terhadap satu TEST split (VALIDATION PR-AUC turun
    v3->v4 sementara TEST naik saat model dipromosikan)?"""
    print("[1/3] Menyusun dataset (sekali, dipakai ulang untuk semua fold)...")
    dataset, features, support_totals, data_end, events, cycles, episodes = (
        training_failure.build_dataset()
    )

    v3_metadata = json.loads(
        (config.FAILURE_MODEL_DIR / "v3" / "metadata.json").read_text(encoding="utf-8")
    )
    v4_metadata = json.loads(
        (config.FAILURE_MODEL_DIR / "v4" / "metadata.json").read_text(encoding="utf-8")
    )
    v3_name = f"v3 ({len(v3_metadata['features'])} fitur)"
    v4_name = f"v4 ({len(v4_metadata['features'])} fitur)"
    variants = {v3_name: v3_metadata["features"], v4_name: v4_metadata["features"]}

    windows = _rolling_fold_windows(data_end)
    print(
        f"[2/3] {len(windows)} fold, window {_ROLLING_BACKTEST_STEP_DAYS} hari masing-masing, "
        f"validasi {_ROLLING_BACKTEST_VALIDATION_DAYS} hari sebelum tiap fold, "
        f"embargo {config.TARGET_HORIZON_DAYS} hari (sama seperti training production)..."
    )

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

    print("\n[3/3] Ringkasan mean +/- sd lintas fold...")
    summary_keys = ["roc_auc", "pr_auc", "brier_calibrated", "precision_at_capacity", "recall_at_capacity"]
    for name, fold_results in results.items():
        print(f"\n  {name}")
        for key in summary_keys:
            values = [r[key] for r in fold_results]
            mean = statistics.mean(values)
            sd = statistics.stdev(values) if len(values) > 1 else 0.0
            print(f"      {key:<24} {mean:.4f} +/- {sd:.4f}")

    print(f"\n  Perbandingan berpasangan per-fold ({v4_name} - {v3_name}):")
    print("  (klaim 'A > B' hanya kalau selisih rata-rata melebihi 1 sd selisih per-fold)")
    for key in summary_keys:
        diffs = [b[key] - a[key] for a, b in zip(results[v3_name], results[v4_name])]
        mean_diff = statistics.mean(diffs)
        sd_diff = statistics.stdev(diffs) if len(diffs) > 1 else 0.0
        if sd_diff == 0:
            verdict = "sd=0, tidak bisa dinilai"
        elif mean_diff > sd_diff:
            verdict = "v4 > v3 (melebihi 1 sd)"
        elif -mean_diff > sd_diff:
            verdict = "v3 > v4 (melebihi 1 sd)"
        else:
            verdict = "TIDAK signifikan (dalam 1 sd) - jangan klaim mana yang lebih baik"
        print(f"      {key:<24} selisih={mean_diff:+.4f} +/- {sd_diff:.4f}   -> {verdict}")

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
    """FASE 8: stabilitas antar-periode model failure production (fitur
    v4 saat ini, retrain segar tiap fold) diukur lifecycle-based (E-49) -
    presisi/recall/alert per fold dan mean+/-sd, di beberapa target
    presisi. Tidak menyimpan/mempromosikan model apa pun."""
    print("[1/3] Menyusun dataset (sekali, dipakai ulang semua fold)...")
    dataset, features, support_totals, data_end, events, cycles, episodes = (
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


def _update_metadata_json(path: Path, apply) -> dict:
    metadata = json.loads(path.read_text(encoding="utf-8"))
    apply(metadata)
    path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    return metadata


def _bootstrap_classification_ci(
    raw: np.ndarray, calibrated: np.ndarray, target: np.ndarray,
    window_days: float, capacity_per_month: float, days_per_month: float = 30.0,
) -> dict:
    rng = np.random.default_rng(_BOOTSTRAP_SEED)
    n = len(target)
    keys = ("roc_auc", "pr_auc", "precision_at_capacity", "recall_at_capacity")
    samples: dict[str, list[float]] = {key: [] for key in keys}
    for _ in range(_BOOTSTRAP_N):
        idx = rng.integers(0, n, size=n)
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
    ci = _bootstrap_classification_ci(raw, calibrated, target, window_days, config.FAILURE_CAPACITY_PER_MONTH)
    for key in ("roc_auc", "pr_auc", "precision_at_capacity", "recall_at_capacity"):
        print(f"      {key:<24} CI95=[{ci[key][0]}, {ci[key][1]}]")

    path = config.FAILURE_MODEL_DIR / metadata["model_version"] / "metadata.json"

    def _apply(doc: dict) -> None:
        doc["evaluation_metrics"]["test"]["bootstrap_ci_95"] = ci

    _update_metadata_json(path, _apply)
    print(f"      Disimpan ke {path}")
    return ci


def _bootstrap_ci_main() -> int:
    """FASE 7 P0-2: CI bootstrap 1000-resample untuk metrik headline model
    kerusakan. Metadata.json ditulis ulang dengan field bootstrap_ci_95
    baru (field yang dipakai scoring TIDAK disentuh)."""
    _bootstrap_ci_failure()
    return 0


_GATE_HORIZONS_DAYS = (7, 14, 30)
_GATE_TARGET_PRECISION = 0.85


def _gate_candidate_result(
    name: str,
    calibrated_val: np.ndarray,
    target_val: np.ndarray,
    calibrated_test: np.ndarray,
    target_test: np.ndarray,
) -> dict:
    selection = gate.select_precision_constrained_threshold(
        calibrated_val, target_val, _GATE_TARGET_PRECISION
    )
    print(f"      [{name}] VALIDATION: {selection['reason']}")
    result = {"name": name, "validation": selection, "test": None, "reliability": None}
    if not selection["feasible"]:
        return result

    result["test"] = gate.honest_test_evaluation(calibrated_test, target_test, selection["threshold"])
    result["reliability"] = gate.reliability_table(calibrated_val, target_val).to_dict("records")
    print(
        f"      [{name}] TEST (threshold beku {selection['threshold']:.4f}): "
        f"presisi={result['test']['precision']:.4f} recall={result['test']['recall']:.4f} "
        f"alert={result['test']['alerts']}"
    )
    gaps = [abs(row["mean_predicted"] - row["observed_rate"]) for row in result["reliability"]]
    if gaps:
        print(f"      [{name}] kalibrasi VALIDATION: gap rata2={sum(gaps)/len(gaps):.4f} maks={max(gaps):.4f}")
    return result


def _gate_candidate_from_model(name: str, model, calibrator, dataset: pd.DataFrame, features: pd.DataFrame) -> dict:
    val_mask = dataset["split"].eq(training_failure.VALIDATION).to_numpy()
    test_mask = dataset["split"].eq(training_failure.TEST).to_numpy()
    calibrated_val = calibrator.predict(model.predict_proba(features[val_mask])[:, 1])
    calibrated_test = calibrator.predict(model.predict_proba(features[test_mask])[:, 1])
    target_val = dataset.loc[val_mask, "target_failure"].astype(bool).to_numpy()
    target_test = dataset.loc[test_mask, "target_failure"].astype(bool).to_numpy()
    return _gate_candidate_result(name, calibrated_val, target_val, calibrated_test, target_test)


def _precision_gate_experiment_main() -> int:
    """Langkah 1: threshold presisi >= 85% dicari HANYA dari VALIDATION lalu
    diuji SEKALI (jujur) di TEST - untuk baseline production (30 hari,
    TANPA retrain) dan kandidat horizon 7/14/30 hari (retrain baru). Tidak
    ada model yang disimpan ke models/failure/ dari eksperimen ini."""
    production_version = training_failure.current_version(config.FAILURE_MODEL_DIR)
    if production_version is None:
        raise SystemExit("Tidak ada model failure CURRENT.")
    print(f"[baseline] {production_version} production (30 hari, tanpa retrain)...")
    dataset_30, features_30, *_ = training_failure.build_dataset()
    incumbent_val = training_failure.evaluate_incumbent(
        production_version, dataset_30, split=training_failure.VALIDATION
    )
    incumbent_test = training_failure.evaluate_incumbent(
        production_version, dataset_30, split=training_failure.TEST
    )
    results = [
        _gate_candidate_result(
            f"baseline {production_version} (30 hari, tanpa retrain)",
            incumbent_val["calibrated"], incumbent_val["target"],
            incumbent_test["calibrated"], incumbent_test["target"],
        )
    ]

    for horizon_days in _GATE_HORIZONS_DAYS:
        print(f"\n[horizon {horizon_days}d] Menyusun dataset & melatih kandidat...")
        if horizon_days == config.TARGET_HORIZON_DAYS:
            dataset, features = dataset_30, features_30
        else:
            dataset, features, *_ = training_failure.build_dataset(horizon_days=horizon_days)
        model, calibrator, _metrics, _raw_test = training_failure.train_model(dataset, features)
        results.append(
            _gate_candidate_from_model(f"horizon {horizon_days} hari (retrain)", model, calibrator, dataset, features)
        )

    print(f"\n[ringkasan - target presisi >= {_GATE_TARGET_PRECISION:.0%}]")
    for result in results:
        val = result["validation"]
        if not val["feasible"]:
            print(
                f"      {result['name']:<32} INFEASIBLE "
                f"(presisi maks VALIDATION={val['best_precision_achievable']:.4f})"
            )
            continue
        test = result["test"]
        print(
            f"      {result['name']:<32} threshold={val['threshold']:.4f} | "
            f"TEST presisi={test['precision']:.4f} recall={test['recall']:.4f} alert={test['alerts']}"
        )
    return 0


def _attach_gate_main() -> int:
    """Tempel blok `gate` (docs/EXPERIMENTS.md E-46/E-47/E-48) ke
    metadata.json versi CURRENT model failure - dipakai SEKALI untuk
    artifact yang dilatih sebelum fitur gerbang presisi ditambahkan
    (mis. v4). Retrain berikutnya lewat train.py::main() sudah menghitung
    blok ini sendiri, tidak perlu perintah ini lagi."""
    version = training_failure.current_version(config.FAILURE_MODEL_DIR)
    if version is None:
        raise SystemExit("Tidak ada model failure CURRENT.")
    print(f"[1/2] Menghitung gerbang presisi>={config.FAILURE_GATE_TARGET_PRECISION:.0%} "
          f"untuk {version} (VALIDATION+TEST, dukungan beku)...")
    dataset, *_ = training_failure.build_dataset()
    validation = training_failure.evaluate_incumbent(version, dataset, split=training_failure.VALIDATION)
    test = training_failure.evaluate_incumbent(version, dataset, split=training_failure.TEST)
    gate_metadata = training_failure.compute_gate(
        validation["calibrated"], validation["target"], test["calibrated"], test["target"],
        validation_dataset=dataset.loc[dataset["split"].eq(training_failure.VALIDATION)],
        test_dataset=dataset.loc[dataset["split"].eq(training_failure.TEST)],
    )
    if gate_metadata["feasible"]:
        tm = gate_metadata["test_metrics"]
        print(
            f"      threshold={gate_metadata['threshold']:.4f}  TEST presisi={tm['precision']:.4f} "
            f"recall={tm['recall']:.4f} alert={tm['alerts']}"
        )
    else:
        print("      INFEASIBLE di VALIDATION - blok gate ditulis dengan feasible=false")

    path = config.FAILURE_MODEL_DIR / version / "metadata.json"

    def _apply(doc: dict) -> None:
        doc["gate"] = gate_metadata

    _update_metadata_json(path, _apply)
    print(f"[2/2] Disimpan ke {path}")
    return 0


_LIFECYCLE_SWEEP_TARGETS = (0.30, 0.40, 0.50, 0.60, 0.70, 0.85)


def _lifecycle_gate_experiment_main() -> int:
    print("[1/3] Menyusun dataset (sama seperti train.py::build_dataset)...")
    dataset, _features, _support_totals, data_end, _events, _cycles, _episodes = (
        training_failure.build_dataset()
    )
    print(f"      data s/d {data_end}, {len(dataset):,} baris eligible")

    print("[2/3] Skor model production v4 (dukungan beku dari metadata)...")
    model, calibrator, metadata = predict.load_failure_model()
    support = feature_builder.part_model_support(dataset, metadata["part_model_support"])
    candidate_features = feature_builder.build_features(dataset, support)[metadata["features"]]
    calibrated = calibrator.predict(model.predict_proba(candidate_features)[:, 1])

    val_mask = dataset["split"].eq(training_failure.VALIDATION).to_numpy()
    test_mask = dataset["split"].eq(training_failure.TEST).to_numpy()
    val_dataset, val_scores = dataset.loc[val_mask], calibrated[val_mask]
    test_dataset, test_scores = dataset.loc[test_mask], calibrated[test_mask]
    print(
        f"      VALIDATION: {len(val_dataset):,} baris, "
        f"{val_dataset['installation_cycle_id'].nunique():,} lifecycle"
    )
    print(
        f"      TEST:       {len(test_dataset):,} baris, "
        f"{test_dataset['installation_cycle_id'].nunique():,} lifecycle"
    )

    print(f"\n[3/3] Sweep target presisi lifecycle {_LIFECYCLE_SWEEP_TARGETS}...")
    header = f"{'target':>8}{'VAL presisi':>13}{'VAL recall':>12}{'VAL alert':>11}   {'TEST presisi':>13}{'TEST recall':>12}{'TEST alert':>11}"
    print(header)
    for target in _LIFECYCLE_SWEEP_TARGETS:
        selection = gate.select_lifecycle_threshold(val_dataset, val_scores, target_precision=target)
        if not selection["feasible"]:
            print(f"{target:>8.2f}   INFEASIBLE (presisi maks VALIDATION={selection['best_precision_achievable']:.4f})")
            continue
        test_eval = gate.lifecycle_metrics(test_dataset, test_scores, selection["threshold"])
        print(
            f"{target:>8.2f}{selection['precision']:>13.4f}{selection['recall']:>12.4f}"
            f"{selection['promoted_cycles']:>11}   "
            f"{test_eval['precision']:>13.4f}{test_eval['recall']:>12.4f}{test_eval['promoted_cycles']:>11}"
        )
        if target == 0.85 and test_eval["true_positive_cycles"] > 0:
            print(
                f"          TEST lead time (hari, TP): mean={test_eval['lead_time_days_mean']:.1f} "
                f"median={test_eval['lead_time_days_median']:.1f}"
            )
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
        description="Batch prediction untuk seluruh PART aktif (CLI, sama dengan GET /api/v1/recommendations).",
    )
    p_predict.add_argument("--output", help="Simpan seluruh hasil ke file CSV (opsional).")
    p_predict.add_argument("--top", type=int, default=10, help="Berapa baris teratas dicetak.")

    sub.add_parser(
        "score-and-persist",
        help="Milestone 2: skor seluruh PART aktif dan simpan sebagai model_run + "
        "item_prediction baru di schema predictive. Dipanggil scheduler eksternal berkala.",
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
    sub.add_parser(
        "precision-gate-experiment",
        help="Langkah 1: cari threshold presisi>=85% untuk baseline v4 vs kandidat horizon 7/14/30 hari.",
    )
    sub.add_parser(
        "attach-gate",
        help="Tempel blok gate (ambang presisi FAILURE_GATE_TARGET_PRECISION) ke metadata.json model failure CURRENT.",
    )
    sub.add_parser(
        "lifecycle-gate-experiment",
        help="Fase 8 Langkah A: sweep gerbang presisi di tingkat lifecycle (first-alert), bukan per-baris.",
    )

    args = parser.parse_args()

    if args.command == "pipeline":
        return _pipeline_main()
    if args.command == "predict":
        return _predict_main(args)
    if args.command == "score-and-persist":
        return _score_and_persist_main()
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
    if args.command == "precision-gate-experiment":
        return _precision_gate_experiment_main()
    if args.command == "attach-gate":
        return _attach_gate_main()
    if args.command == "lifecycle-gate-experiment":
        return _lifecycle_gate_experiment_main()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
