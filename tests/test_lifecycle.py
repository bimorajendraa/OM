from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from sklearn.isotonic import IsotonicRegression

from partrisk.core import config
from partrisk.core import data_reader
from partrisk.core import features as scrap_features
from partrisk.engines import predict as failure_model
from partrisk.engines import predict as scrap_model
from partrisk.engines.survival import predict as predict_survival
from partrisk.serving import batch as batch_predictor
from partrisk.engines.survival import curve as curves
from tests.conftest import needs_database, needs_models


def _synthetic_calibrators(horizons=(30, 60, 90, 120), seed=0) -> dict:
    rng = np.random.default_rng(seed)
    calibrators = {}
    for h in horizons:
        raw = np.sort(rng.random(200))
        label = (raw + rng.normal(0, 0.05, size=200) > 0.5).astype(float)
        calibrator = IsotonicRegression(out_of_bounds="clip")
        calibrator.fit(raw, label)
        calibrators[h] = calibrator
    return calibrators


def _synthetic_curve(times: np.ndarray, n_rows: int = 5, seed: int = 1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    rate = rng.uniform(0.001, 0.01, size=n_rows)
    return np.exp(-np.outer(rate, times))


def test_calibrate_curve_titik_persis_di_horizon_terlatih_tidak_nan():
    times = np.array([1.0, 15.0, 30.0, 45.0, 60.0, 75.0, 90.0, 105.0, 120.0, 200.0])
    curve = _synthetic_curve(times)
    calibrators = _synthetic_calibrators()
    result = curves.calibrate_curve(times, curve, calibrators)
    assert not np.isnan(result).any()
    assert result.shape == curve.shape


def test_calibrate_curve_grid_padat_harian_tidak_nan():
    times = np.arange(1, 400, dtype=float)
    curve = _synthetic_curve(times)
    calibrators = _synthetic_calibrators()
    result = curves.calibrate_curve(times, curve, calibrators)
    assert not np.isnan(result).any()


def test_calibrate_curve_hasil_monoton_non_increasing():
    times = np.arange(1, 400, dtype=float)
    curve = _synthetic_curve(times, n_rows=20, seed=7)
    calibrators = _synthetic_calibrators()
    result = curves.calibrate_curve(times, curve, calibrators)
    assert (np.diff(result, axis=1) <= 1e-9).all()


def test_calibrate_curve_hasil_dalam_rentang_0_1():
    times = np.arange(1, 400, dtype=float)
    curve = _synthetic_curve(times)
    calibrators = _synthetic_calibrators()
    result = curves.calibrate_curve(times, curve, calibrators)
    assert (result >= -1e-9).all() and (result <= 1.0 + 1e-9).all()


def test_calibrate_curve_di_titik_horizon_pas_sama_dengan_calibrator_langsung():
    times = np.array([30.0, 60.0, 90.0, 120.0])
    curve = _synthetic_curve(times, n_rows=3, seed=3)
    calibrators = _synthetic_calibrators()

    result = curves.calibrate_curve(times, curve, calibrators)

    raw_risk_at_30 = 1.0 - curve[:, 0]
    expected_calibrated_risk_at_30 = calibrators[30].predict(raw_risk_at_30)
    np.testing.assert_allclose(1.0 - result[:, 0], expected_calibrated_risk_at_30)


def test_mae_median_days_hanya_hitung_baris_event_observed():
    times = np.arange(1, 400, dtype=float)
    curve = _synthetic_curve(times, n_rows=4, seed=5)
    duration = np.array([50.0, 80.0, 120.0, 200.0])
    event = np.array([True, False, True, False])

    result = curves.mae_median_days(times, curve, duration, event)

    assert result["n_event_observed"] == 2
    assert result["n_usable"] <= 2
    assert result["mae_days"] is None or result["mae_days"] >= 0.0


def test_mae_median_days_median_persis_sama_dengan_durasi_mae_nol():
    times = np.array([10.0, 20.0, 30.0])
    curve = np.array([[0.6, 0.4, 0.1]])
    duration = np.array([20.0])
    event = np.array([True])

    result = curves.mae_median_days(times, curve, duration, event)

    assert result["n_usable"] == 1
    assert result["mae_days"] == pytest.approx(0.0)
    assert result["bias_days"] == pytest.approx(0.0)


def test_mae_median_days_tanpa_baris_usable_mengembalikan_none():
    times = np.array([10.0, 20.0, 30.0])
    curve = np.array([[0.6, 0.55, 0.51]])
    duration = np.array([20.0])
    event = np.array([True])

    result = curves.mae_median_days(times, curve, duration, event)

    assert result["n_usable"] == 0
    assert result["mae_days"] is None


SAMPLE_SIZE = 6


@pytest.fixture(scope="module")
def sample(batch) -> pd.DataFrame:
    frame = batch.frame
    positions = np.unique(
        np.linspace(0, len(frame) - 1, SAMPLE_SIZE).astype(int)
    )
    return frame.iloc[positions]


@needs_database
@needs_models
def test_probabilitas_kerusakan_batch_sama_dengan_single(sample):
    for _, row in sample.iterrows():
        single = failure_model.predict(row["item_id"])
        for days in config.PREDICTION_HORIZON_DAYS:
            column = f"failure_probability_{days}d"
            assert single[column] == row[column], (
                f"{row['item_id']} horizon {days}d: "
                f"single={single[column]} batch={row[column]}"
            )


@needs_database
@needs_models
def test_kelompok_risiko_kerusakan_batch_sama_dengan_single(sample):
    for _, row in sample.iterrows():
        single = failure_model.predict(row["item_id"])
        assert single["risk_level"] == row["failure_risk_level"]


@needs_database
@needs_models
def test_probabilitas_scrap_batch_sama_dengan_single(sample):
    for _, row in sample.iterrows():
        single = scrap_model.predict_scrap(row["item_id"])
        assert single["scrap_probability"] == row["scrap_probability"]
        assert single["scrap_risk_level"] == row["scrap_risk_level"]
        assert single["item_type"] == row["item_type"]


@needs_database
@needs_models
def test_median_days_to_failure_batch_sama_dengan_single(sample):
    try:
        predict_survival.load_model()
    except FileNotFoundError:
        pytest.skip("model survival belum dilatih (models/survival/)")

    for _, row in sample.iterrows():
        single = predict_survival.predict(row["item_id"])
        for batch_column, single_key in (
            ("median_days_to_failure", "median_days_remaining_from_now"),
            ("days_until_survival_90pct", "days_until_survival_90pct_from_now"),
            ("days_until_risk_medium", "days_until_risk_medium_from_now"),
            ("days_until_risk_high", "days_until_risk_high_from_now"),
        ):
            expected, actual = single[single_key], row[batch_column]
            if expected is None:
                assert pd.isna(actual), f"{row['item_id']}.{batch_column}: single=None batch={actual}"
            else:
                assert expected == actual, (
                    f"{row['item_id']}.{batch_column}: single={expected} batch={actual}"
                )


@needs_database
@needs_models
def test_survival_kurva_terkalibrasi_monoton_turun_dan_flag_benar(sample):
    try:
        predict_survival.load_model()
    except FileNotFoundError:
        pytest.skip("model survival belum dilatih (models/survival/)")

    for _, row in sample.iterrows():
        result = predict_survival.predict(row["item_id"])
        assert result["curve_is_calibrated"] is True
        curve = result["estimated_survival_curve_from_now"]
        if not curve:
            continue
        probs = [point["survival_probability"] for point in curve]
        assert all(0.0 - 1e-9 <= p <= 1.0 + 1e-9 for p in probs)
        assert all(a >= b - 1e-9 for a, b in zip(probs, probs[1:]))


@needs_database
@needs_models
def test_survival_urutan_ambang_waktu_konsisten(sample):
    try:
        predict_survival.load_model()
    except FileNotFoundError:
        pytest.skip("model survival belum dilatih (models/survival/)")

    checked_any = False
    for _, row in sample.iterrows():
        result = predict_survival.predict(row["item_id"])
        p90 = result["days_until_survival_90pct_from_now"]
        medium = result["days_until_risk_medium_from_now"]
        high = result["days_until_risk_high_from_now"]
        if p90 is not None and medium is not None:
            checked_any = True
            assert p90 <= medium + 1e-9, (row["item_id"], p90, medium)
        if medium is not None and high is not None:
            checked_any = True
            assert medium <= high + 1e-9, (row["item_id"], medium, high)
    if not checked_any:
        pytest.skip("tidak ada sample dengan pasangan ambang terisi untuk diuji")


@needs_database
@needs_models
def test_survival_calibrated_risk_monoton_naik(sample):
    try:
        predict_survival.load_model()
    except FileNotFoundError:
        pytest.skip("model survival belum dilatih (models/survival/)")

    checked_any = False
    for _, row in sample.iterrows():
        result = predict_survival.predict(row["item_id"])
        values = [result[f"calibrated_risk_{h}d"] for h in predict_survival.HORIZONS_DAYS]
        if any(v is None for v in values):
            continue
        checked_any = True
        for a, b in zip(values, values[1:]):
            assert a <= b + 1e-9, f"{row['item_id']}: calibrated_risk turun {values}"
    if not checked_any:
        pytest.skip("tidak ada sample dengan calibrated_risk terisi untuk diuji")


@needs_database
@needs_models
def test_kolom_mentah_scrap_batch_sama_dengan_current_state(batch, sample):
    items = sample["item_id"]
    cycles = data_reader.get_cycles()
    events = data_reader.get_events()
    batched = batch_predictor._scrap_states(
        events, cycles, batch.data_end, items
    ).set_index("item_identifier_clean")

    for item in items:
        single = scrap_features.current_state(
            data_reader.get_events(item),
            data_reader.get_cycles(item, batch.data_end),
            batch.data_end,
        )
        assert not single.empty, item
        expected = single.iloc[0]
        actual = batched.loc[item]
        for column in (
            "item_type_clean",
            "age_total_days",
            "cycle_age_days",
            "prior_repaired_count",
            "prior_failure_count",
            "failure_onset_on",
        ):
            left, right = expected[column], actual[column]
            if isinstance(left, float) and np.isnan(left):
                assert np.isnan(right), f"{item}.{column}"
            else:
                assert left == right, f"{item}.{column}: {left!r} != {right!r}"


@needs_database
@needs_models
def test_populasi_batch_sama_dengan_yang_dipakai_menyetel_ambang(batch):
    metadata = failure_model._load_failure_model()[2]
    basis = metadata["cutoff_basis"]
    if metadata["fleet_snapshot_at"] != str(batch.data_end):
        pytest.skip("database sudah bertambah sejak model dilatih")

    assert len(batch.frame) == basis["active_parts_scored"]
    high = int(batch.frame["failure_risk_level"].eq("HIGH").sum())
    assert high == basis["flagged_high"]


@needs_database
@needs_models
def test_urutan_prioritas_konsisten_dengan_kelompok_risiko(batch):
    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    ranks = batch.frame["failure_risk_level"].map(order).to_numpy()
    assert np.all(np.diff(ranks) >= 0)


@needs_database
@needs_models
def test_risiko_kumulatif_tidak_pernah_menurun(batch):
    horizons = config.PREDICTION_HORIZON_DAYS
    for earlier, later in zip(horizons, horizons[1:]):
        assert (
            batch.frame[f"failure_probability_{earlier}d"]
            <= batch.frame[f"failure_probability_{later}d"] + 1e-12
        ).all()
