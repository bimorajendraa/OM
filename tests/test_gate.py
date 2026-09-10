from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from partrisk.engines.failure import gate


def _synthetic_with_signal(n: int = 2000, seed: int = 42):
    rng = np.random.default_rng(seed)
    labels = rng.random(n) < 0.05
    scores = np.clip(
        labels * rng.uniform(0.4, 0.95, n) + (~labels) * rng.uniform(0.0, 0.3, n), 0, 1
    )
    return scores, labels


def test_select_precision_constrained_threshold_kasus_feasible():
    scores, labels = _synthetic_with_signal()
    result = gate.select_precision_constrained_threshold(scores, labels, target_precision=0.85)
    assert result["feasible"] is True
    assert result["precision"] >= 0.85
    flagged = scores >= result["threshold"]
    assert int(flagged.sum()) == result["alerts"]
    true_positive = int(labels[flagged].sum())
    assert true_positive / result["alerts"] == pytest.approx(result["precision"])


def test_select_precision_constrained_threshold_infeasible_tidak_substitusi_diam_diam():
    rng = np.random.default_rng(1)
    n = 2000
    labels = rng.random(n) < 0.05
    noise_scores = rng.random(n)
    result = gate.select_precision_constrained_threshold(noise_scores, labels, target_precision=0.85)
    assert result["feasible"] is False
    assert result["threshold"] is None
    assert result["precision"] is None
    assert result["best_precision_achievable"] < 0.85


def test_select_precision_constrained_threshold_tanpa_label_positif():
    rng = np.random.default_rng(2)
    scores = rng.random(200)
    labels = np.zeros(200, dtype=bool)
    result = gate.select_precision_constrained_threshold(scores, labels, target_precision=0.85)
    assert result["feasible"] is False
    assert result["threshold"] is None
    assert result["best_precision_achievable"] == 0.0


def test_select_precision_constrained_threshold_maksimalkan_recall_bukan_presisi():

    scores, labels = _synthetic_with_signal()
    result = gate.select_precision_constrained_threshold(scores, labels, target_precision=0.30)
    assert result["feasible"] is True

    strict = gate.select_precision_constrained_threshold(scores, labels, target_precision=0.85)
    if strict["feasible"]:
        assert result["recall"] >= strict["recall"]


def test_precision_lower_bound_tanpa_true_positive_adalah_nol():
    assert gate.precision_lower_bound(0, 10) == 0.0
    assert gate.precision_lower_bound(5, 0) == 0.0


def test_precision_lower_bound_lebih_ketat_dari_presisi_titik_estimasi_sampel_kecil():
    # sampel kecil (9/22) - batas bawah Clopper-Pearson wajib jauh di bawah titik-estimasi.
    point_estimate = 9 / 22
    lower = gate.precision_lower_bound(9, 22, confidence=0.95)
    assert lower < point_estimate - 0.1


def test_select_threshold_menolak_winners_curse_yang_diterima_aturan_lama():
    """Aturan baru wajib menolak winner's curse - docs/DECISIONS.md §43."""
    n = 200
    scores = np.linspace(1.0, 0.0, n)
    labels = np.zeros(n, dtype=bool)
    labels[:6] = True
    for i in (8, 10, 13):
        labels[i] = True

    old = gate.select_precision_constrained_threshold(scores, labels, target_precision=0.40)
    assert old["feasible"] is True, "prasyarat: aturan LAMA harus menganggap skenario ini feasible"
    assert old["alerts"] < 30, "prasyarat: aturan lama memilih threshold dengan sedikit alert"

    new = gate.select_threshold(scores, labels, target_precision=0.40, min_alerts=30)
    assert new["feasible"] is False, (
        "aturan BARU wajib menolak threshold yang cuma didukung sedikit alert, "
        "walau presisi titik-estimasinya di atas target"
    )
    assert new["threshold"] is None


def test_select_threshold_threshold_reproduksi_jumlah_alert_yang_sama_saat_ada_ties():
    # skor bertangga (isotonic) - alert harus sama persis dgn (scores >= threshold).sum().
    rng = np.random.default_rng(3)
    n = 500
    scores = np.round(rng.random(n), 1)
    labels = rng.random(n) < (scores * 0.6)

    result = gate.select_threshold(scores, labels, target_precision=0.3, min_alerts=10)
    assert result["feasible"] is True
    actual_alerts = int((scores >= result["threshold"]).sum())
    assert actual_alerts == result["alerts"]
    actual_true_positive = int(labels[scores >= result["threshold"]].sum())
    assert actual_true_positive == result["true_positive"]


def test_select_threshold_tanpa_label_positif():
    rng = np.random.default_rng(2)
    scores = rng.random(200)
    labels = np.zeros(200, dtype=bool)
    result = gate.select_threshold(scores, labels, target_precision=0.85)
    assert result["feasible"] is False
    assert result["threshold"] is None
    assert result["best_precision_achievable"] == 0.0


def test_select_threshold_maksimalkan_cakupan_bukan_presisi():
    scores, labels = _synthetic_with_signal()
    permisif = gate.select_threshold(scores, labels, target_precision=0.30, min_alerts=5)
    ketat = gate.select_threshold(scores, labels, target_precision=0.85, min_alerts=5)
    assert permisif["feasible"] is True
    if ketat["feasible"]:
        assert permisif["recall"] >= ketat["recall"]


def test_honest_test_evaluation_murni_mengukur_bukan_mencari_ulang():
    scores, labels = _synthetic_with_signal()
    selection = gate.select_precision_constrained_threshold(scores, labels, target_precision=0.5)
    assert selection["feasible"]

    evaluation = gate.honest_test_evaluation(scores, labels, selection["threshold"])
    assert evaluation["threshold"] == pytest.approx(selection["threshold"])
    assert evaluation["precision"] == pytest.approx(selection["precision"])
    assert evaluation["recall"] == pytest.approx(selection["recall"])
    assert evaluation["true_positive"] + evaluation["false_positive"] == evaluation["alerts"]


def test_honest_test_evaluation_threshold_tidak_ada_yang_lolos():
    scores = np.array([0.1, 0.2, 0.3])
    labels = np.array([False, True, False])
    result = gate.honest_test_evaluation(scores, labels, threshold=0.99)
    assert result["alerts"] == 0
    assert result["true_positive"] == 0
    assert result["precision"] == 0.0
    assert result["recall"] == 0.0


def test_reliability_table_bentuk_dan_isi():
    scores, labels = _synthetic_with_signal()
    table = gate.reliability_table(scores, labels, n_bins=5)
    assert set(table.columns) >= {"mean_predicted", "observed_rate", "n"}
    assert table["n"].sum() == len(scores)
    assert (table["observed_rate"] >= 0).all()
    assert (table["observed_rate"] <= 1).all()


def _lifecycle_dataset() -> tuple[pd.DataFrame, np.ndarray]:
    rows = [

        {"installation_cycle_id": "A", "observation_on": pd.Timestamp("2026-01-01"), "score": 0.9,
         "target_failure": False, "failure_onset_on": pd.Timestamp("2026-02-15")},
        {"installation_cycle_id": "A", "observation_on": pd.Timestamp("2026-01-31"), "score": 0.95,
         "target_failure": True, "failure_onset_on": pd.Timestamp("2026-02-15")},

        {"installation_cycle_id": "B", "observation_on": pd.Timestamp("2026-01-01"), "score": 0.9,
         "target_failure": True, "failure_onset_on": pd.Timestamp("2026-01-20")},

        {"installation_cycle_id": "C", "observation_on": pd.Timestamp("2026-01-01"), "score": 0.1,
         "target_failure": True, "failure_onset_on": pd.Timestamp("2026-01-25")},

        {"installation_cycle_id": "D", "observation_on": pd.Timestamp("2026-01-01"), "score": 0.9,
         "target_failure": False, "failure_onset_on": pd.NaT},
    ]
    frame = pd.DataFrame(rows)
    return frame.drop(columns=["score"]), frame["score"].to_numpy()


def test_lifecycle_metrics_dedup_hanya_pakai_alert_pertama():
    dataset, scores = _lifecycle_dataset()
    result = gate.lifecycle_metrics(dataset, scores, threshold=0.5)

    assert result["total_cycles"] == 4
    assert result["failed_cycles"] == 3
    assert result["promoted_cycles"] == 3
    assert result["true_positive_cycles"] == 1
    assert result["false_positive_cycles"] == 2
    assert result["false_negative_cycles"] == 2
    assert result["precision"] == pytest.approx(1 / 3)
    assert result["recall"] == pytest.approx(1 / 3)


def test_lifecycle_metrics_lead_time_dari_alert_pertama():
    dataset, scores = _lifecycle_dataset()
    result = gate.lifecycle_metrics(dataset, scores, threshold=0.5)

    assert result["lead_time_days_mean"] == pytest.approx(19.0)
    assert result["lead_time_days_median"] == pytest.approx(19.0)


def test_lifecycle_metrics_threshold_tidak_ada_yang_lolos():
    dataset, scores = _lifecycle_dataset()
    result = gate.lifecycle_metrics(dataset, scores, threshold=0.99)
    assert result["promoted_cycles"] == 0
    assert result["precision"] == 0.0
    assert result["lead_time_days_mean"] is None


def _synthetic_lifecycle_with_signal(n: int = 1000, seed: int = 7):
    rng = np.random.default_rng(seed)
    labels = rng.random(n) < 0.05
    scores = np.clip(
        labels * rng.uniform(0.4, 0.95, n) + (~labels) * rng.uniform(0.0, 0.3, n), 0, 1
    )
    observed = pd.Timestamp("2026-01-01") + pd.to_timedelta(np.arange(n), unit="D")
    failure_onset = observed + pd.Timedelta(days=10)
    dataset = pd.DataFrame({
        "installation_cycle_id": [f"C{i}" for i in range(n)],
        "observation_on": observed,
        "target_failure": labels,
        "failure_onset_on": failure_onset.where(labels, pd.NaT),
    })
    return dataset, scores


def test_select_lifecycle_threshold_feasible_cocok_dengan_lifecycle_metrics():
    dataset, scores = _synthetic_lifecycle_with_signal()
    selection = gate.select_lifecycle_threshold(dataset, scores, target_precision=0.85)
    assert selection["feasible"] is True
    assert selection["precision"] >= 0.85

    recomputed = gate.lifecycle_metrics(dataset, scores, selection["threshold"])
    assert recomputed["precision"] == pytest.approx(selection["precision"])
    assert recomputed["recall"] == pytest.approx(selection["recall"])
    assert recomputed["promoted_cycles"] == selection["promoted_cycles"]


def test_select_lifecycle_threshold_maksimalkan_recall_bukan_presisi():
    dataset, scores = _synthetic_lifecycle_with_signal()
    permisif = gate.select_lifecycle_threshold(dataset, scores, target_precision=0.30)
    ketat = gate.select_lifecycle_threshold(dataset, scores, target_precision=0.85)
    assert permisif["feasible"] is True
    if ketat["feasible"]:
        assert permisif["recall"] >= ketat["recall"]


def test_select_lifecycle_threshold_infeasible_tidak_substitusi_diam_diam():
    rng = np.random.default_rng(1)
    n = 500
    labels = rng.random(n) < 0.05
    noise_scores = rng.random(n)
    observed = pd.Timestamp("2026-01-01") + pd.to_timedelta(np.arange(n), unit="D")
    dataset = pd.DataFrame({
        "installation_cycle_id": [f"C{i}" for i in range(n)],
        "observation_on": observed,
        "target_failure": labels,
        "failure_onset_on": (observed + pd.Timedelta(days=10)).where(labels, pd.NaT),
    })
    result = gate.select_lifecycle_threshold(dataset, noise_scores, target_precision=0.85)
    assert result["feasible"] is False
    assert result["threshold"] is None
    assert result["best_precision_achievable"] < 0.85
