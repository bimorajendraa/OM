from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from partrisk.core import config, data_reader
from partrisk.core import features as feature_builder
from partrisk.engines import predict as failure_model
from partrisk.engines.failure import train
from partrisk.engines.failure import train as training_utils
from tests.conftest import needs_database, needs_models


def _minimal_failure_raw(n: int = 3) -> pd.DataFrame:
    return pd.DataFrame({
        "item_model_code_clean": ["0521201"] * n,
        "installed_client_clean": ["CLIENT A"] * n,
        "days_since_installation": [10.0, 200.0, 800.0][:n],
        "total_prior_events": [1, 2, 3][:n],
        "prior_failure_count": [0, 1, 0][:n],
        "prior_corrective_count": [0, 1, 0][:n],
        "days_since_last_corrective": [np.nan, 5.0, np.nan][:n],
        "prior_distinct_places": [1, 2, 1][:n],
        "prior_corrective_30d": [0, 1, 0][:n],
        "prior_failure_365d": [0, 1, 0][:n],
        "prior_events_180d": [1, 2, 1][:n],
        "previous_cycle_lifetime_mean": [np.nan, 100.0, np.nan][:n],
        "has_previous_cycle": [False, True, False][:n],
        "observation_on": pd.to_datetime(["2026-01-01", "2026-02-01", "2026-03-01"][:n]),
        "log_model_failures_90d": [0.0, 1.0, 0.5][:n],
        "model_failure_rate_90d": [0.0, 0.1, 0.05][:n],
        "log_model_fleet_size": [1.0, 2.0, 1.5][:n],
        "log_cumulative_prior_cycle_days": [0.0, np.log1p(50.0), 0.0][:n],
        "log_previous_cycle_count": [0.0, np.log1p(1), 0.0][:n],
        "has_failure_interval_trend": [False, True, False][:n],
        "log_failure_interval_mean_days": [0.0, 3.5, 0.0][:n],
        "failure_interval_trend_ratio": [1.0, 0.8, 1.0][:n],
        "log_prior_corrective_60d": [0.0, 1.0, 0.0][:n],
        "log_prior_corrective_90d": [0.0, 1.0, 0.0][:n],
        "log_item_type_failures_90d": [0.0, 1.0, 0.5][:n],
        "item_type_failure_rate_90d": [0.0, 0.05, 0.02][:n],
        "log_item_type_failures_180d": [0.0, 1.5, 0.7][:n],
        "item_type_failure_rate_180d": [0.0, 0.07, 0.03][:n],
    })


def test_kolom_fitur_kerusakan_persis_sama_dengan_config():
    raw = _minimal_failure_raw()
    support = pd.Series([500] * len(raw))
    features = feature_builder.build_features(raw, support)
    assert list(features.columns) == config.FEATURE_COLUMNS


def test_kolom_fitur_kerusakan_konsisten_lewat_project_features():
    raw = _minimal_failure_raw()
    support = pd.Series([500] * len(raw))
    for step in range(4):
        features = feature_builder.project_features(raw, support, step)
        assert list(features.columns) == config.FEATURE_COLUMNS, f"step={step}"


def test_urutan_kolom_kategorikal_dan_numerik_tidak_bercampur():
    n_cat = len(config.CATEGORICAL_FEATURES)
    assert config.FEATURE_COLUMNS[:n_cat] == config.CATEGORICAL_FEATURES


@needs_models
def test_model_dimuat_sekali_dipakai_ulang():
    failure_model._LOADED_FAILURE = None
    first = failure_model._load_failure_model()
    second = failure_model._load_failure_model()
    assert first[0] is second[0]
    assert first[1] is second[1]


@needs_models
def test_model_baru_terlihat_setelah_cache_direset(tmp_path_factory=None):
    failure_model._LOADED_FAILURE = None
    first = failure_model._load_failure_model()
    version_first = first[2]["model_version"]

    failure_model._LOADED_FAILURE = None
    second = failure_model._load_failure_model()
    version_second = second[2]["model_version"]

    assert version_first == version_second
    assert first[0] is not second[0]


@needs_models
def test_fleet_snapshot_ikut_dibuang_saat_model_direset():
    failure_model._LOADED_FAILURE = None
    failure_model._FLEET = None
    metadata = failure_model._load_failure_model()[2]
    snapshot = failure_model._fleet_snapshot(pd.Timestamp(metadata["fleet_snapshot_at"]))
    assert not snapshot.empty
    assert set(config.FLEET_FEATURES) <= set(snapshot.columns)


def _events(rows: list[tuple]) -> pd.DataFrame:
    return pd.DataFrame({
        "item_identifier_clean": ["ITEM-A"] * len(rows),
        "created_on": pd.to_datetime([r[0] for r in rows]),
        "wo_type_clean": [r[1] for r in rows],
        "status_clean": ["DISMANTLED"] * len(rows),
        "is_failure_onset": [r[2] for r in rows],
        "place_canonical_clean": [r[3] for r in rows],
    })


def _observation(at: str) -> pd.DataFrame:
    return pd.DataFrame({
        "item_identifier_clean": ["ITEM-A"],
        "observation_on": pd.to_datetime([at]),
    })


def test_attach_history_tidak_melihat_event_setelah_observasi():
    baseline_events = _events([
        ("2026-01-01", "CORRECTIVE", True, "LOKASI A"),
        ("2026-01-15", "PREVENTIVE", False, "LOKASI A"),
    ])
    future_events = pd.concat([
        baseline_events,
        _events([("2026-06-01", "CORRECTIVE", True, "LOKASI B")]),
    ], ignore_index=True)

    observation = _observation("2026-02-01")
    baseline_result = feature_builder.attach_history(observation.copy(), baseline_events)
    with_future = feature_builder.attach_history(observation.copy(), future_events)

    for column in feature_builder._HISTORY_COUNTS:
        assert baseline_result[column].iloc[0] == with_future[column].iloc[0], column
    assert (
        baseline_result["days_since_last_corrective"].iloc[0]
        == with_future["days_since_last_corrective"].iloc[0]
    )


def test_attach_history_event_pada_detik_observasi_ikut_terhitung():
    at_boundary = _events([("2026-02-01", "CORRECTIVE", True, "LOKASI A")])
    after_boundary = _events([("2026-02-01 00:00:01", "CORRECTIVE", True, "LOKASI A")])

    observation = _observation("2026-02-01")
    included = feature_builder.attach_history(observation.copy(), at_boundary)
    excluded = feature_builder.attach_history(observation.copy(), after_boundary)

    assert included["prior_failure_count"].iloc[0] == 1
    assert excluded["prior_failure_count"].iloc[0] == 0


def test_project_features_tidak_memakai_kejadian_sungguhan_masa_depan():
    raw = _minimal_failure_raw(n=1)
    support = pd.Series([500])

    baseline = feature_builder.project_features(raw, support, steps_ahead=0)
    projected = feature_builder.project_features(raw, support, steps_ahead=2)

    assert (
        baseline["log_prior_failure_count"].iloc[0]
        == projected["log_prior_failure_count"].iloc[0]
    )
    assert (
        baseline["log_total_prior_events"].iloc[0]
        == projected["log_total_prior_events"].iloc[0]
    )
    assert (
        projected["log_days_since_installation"].iloc[0]
        > baseline["log_days_since_installation"].iloc[0]
    )


def test_label_negatif_dekat_batas_data_tidak_dipakai():
    last_confirmable = pd.Timestamp("2026-07-04")
    cycle = pd.DataFrame({
        "is_initial_model_cohort": [True],
        "installed_on": pd.to_datetime(["2026-01-01"]),
        "cycle_end_on": pd.to_datetime(["2026-08-03"]),
        "dataset_max_event_on": pd.to_datetime(["2026-08-03"]),
        "failure_onset_on": [pd.NaT],
        "is_recon_verified_negative_eligible": [True],
        "last_confirmable_observation_on": [last_confirmable],
    })

    observations = feature_builder.training_observations(cycle)

    before_boundary = observations["observation_on"] <= last_confirmable
    after_boundary = observations["observation_on"] > last_confirmable

    assert before_boundary.any() and after_boundary.any(), (
        "grid observasi test tidak mencakup kedua sisi batas - perbaiki setup test"
    )
    assert observations.loc[before_boundary, "is_eligible"].all(), (
        "observasi sebelum batas confirmable seharusnya layak dipakai (negatif terbukti)"
    )
    assert not observations.loc[after_boundary, "is_eligible"].any(), (
        "observasi SETELAH batas confirmable terpakai - hasilnya belum tentu benar "
        "negatif, ini kebocoran: model bisa belajar dari label yang belum pasti"
    )


def test_label_positif_dekat_batas_data_tetap_dipakai():
    cycle = pd.DataFrame({
        "is_initial_model_cohort": [True],
        "installed_on": pd.to_datetime(["2026-07-01"]),
        "cycle_end_on": pd.to_datetime(["2026-07-20"]),
        "dataset_max_event_on": pd.to_datetime(["2026-07-20"]),
        "failure_onset_on": pd.to_datetime(["2026-07-20"]),
        "is_recon_verified_negative_eligible": [False],
        "last_confirmable_observation_on": pd.to_datetime(["2026-06-01"]),
    })

    observations = feature_builder.training_observations(cycle)

    assert observations["target_failure"].any(), "setup test tidak menghasilkan target positif"
    positive_rows = observations.loc[observations["target_failure"]]
    assert positive_rows["is_eligible"].all(), (
        "observasi dengan kerusakan terkonfirmasi tidak boleh dibuang hanya karena "
        "dekat batas data - kerusakan yang tercatat adalah fakta, bukan estimasi"
    )


def test_training_observations_horizon_days_mempersempit_jendela_target():

    cycle = pd.DataFrame({
        "is_initial_model_cohort": [True],
        "installed_on": pd.to_datetime(["2026-01-01"]),
        "cycle_end_on": pd.to_datetime(["2026-03-01"]),
        "dataset_max_event_on": pd.to_datetime(["2026-03-01"]),
        "failure_onset_on": pd.to_datetime(["2026-01-16"]),
        "is_recon_verified_negative_eligible": [False],
        "last_confirmable_observation_on": pd.to_datetime(["2026-01-01"]),
    })

    default_horizon = feature_builder.training_observations(cycle)
    short_horizon = feature_builder.training_observations(cycle, horizon_days=7)

    first_observation_default = default_horizon.iloc[0]
    first_observation_short = short_horizon.iloc[0]
    assert first_observation_default["observation_on"] == first_observation_short["observation_on"]
    assert first_observation_default["target_failure"], (
        "setup test salah - horizon 30 hari (default) semestinya menangkap "
        "kerusakan 15 hari setelah observasi"
    )
    assert not first_observation_short["target_failure"], (
        "horizon_days=7 seharusnya TIDAK menangkap kerusakan yang terjadi "
        "15 hari setelah observasi - parameter horizon tidak benar-benar dipakai"
    )


def test_assign_split_membuang_observasi_terlalu_lama():
    data_end = pd.Timestamp("2026-08-03")
    dataset = pd.DataFrame({
        "observation_on": pd.to_datetime([
            "2010-01-01",
            "2020-01-01",
        ]),
    })
    split = train.assign_split(dataset, data_end)
    assert split.iloc[0] == "EXCLUDED_TOO_OLD"
    assert split.iloc[1] == train.TRAIN


def test_assign_split_test_dan_validation_tidak_tumpang_tindih_dengan_train():
    data_end = pd.Timestamp("2026-08-03")
    horizon = pd.Timedelta(days=config.TARGET_HORIZON_DAYS)
    validation_start = pd.Timestamp(year=data_end.year, month=1, day=1) - pd.DateOffset(years=1)

    dataset = pd.DataFrame({
        "observation_on": [validation_start - pd.Timedelta(days=1)],
    })
    split = train.assign_split(dataset, data_end)
    assert split.iloc[0] != train.TRAIN, (
        "observasi yang jawabannya baru terungkap di periode VALIDATION "
        "tidak boleh ikut TRAIN - itu kebocoran dari masa depan (relatif "
        "terhadap TRAIN) ke masa lalu"
    )


def _open_cycle(item_id: str = "ITEM-A") -> pd.DataFrame:
    return pd.DataFrame({
        "item_identifier_clean": [item_id],
        "is_initial_model_cohort": [True],
        "cycle_end_reason": ["RIGHT_CENSORED_AT_DATA_END"],
        "installed_on": pd.to_datetime(["2020-01-01"]),
        "dataset_max_event_on": pd.to_datetime(["2026-08-03"]),
    })


def _raw_events(rows: list[tuple], item_id: str = "ITEM-A") -> pd.DataFrame:
    return pd.DataFrame({
        "item_identifier_clean": [item_id] * len(rows),
        "created_on": pd.to_datetime([r[0] for r in rows], format="mixed"),
        "journey_id": range(len(rows)),
        "status_clean": [r[1] for r in rows],
    })


def test_current_observations_menyaring_part_yang_sudah_dilepas():

    cycles = _open_cycle()
    events = _raw_events([
        ("2020-01-01", "INSTALLED"),
        ("2025-06-01", "RETURNED"),
        ("2025-06-01 00:00:05", "OK"),
    ])
    result = feature_builder.current_observations(cycles, events)
    assert result.empty


def test_current_observations_mempertahankan_part_yang_masih_installed():
    cycles = _open_cycle()
    events = _raw_events([
        ("2020-01-01", "INSTALLED"),
    ])
    result = feature_builder.current_observations(cycles, events)
    assert len(result) == 1
    assert result["item_identifier_clean"].iloc[0] == "ITEM-A"


def test_current_observations_tanpa_event_sama_sekali_dibuang():
    cycles = _open_cycle()
    events = _raw_events([], item_id="ITEM-LAIN")
    result = feature_builder.current_observations(cycles, events)
    assert result.empty


@needs_database
def test_get_cycles_tidak_membiarkan_part_yang_sudah_dilepas_tetap_aktif():
    cycles = data_reader.get_cycles()
    events = data_reader.get_events()
    failure = cycles["cycle_end_reason"].eq("FAILURE")
    detached = cycles["cycle_end_reason"].isin(["RETURNED", "DISMANTLED"])
    active = cycles.loc[
        cycles["is_initial_model_cohort"].fillna(False)
        & cycles["cycle_end_reason"].eq("RIGHT_CENSORED_AT_DATA_END")
    ]
    latest_status = (
        events.sort_values(["item_identifier_clean", "created_on", "journey_id"])
        .groupby("item_identifier_clean")["status_clean"]
        .last()
    )

    assert cycles.loc[failure, "failure_onset_on"].eq(
        cycles.loc[failure, "cycle_end_on"]
    ).all()
    assert cycles.loc[detached, "failure_onset_on"].isna().all()
    assert active["item_identifier_clean"].map(latest_status).eq("INSTALLED").all()


@needs_database
def test_as_of_membatasi_seluruh_pembacaan_ke_boundary_yang_sama():
    """Satu batch scoring run harus melihat SATU potret data operasional yang
    konsisten (docs/DECISIONS.md) - get_events/get_terminal_context/
    get_failure_episodes/get_cycles semuanya dibatasi `as_of`/
    `dataset_max_event_on` yang sama, bukan membaca live tanpa batas."""
    latest = data_reader.get_dataset_max_event_on()
    events_all = data_reader.get_events()
    cutoff = events_all["created_on"].sort_values().iloc[len(events_all) // 2]

    bounded_events = data_reader.get_events(as_of=cutoff)
    assert (bounded_events["created_on"] <= cutoff).all()
    assert len(bounded_events) < len(events_all), (
        "as_of di tengah rentang data harus benar-benar memotong sebagian baris"
    )

    bounded_episodes = data_reader.get_failure_episodes(as_of=cutoff)
    assert (bounded_episodes["failure_onset_on"] <= cutoff).all()

    bounded_terminal = data_reader.get_terminal_context(as_of=cutoff)
    assert (bounded_terminal["installed_on"] <= cutoff).all()

    bounded_cycles = data_reader.get_cycles(dataset_max_event_on=cutoff)
    assert (bounded_cycles["installed_on"] <= cutoff).all()

    # Tanpa as_of (default None) - perilaku lama tetap utuh, tidak dibatasi.
    unbounded_cycles = data_reader.get_cycles()
    assert unbounded_cycles["dataset_max_event_on"].max() == latest


def _synthetic(n: int = 1000, positive_rate: float = 0.05, seed: int = 0):
    rng = np.random.default_rng(seed)
    target = (rng.random(n) < positive_rate).astype(int)
    raw = target * rng.random(n) * 0.5 + rng.random(n) * 0.3
    return raw, target


def test_capacity_metrics_menangkap_seluruh_positif_saat_kapasitas_cukup():
    raw, target = _synthetic()
    result = training_utils.capacity_metrics(raw, target, window_days=10_000_000.0, capacity_per_month=10)
    assert result["capacity_evaluated"] == len(raw)
    assert result["recall_at_capacity"] == pytest.approx(1.0)


def test_capacity_metrics_kapasitas_minimal_satu():
    raw, target = _synthetic(n=50)
    result = training_utils.capacity_metrics(raw, target, window_days=0.01, capacity_per_month=10)
    assert result["capacity_evaluated"] >= 1
    assert 0.0 <= result["precision_at_capacity"] <= 1.0


def test_capacity_metrics_kapasitas_tidak_melebihi_jumlah_baris():
    raw, target = _synthetic(n=20)
    result = training_utils.capacity_metrics(raw, target, window_days=100000.0, capacity_per_month=10)
    assert result["capacity_evaluated"] <= 20


def test_capacity_metrics_days_per_month_mempengaruhi_kapasitas():
    raw, target = _synthetic(n=500)
    result_30 = training_utils.capacity_metrics(raw, target, window_days=180.0, capacity_per_month=10, days_per_month=30.0)
    result_30_44 = training_utils.capacity_metrics(raw, target, window_days=180.0, capacity_per_month=10, days_per_month=30.44)
    assert result_30["capacity_evaluated"] >= result_30_44["capacity_evaluated"]


def test_full_metrics_berisi_seluruh_metrik_yang_disyaratkan():
    raw, target = _synthetic()
    calibrated = raw.copy()
    result = training_utils.full_metrics(raw, calibrated, target, window_days=180.0, capacity_per_month=10)
    for key in (
        "roc_auc", "pr_auc", "brier_calibrated",
        "precision_at_capacity", "recall_at_capacity",
    ):
        assert key in result, key


def _metrics(**overrides) -> dict:
    base = {
        "pr_auc": 0.20, "roc_auc": 0.80, "recall_at_capacity": 0.30,
        "precision_at_capacity": 0.15, "brier_calibrated": 0.02,
    }
    base.update(overrides)
    return base


def test_promosi_pertama_kali_selalu_lolos():
    promote, reason, comparison = training_utils.decide_promotion(_metrics(), None, None, force=False)
    assert promote is True
    assert "belum ada" in reason


def test_kandidat_lebih_baik_di_kedua_metrik_dipromosikan():
    candidate = _metrics(pr_auc=0.25, recall_at_capacity=0.35)
    incumbent = _metrics(pr_auc=0.20, recall_at_capacity=0.30)
    promote, reason, comparison = training_utils.decide_promotion(candidate, incumbent, "v1", force=False)
    assert promote is True
    assert comparison["incumbent_version"] == "v1"


def test_pr_auc_turun_menahan_promosi_walau_recall_naik():
    candidate = _metrics(pr_auc=0.15, recall_at_capacity=0.40, roc_auc=0.90)
    incumbent = _metrics(pr_auc=0.20, recall_at_capacity=0.30, roc_auc=0.80)
    promote, reason, comparison = training_utils.decide_promotion(candidate, incumbent, "v1", force=False)
    assert promote is False


def test_recall_turun_menahan_promosi_walau_pr_auc_naik():
    candidate = _metrics(pr_auc=0.25, recall_at_capacity=0.20)
    incumbent = _metrics(pr_auc=0.20, recall_at_capacity=0.30)
    promote, reason, comparison = training_utils.decide_promotion(candidate, incumbent, "v1", force=False)
    assert promote is False


def test_force_promote_memaksa_walau_lebih_buruk():
    candidate = _metrics(pr_auc=0.10, recall_at_capacity=0.10)
    incumbent = _metrics(pr_auc=0.20, recall_at_capacity=0.30)
    promote, reason, comparison = training_utils.decide_promotion(candidate, incumbent, "v1", force=True)
    assert promote is True
    assert "dipaksa" in reason


def test_kandidat_dan_incumbent_identik_dipromosikan():
    same = _metrics()
    promote, reason, comparison = training_utils.decide_promotion(same, dict(same), "v1", force=False)
    assert promote is True


def test_split_label_default_tetap_test_untuk_kompatibilitas_mundur():
    candidate = _metrics(pr_auc=0.25, recall_at_capacity=0.35)
    incumbent = _metrics(pr_auc=0.20, recall_at_capacity=0.30)
    promote, reason, comparison = training_utils.decide_promotion(candidate, incumbent, "v1", force=False)
    assert comparison["decisive_split"] == "TEST"
    assert reason.startswith("[TEST]")


def test_split_label_validation_dipakai_failure_model_untuk_gerbang_promosi():
    candidate = _metrics(pr_auc=0.25, recall_at_capacity=0.35)
    incumbent = _metrics(pr_auc=0.20, recall_at_capacity=0.30)
    promote, reason, comparison = training_utils.decide_promotion(
        candidate, incumbent, "v1", force=False, split_label="VALIDATION"
    )
    assert comparison["decisive_split"] == "VALIDATION"
    assert reason.startswith("[VALIDATION]")


def test_split_label_tidak_mengubah_keputusan_hanya_label():
    candidate = _metrics(pr_auc=0.15, recall_at_capacity=0.40)
    incumbent = _metrics(pr_auc=0.20, recall_at_capacity=0.30)
    promote_test, _, _ = training_utils.decide_promotion(candidate, incumbent, "v1", force=False)
    promote_val, _, _ = training_utils.decide_promotion(
        candidate, incumbent, "v1", force=False, split_label="VALIDATION"
    )
    assert promote_test is False
    assert promote_val is False


@needs_database
@needs_models
def test_evaluate_incumbent_menghasilkan_skor_valid():
    events = data_reader.get_events()
    cycles = data_reader.get_cycles()
    data_end = pd.Timestamp(cycles["dataset_max_event_on"].max())

    observations = feature_builder.training_observations(cycles)
    observations = feature_builder.attach_history(observations, events)
    observations = feature_builder.attach_degradation_history(observations, cycles, events)
    episodes = data_reader.get_failure_episodes()
    observations = feature_builder.attach_fleet(observations, cycles, episodes)
    observations = feature_builder.attach_item_type_density(observations, events, cycles, episodes)
    eligible = observations.loc[observations["is_eligible"]].reset_index(drop=True)
    eligible["split"] = train.assign_split(eligible, data_end)
    eligible = eligible.loc[eligible["split"].isin([train.TRAIN, train.VALIDATION, train.TEST])]

    if eligible["split"].eq(train.TEST).sum() == 0:
        pytest.skip("tidak ada baris test split untuk diuji")

    if not (config.FAILURE_MODEL_DIR / "v1" / "metadata.json").exists():
        pytest.skip("model v1 tidak ada di repo ini")

    result = train.evaluate_incumbent("v1", eligible)
    assert result["model_version"] == "v1"
    assert len(result["raw"]) == eligible["split"].eq(train.TEST).sum()
    assert ((result["raw"] >= 0) & (result["raw"] <= 1)).all()
    assert ((result["calibrated"] >= 0) & (result["calibrated"] <= 1)).all()
    assert set(np.unique(result["target"])) <= {0, 1, True, False}


BASELINE_PATH = Path(__file__).resolve().parent.parent / ".cache" / "golden" / "phase0_baseline.parquet"


@needs_database
@needs_models
def test_golden_baseline_exists():
    assert BASELINE_PATH.exists(), (
        f"Golden baseline belum ada di {BASELINE_PATH}. Jalankan "
        "python -m partrisk.cli golden-batch generate sebelum melakukan langkah MOVE."
    )


@needs_database
@needs_models
@pytest.mark.skipif(not BASELINE_PATH.exists(), reason="golden baseline belum ada - lihat test_golden_baseline_exists")
def test_current_batch_matches_golden_baseline(batch):
    from partrisk import cli

    live_path = BASELINE_PATH.parent / "_live_comparison_scratch.parquet"
    cli.generate(live_path)

    q2_columns = {
        "item_id", "failure_probability_30d", "failure_probability_60d",
        "failure_probability_90d", "failure_probability_120d",
        "failure_risk_level", "gate_flagged",
    }
    assert cli.compare(BASELINE_PATH, live_path, columns=q2_columns), (
        "Angka model kerusakan (Q2) BERBEDA dari golden baseline yang direkam SEBELUM "
        "Milestone 1 (penghapusan Survival/Scrap) - lihat detail perbedaan di output di "
        "atas. Hanya kolom Q2 yang dibandingkan di sini karena skema frame SENGAJA "
        "menyusut (kolom Survival/Scrap dihapus), bukan pure-move."
    )
