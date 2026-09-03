from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from partrisk.core import config
from partrisk.core import data_reader
from partrisk.api import services as gs
from partrisk.api import services as monitoring_service
from partrisk.engines import predict as failure_model
from partrisk.serving import single as explanation
from partrisk.serving import single as predictor
from partrisk.serving import alerts as alert_store
from partrisk.serving import batch as data_state
from partrisk.serving import batch as query_cache
from partrisk.serving.single import (
    PRIORITY_ORDER,
    RISK_LEVELS,
    recommend,
)
from tests.conftest import needs_database, needs_internet, needs_models

DASHBOARD_DIR = Path(__file__).resolve().parent.parent / "dashboard"
if str(DASHBOARD_DIR) not in sys.path:
    sys.path.insert(0, str(DASHBOARD_DIR))

import ui  # noqa: E402


def test_setiap_kelompok_risiko_punya_rekomendasi():
    for failure_level in RISK_LEVELS:
        decision = recommend(failure_level)
        assert decision["priority"] in PRIORITY_ORDER
        assert decision["action"]
        assert decision["message"]
        assert decision["based_on"] == {"failure_risk_level": failure_level}


def test_kelompok_risiko_tidak_dikenal_ditolak():
    with pytest.raises(ValueError):
        recommend("SANGAT_TINGGI")


def test_prioritas_naik_mengikuti_risiko_kerusakan():
    ranks = [
        PRIORITY_ORDER[recommend(failure_level)["priority"]]
        for failure_level in ("LOW", "MEDIUM", "HIGH")
    ]
    assert ranks == sorted(ranks, reverse=True)


def _row(**overrides) -> pd.Series:
    base = {
        "item_model_code_clean": "0521201",
        "days_since_installation": 20.0,
        "total_prior_events": 18,
        "prior_failure_count": 0,
        "prior_failure_365d": 0,
        "prior_corrective_count": 0,
        "prior_corrective_30d": 0,
        "days_since_last_corrective": np.nan,
        "prior_distinct_places": 1,
        "previous_cycle_lifetime_mean": 0.0,
        "has_previous_cycle": False,
        "log_model_failures_90d": 0.0,
        "model_failure_rate_90d": 0.0,
        "log_model_fleet_size": 0.0,
    }
    base.update(overrides)
    return pd.Series(base)


def _codes(row: pd.Series) -> set[str]:
    return {factor["code"] for factor in explanation.risk_factors(row)}


def _label(row: pd.Series, code: str) -> str:
    return next(f["label"] for f in explanation.risk_factors(row) if f["code"] == code)


def test_kerusakan_lama_tidak_terbaca_sebagai_rusak_permanen():
    row = _row(prior_failure_count=1, prior_failure_365d=0)
    label = _label(row, "OLDER_FAILURE_HISTORY")
    assert "sepanjang riwayat" in label
    assert "seumur hidup" not in label


def test_hitungan_korektif_disebut_sebagai_catatan_bukan_pekerjaan():
    row = _row(prior_corrective_30d=4, prior_corrective_count=9,
               days_since_last_corrective=20.0)
    history = _label(row, "CORRECTIVE_HISTORY")
    assert "catatan" in history and "seumur hidup" not in history


def test_kerusakan_baru_dan_lama_tidak_muncul_bersamaan():
    recent = _row(prior_failure_count=3, prior_failure_365d=2)
    assert "RECENT_FAILURE_HISTORY" in _codes(recent)
    assert "OLDER_FAILURE_HISTORY" not in _codes(recent)

    older = _row(prior_failure_count=3, prior_failure_365d=0)
    assert "OLDER_FAILURE_HISTORY" in _codes(older)
    assert "RECENT_FAILURE_HISTORY" not in _codes(older)


def test_belum_pernah_rusak_ditandai_meringankan():
    factors = explanation.risk_factors(_row(prior_failure_count=0))
    no_failure = next(f for f in factors if f["code"] == "NO_FAILURE_HISTORY")
    assert no_failure["direction"] == explanation.MITIGATING


def test_faktor_hanya_muncul_kalau_datanya_ada():
    codes = _codes(_row())
    assert "RECENT_CORRECTIVE_MAINTENANCE" not in codes
    assert "CORRECTIVE_HISTORY" not in codes
    assert "FLEET_CONDITION" not in codes
    assert "LOCATION_CHANGES" not in codes
    assert "PREVIOUS_CYCLE_LIFETIME" not in codes
    assert "INSTALLATION_AGE" in codes


def test_umur_pemasangan_jadi_faktor_risiko_hanya_di_kelompok_tertua():
    muda = explanation.risk_factors(_row(days_since_installation=20.0))
    tua = explanation.risk_factors(
        _row(days_since_installation=float(config.AGE_BAND_THRESHOLDS[-1] + 1))
    )
    assert next(f for f in muda if f["code"] == "INSTALLATION_AGE")["direction"] == (
        explanation.CONTEXT
    )
    assert next(f for f in tua if f["code"] == "INSTALLATION_AGE")["direction"] == (
        explanation.RISK
    )


def test_kondisi_armada_dilaporkan_dengan_jumlah_unit():
    row = _row(
        log_model_failures_90d=float(np.log1p(11)),
        log_model_fleet_size=float(np.log1p(848)),
        model_failure_rate_90d=11 / 848,
    )
    label = _label(row, "FLEET_CONDITION")
    assert "11 kerusakan" in label
    assert "848 unit" in label


def test_caveat_muncul_untuk_model_part_berdukungan_rendah():
    sedikit = explanation.caveats(_row(), {"0521201": 5})
    banyak = explanation.caveats(_row(), {"0521201": config.MIN_PART_MODEL_SUPPORT + 1})
    assert sedikit and "sedikit" in sedikit[0]
    assert banyak == []


def test_catatan_pembacaan_tersedia():
    assert "bukan berarti" in explanation.FAILURE_HISTORY_NOTE
    assert "CATATAN" in explanation.CORRECTIVE_NOTE


@pytest.fixture
def count_connections(monkeypatch):
    original = data_reader.connect
    counter = {"n": 0}

    def counted(*args, **kwargs):
        counter["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(data_reader, "connect", counted)
    return counter


@needs_database
@needs_models
def test_potret_armada_dibuang_saat_data_bertambah(batch):
    data_state.reset()
    data_state.current_data_end()
    failure_model._fleet_snapshot(batch.data_end)
    assert failure_model._FLEET is not None

    data_state._data_end = pd.Timestamp("2000-01-01")
    data_state._checked_at = 0.0
    generation_before = data_state.generation()
    data_state.current_data_end()

    assert failure_model._FLEET is None, "potret armada basi tidak dibuang"
    assert data_state.generation() > generation_before


@needs_database
@needs_models
def test_hasil_batch_ditandai_basi_saat_data_bertambah(batch):
    assert not batch.is_stale(batch.generation)
    assert batch.is_stale(batch.generation + 1)


@needs_database
@needs_models
def test_batas_data_tidak_ditanyakan_ulang_setiap_saat(count_connections):
    data_state.reset()
    data_state.current_data_end()
    first = count_connections["n"]
    for _ in range(5):
        data_state.current_data_end()
    assert count_connections["n"] == first


@needs_database
@needs_models
def test_assessment_tidak_membaca_hal_yang_sama_berulang(
    count_connections, scorable_item
):
    data_state.reset()
    count_connections["n"] = 0
    predictor.get_part_assessment(scorable_item, include_explanation=True)
    assert count_connections["n"] <= 7, (
        f"{count_connections['n']} koneksi untuk satu assessment - "
        "pembacaan berulang tidak tersatukan"
    )


@needs_database
@needs_models
def test_cache_hanya_hidup_di_dalam_scope(scorable_item):
    query_cache.install()
    assert query_cache.reads_in_scope() == 0
    with query_cache.request_scope():
        data_reader.get_events(scorable_item)
        assert query_cache.reads_in_scope() == 1
    assert query_cache.reads_in_scope() == 0


@needs_database
@needs_models
def test_cache_tidak_bertahan_antar_request(scorable_item):
    with query_cache.request_scope():
        data_reader.get_events(scorable_item)
        inside = query_cache.reads_in_scope()
    with query_cache.request_scope():
        assert query_cache.reads_in_scope() == 0
    assert inside == 1


@needs_database
@needs_models
def test_hasil_dengan_dan_tanpa_cache_identik(scorable_item):
    cached = predictor.get_part_assessment(scorable_item, include_explanation=False)

    direct_failure = failure_model.predict(scorable_item)
    for key, value in direct_failure.items():
        assert cached["failure"][key] == value, key


@needs_database
@needs_models
def test_argumen_berbeda_tidak_saling_menimpa(scorable_item, batch):
    with query_cache.request_scope():
        one = data_reader.get_events(scorable_item)
        again = data_reader.get_events(scorable_item)
        assert one is again
        assert set(one["item_identifier_clean"].unique()) == {scorable_item}


@needs_database
@needs_models
def test_penjelasan_dari_batch_sama_dengan_yang_dihitung_langsung(batch, scorable_item):
    from partrisk.serving import single as explanation

    from_batch = predictor._feature_row(scorable_item)
    direct = predictor._active_snapshot(scorable_item).iloc[0]

    for column in explanation.SOURCE_COLUMNS:
        left, right = from_batch[column], direct[column]
        if isinstance(left, float) and pd.isna(left):
            assert pd.isna(right), column
        else:
            assert left == right, f"{column}: batch={left!r} langsung={right!r}"


@needs_database
@needs_models
def test_penjelasan_tidak_memicu_batch_saat_cache_kosong(scorable_item):
    from partrisk.serving import batch as batch_predictor

    saved = batch_predictor._CACHE
    batch_predictor._CACHE = None
    try:
        result = predictor.explain(scorable_item)
        assert result["factors"]
        assert batch_predictor._CACHE is None, "penjelasan satu PART memicu batch penuh"
    finally:
        batch_predictor._CACHE = saved


@pytest.fixture
def clear_alerts():
    alert_store.clear()
    yield
    alert_store.clear()


@pytest.mark.usefixtures("clear_alerts")
def test_alert_terbuka_untuk_part_yang_gate_flagged():
    alert_store.register_flagged(
        pd.Series(["PART-1"]), pd.Series([0.5]), 0.4, "v4",
    )
    frame = alert_store.annotate(pd.DataFrame({"item_id": ["PART-1", "PART-2"]}))

    part1 = frame.set_index("item_id").loc["PART-1"]
    assert part1["alert_status"] == "OPEN"
    assert part1["alert_score_at_open"] == 0.5
    assert part1["alert_threshold_at_open"] == 0.4
    assert part1["alert_model_version"] == "v4"
    assert pd.isna(frame.set_index("item_id").loc["PART-2", "alert_status"])


@pytest.mark.usefixtures("clear_alerts")
def test_alert_tidak_dibuka_ulang_selama_masih_open():
    alert_store.register_flagged(pd.Series(["PART-1"]), pd.Series([0.5]), 0.4, "v4")
    first = alert_store.annotate(pd.DataFrame({"item_id": ["PART-1"]})).iloc[0]

    alert_store.register_flagged(pd.Series(["PART-1"]), pd.Series([0.9]), 0.4, "v5")
    second = alert_store.annotate(pd.DataFrame({"item_id": ["PART-1"]})).iloc[0]

    assert second["alert_opened_at"] == first["alert_opened_at"]
    assert second["alert_score_at_open"] == 0.5
    assert second["alert_model_version"] == "v4"


@pytest.mark.usefixtures("clear_alerts")
def test_resolve_menutup_alert_dan_mengizinkan_alert_baru():
    alert_store.register_flagged(pd.Series(["PART-1"]), pd.Series([0.5]), 0.4, "v4")
    assert alert_store.resolve("PART-1") is True

    resolved = alert_store.annotate(pd.DataFrame({"item_id": ["PART-1"]})).iloc[0]
    assert pd.isna(resolved["alert_status"])

    alert_store.register_flagged(pd.Series(["PART-1"]), pd.Series([0.6]), 0.4, "v4")
    reopened = alert_store.annotate(pd.DataFrame({"item_id": ["PART-1"]})).iloc[0]
    assert reopened["alert_status"] == "OPEN"
    assert reopened["alert_score_at_open"] == 0.6


@pytest.mark.usefixtures("clear_alerts")
def test_resolve_part_tanpa_alert_mengembalikan_false():
    assert alert_store.resolve("PART-TIDAK-ADA-ALERT") is False


@pytest.mark.usefixtures("clear_alerts")
def test_resolve_alert_yang_sudah_diselesaikan_mengembalikan_false():
    alert_store.register_flagged(pd.Series(["PART-1"]), pd.Series([0.5]), 0.4, "v4")
    assert alert_store.resolve("PART-1") is True
    assert alert_store.resolve("PART-1") is False


@pytest.mark.usefixtures("clear_alerts")
def test_open_count_hanya_menghitung_alert_yang_masih_open():
    alert_store.register_flagged(pd.Series(["PART-1", "PART-2"]), pd.Series([0.5, 0.6]), 0.4, "v4")
    assert alert_store.open_count() == 2
    alert_store.resolve("PART-1")
    assert alert_store.open_count() == 1


@pytest.mark.usefixtures("clear_alerts")
def test_open_lead_times_days_kosong_tanpa_alert():
    assert alert_store.open_lead_times_days() == []


@pytest.mark.usefixtures("clear_alerts")
def test_open_lead_times_days_positif_untuk_alert_baru_dibuka():
    alert_store.register_flagged(pd.Series(["PART-1"]), pd.Series([0.5]), 0.4, "v4")
    ages = alert_store.open_lead_times_days()
    assert len(ages) == 1
    assert 0 <= ages[0] < 1


def test_score_distribution_kosong_untuk_array_kosong():
    assert monitoring_service._score_distribution(np.array([])) == {}


def test_score_distribution_urut_naik():
    scores = np.random.default_rng(0).random(500)
    result = monitoring_service._score_distribution(scores)
    assert result["min"] <= result["p05"] <= result["p25"] <= result["median"]
    assert result["median"] <= result["p75"] <= result["p95"] <= result["max"]


def test_unknown_category_share_menghitung_dukungan_rendah():
    snapshot = pd.DataFrame({
        "item_model_code_clean": ["A", "A", "B", None, "C"],
    })
    support = {"A": config.MIN_PART_MODEL_SUPPORT + 100, "B": 5}
    result = monitoring_service._unknown_category_share(snapshot, support)
    assert result["unknown_or_low_support_parts"] == 3
    assert result["unknown_or_low_support_share"] == pytest.approx(0.6)
    assert result["distinct_model_codes_active"] == 3
    assert result["distinct_model_codes_in_training"] == 2


def test_unknown_category_share_semua_dikenal():
    snapshot = pd.DataFrame({"item_model_code_clean": ["A", "A", "B"]})
    support = {"A": 1000, "B": 1000}
    result = monitoring_service._unknown_category_share(snapshot, support)
    assert result["unknown_or_low_support_share"] == 0.0


def test_feature_summary_mengabaikan_kolom_yang_tidak_ada():
    snapshot = pd.DataFrame({"days_since_installation": [10.0, 20.0, np.nan]})
    result = monitoring_service._feature_summary(snapshot)
    assert "days_since_installation" in result
    assert result["days_since_installation"]["missing_share"] == pytest.approx(1 / 3, abs=1e-4)
    assert "prior_failure_count" not in result


def test_feature_summary_kolom_seluruhnya_kosong_tidak_meledak():
    snapshot = pd.DataFrame({"days_since_installation": [np.nan, np.nan]})
    result = monitoring_service._feature_summary(snapshot)
    assert "days_since_installation" not in result


@needs_database
@needs_models
def test_failure_monitoring_memisahkan_offline_dan_live():
    result = monitoring_service.failure_monitoring()
    assert set(result.keys()) == {"offline", "live"}

    offline = result["offline"]
    assert offline["model_version"]
    assert "roc_auc" in offline["test_metrics"]
    assert "pr_auc" in offline["test_metrics"]

    live = result["live"]
    assert live["active_parts"] > 0
    assert live["risk_level_counts"]["HIGH"] >= 0
    assert "roc_auc" not in live
    assert "pr_auc" not in live


@needs_database
@needs_models
def test_jumlah_high_live_dan_expected_konsisten_secara_struktur():
    result = monitoring_service.failure_monitoring()
    live = result["live"]
    total = sum(live["risk_level_counts"].values())
    assert total == live["active_parts"]
    if live["expected_high_from_training"]:

        assert live["high_count_ratio_vs_training"] == pytest.approx(
            live["risk_level_counts"]["HIGH"] / live["expected_high_from_training"], abs=5e-4
        )


@needs_database
@needs_models
def test_endpoint_monitoring_metrics(client):
    response = client.get("/api/v1/monitoring/metrics")
    assert response.status_code == 200
    body = response.json()
    assert "failure" in body


@needs_database
@needs_models
def test_endpoint_monitoring_terpisah_per_model(client):
    failure_only = client.get("/api/v1/monitoring/metrics/failure").json()
    assert "offline" in failure_only and "live" in failure_only


def _terminal_raw(rows: list[dict]) -> pd.DataFrame:
    base = {
        "terminal_serial_code_clean": None,
        "terminal_model_name_clean": None,
        "terminal_inventory_item_id": None,
        "parent_link_quality_status": "VALID_POINT_IN_TIME_RELATION",
    }
    return pd.DataFrame([{**base, **row} for row in rows])


def _scored_frame(rows: list[dict]) -> pd.DataFrame:
    base = {
        "failure_risk_level": "LOW", "tier_score": 0.1,
        f"failure_probability_{config.TARGET_HORIZON_DAYS}d": 0.1,
    }
    return pd.DataFrame([{**base, **row} for row in rows])


def test_attach_terminal_hanya_memakai_status_relasi_yang_bisa_dipercaya():
    frame = _scored_frame([{"item_id": "A"}, {"item_id": "B"}])
    terminal_raw = _terminal_raw([
        {
            "item_identifier_clean": "A", "terminal_inventory_item_id": 1,
            "parent_link_quality_status": "VALID_POINT_IN_TIME_RELATION",
        },
        {
            "item_identifier_clean": "B", "terminal_inventory_item_id": 2,
            "parent_link_quality_status": "PARENT_NOT_TERMINAL",
        },
    ])
    result = data_state._attach_terminal(frame, terminal_raw)
    assert result.set_index("item_id").loc["A", "terminal_id"] == "1"
    assert pd.isna(result.set_index("item_id").loc["B", "terminal_id"])


def test_attach_terminal_ambil_relasi_terbaru_per_part():
    frame = _scored_frame([{"item_id": "A"}])
    terminal_raw = _terminal_raw([
        {"item_identifier_clean": "A", "terminal_inventory_item_id": 1},
        {"item_identifier_clean": "A", "terminal_inventory_item_id": 2},
    ])
    result = data_state._attach_terminal(frame, terminal_raw)
    assert result.loc[0, "terminal_id"] == "2"


def test_attach_terminal_id_berbentuk_string_bersih_tanpa_desimal():
    frame = _scored_frame([{"item_id": "A"}])
    terminal_raw = _terminal_raw([
        {"item_identifier_clean": "A", "terminal_inventory_item_id": 12345},
    ])
    result = data_state._attach_terminal(frame, terminal_raw)
    assert result.loc[0, "terminal_id"] == "12345"


def test_filter_scores_terminal_id_menyaring_part_satu_terminal():
    frame = _scored_frame([
        {"item_id": "A", "terminal_id": "1", "in_official_queue": False},
        {"item_id": "B", "terminal_id": "2", "in_official_queue": False},
    ])
    result = data_state.filter_scores(frame, terminal_id="1", official_queue_only=False)
    assert result["item_id"].tolist() == ["A"]


def test_terminal_overview_menghitung_part_dengan_dan_tanpa_terminal():
    frame = _scored_frame([
        {"item_id": "A", "terminal_id": "1"},
        {"item_id": "B", "terminal_id": "1"},
        {"item_id": "C", "terminal_id": pd.NA},
    ])
    overview = data_state.terminal_overview(frame)
    assert overview == {"terminals": 1, "parts_with_terminal": 2, "parts_without_terminal": 1}


def test_terminal_summary_mengagregasi_tanpa_memaksakan_part_tanpa_terminal():
    prob_column = f"failure_probability_{config.TARGET_HORIZON_DAYS}d"
    frame = _scored_frame([
        {
            "item_id": "A", "terminal_id": "1", "terminal_label": "TRM-1",
            "terminal_model_name": "Model X", "location": "SITE A",
            "failure_risk_level": "HIGH", "tier_score": 0.9,
            prob_column: 0.9,
        },
        {
            "item_id": "B", "terminal_id": "1", "terminal_label": "TRM-1",
            "terminal_model_name": "Model X", "location": "SITE A",
            "failure_risk_level": "LOW", "tier_score": 0.1,
            prob_column: 0.1,
        },
        {"item_id": "C", "terminal_id": pd.NA},
    ])
    summary = data_state.terminal_summary(frame)
    assert len(summary) == 1
    row = summary.loc["1"]
    assert row["active_parts"] == 2
    assert row["high_risk_parts"] == 1
    assert row["top_risk_item_id"] == "A"


def test_terminal_summary_kosong_kalau_tidak_ada_part_dengan_terminal():
    frame = _scored_frame([{"item_id": "A", "terminal_id": pd.NA}])
    summary = data_state.terminal_summary(frame)
    assert summary.empty


@pytest.fixture
def isolated_cache(monkeypatch):

    directory = Path(tempfile.mkdtemp(prefix="geocode_test_"))
    monkeypatch.setattr(gs, "CACHE_PATH", directory / "geocode.json")
    monkeypatch.setattr(gs, "_last_request_at", 0.0)
    yield
    shutil.rmtree(directory, ignore_errors=True)


def _fake_response(payload: list[dict]):
    class _Response:
        def raise_for_status(self):
            pass

        def json(self):
            return payload

    return _Response()


@pytest.mark.usefixtures("isolated_cache")
def test_nama_stasiun_lolos_saringan_pola():
    assert gs._looks_like_public_station("STASIUN JUANDA")
    assert gs._looks_like_public_station("stasiun juanda")
    assert gs._looks_like_public_station("BATU CEPER (KA BANDARA)")


@pytest.mark.usefixtures("isolated_cache")
def test_fasilitas_internal_ditolak_sebelum_ke_jaringan():
    for name in ("GUDANG NI", "SERVICE CENTER", "DIPO DEPOK", "IT KCI JUANDA"):
        assert not gs._looks_like_public_station(name), name


@pytest.mark.usefixtures("isolated_cache")
def test_fasilitas_internal_tidak_pernah_memanggil_nominatim(monkeypatch):
    called = []
    monkeypatch.setattr(gs.requests, "get", lambda *a, **k: called.append(1) or _fake_response([]))

    entry = gs._resolve_one("GUDANG NI")

    assert called == []
    assert entry["resolved"] is False
    assert "bukan nama stasiun publik" in entry["reason"]


@pytest.mark.usefixtures("isolated_cache")
def test_kalimat_pencarian_membuang_akhiran_ka_bandara():
    assert gs._search_query("BATU CEPER (KA BANDARA)") == "Stasiun BATU CEPER"
    assert gs._search_query("STASIUN JUANDA") == "STASIUN JUANDA"


@pytest.mark.usefixtures("isolated_cache")
def test_hasil_di_luar_indonesia_ditolak(monkeypatch):

    bangkok = [{"lat": "13.7563", "lon": "100.5018", "display_name": "Bangkok"}]
    monkeypatch.setattr(gs, "_query_nominatim", lambda name: bangkok)

    entry = gs._resolve_one("STASIUN TIDAK_DIKENAL")

    assert entry["resolved"] is False


@pytest.mark.usefixtures("isolated_cache")
def test_hasil_di_dalam_jabodetabek_diterima(monkeypatch):
    jakarta = [{"lat": "-6.1667", "lon": "106.8305", "display_name": "Juanda, Jakarta"}]
    monkeypatch.setattr(gs, "_query_nominatim", lambda name: jakarta)

    entry = gs._resolve_one("STASIUN JUANDA")

    assert entry["resolved"] is True
    assert entry["lat"] == pytest.approx(-6.1667)


@pytest.mark.usefixtures("isolated_cache")
def test_hasil_di_luar_jabodetabek_tapi_dalam_indonesia_diterima(monkeypatch):

    medan = [{"lat": "3.5952", "lon": "98.6722", "display_name": "Binjai, Sumatera Utara"}]
    monkeypatch.setattr(gs, "_query_nominatim", lambda name: medan)

    entry = gs._resolve_one("STASIUN BINJAI")

    assert entry["resolved"] is True
    assert entry["lat"] == pytest.approx(3.5952)
    assert entry["lon"] == pytest.approx(98.6722)


@pytest.mark.usefixtures("isolated_cache")
def test_kandidat_pertama_di_luar_kotak_kandidat_kedua_di_dalam(monkeypatch):
    candidates = [
        {"lat": "13.7563", "lon": "100.5018", "display_name": "Salah, Bangkok"},
        {"lat": "-6.1667", "lon": "106.8305", "display_name": "Benar, Jakarta"},
    ]
    monkeypatch.setattr(gs, "_query_nominatim", lambda name: candidates)

    entry = gs._resolve_one("STASIUN JUANDA")

    assert entry["resolved"] is True
    assert "Benar" in entry["matched_name"]


@pytest.mark.usefixtures("isolated_cache")
def test_tidak_ada_kandidat_sama_sekali(monkeypatch):
    monkeypatch.setattr(gs, "_query_nominatim", lambda name: [])
    entry = gs._resolve_one("STASIUN JUANDA")
    assert entry["resolved"] is False
    assert entry["retry"] is False


@pytest.mark.usefixtures("isolated_cache")
def test_kegagalan_jaringan_ditandai_boleh_dicoba_lagi(monkeypatch):
    import requests

    def boom(name):
        raise requests.RequestException("timeout")

    monkeypatch.setattr(gs, "_query_nominatim", boom)
    entry = gs._resolve_one("STASIUN JUANDA")
    assert entry["resolved"] is False
    assert entry["retry"] is True


@pytest.mark.usefixtures("isolated_cache")
def test_lokasi_yang_sudah_di_cache_tidak_dicoba_lagi(monkeypatch):
    calls = []
    monkeypatch.setattr(
        gs, "_resolve_one",
        lambda name: (calls.append(name), {"resolved": True, "lat": 0.0, "lon": 0.0})[1],
    )

    gs.resolve_missing(["STASIUN A"], budget_seconds=10)
    assert calls == ["STASIUN A"]

    gs.resolve_missing(["STASIUN A"], budget_seconds=10)
    assert calls == ["STASIUN A"], "lokasi yang sudah berhasil di-cache dipanggil lagi"


@pytest.mark.usefixtures("isolated_cache")
def test_kegagalan_boleh_dicoba_lagi_lain_kali(monkeypatch):
    monkeypatch.setattr(gs, "_resolve_one", lambda name: {"resolved": False, "retry": True})

    gs.resolve_missing(["STASIUN GAGAL"], budget_seconds=10)
    gs.resolve_missing(["STASIUN GAGAL"], budget_seconds=10)

    entry = gs.known_coordinates(["STASIUN GAGAL"])["STASIUN GAGAL"]
    assert entry["retry"] is True


@pytest.mark.usefixtures("isolated_cache")
def test_anggaran_waktu_menghentikan_lebih_awal(monkeypatch):
    import time as time_module

    def slow_resolve(name):
        time_module.sleep(0.05)
        return {"resolved": True, "lat": 0.0, "lon": 0.0}

    monkeypatch.setattr(gs, "_resolve_one", slow_resolve)

    processed = gs.resolve_missing(
        [f"STASIUN {i}" for i in range(100)], budget_seconds=0.12
    )
    assert 0 < processed < 100


@pytest.mark.usefixtures("isolated_cache")
def test_koordinat_yang_belum_pernah_dicoba_kembalikan_none():
    result = gs.known_coordinates(["STASIUN BELUM_PERNAH_DICOBA"])
    assert result["STASIUN BELUM_PERNAH_DICOBA"] is None


def test_lokasi_dengan_risiko_tinggi_berwarna_merah():
    assert ui.risk_marker_color(high_risk_parts=3, medium_risk_parts=5) == ui.MAP_HIGH_COLOR


def test_lokasi_hanya_risiko_sedang_berwarna_oranye():
    assert ui.risk_marker_color(high_risk_parts=0, medium_risk_parts=2) == ui.MAP_MEDIUM_COLOR


def test_lokasi_tanpa_risiko_tinggi_sedang_berwarna_biru():
    assert ui.risk_marker_color(high_risk_parts=0, medium_risk_parts=0) == ui.MAP_LOW_COLOR


def test_risiko_tinggi_menang_atas_risiko_sedang():
    assert ui.risk_marker_color(high_risk_parts=1, medium_risk_parts=10) == ui.MAP_HIGH_COLOR


def test_radius_naik_mengikuti_jumlah_risiko_tinggi():
    small = ui.risk_marker_radius(high_risk_parts=0)
    big = ui.risk_marker_radius(high_risk_parts=10)
    assert big > small


def test_radius_selalu_positif_walau_tanpa_risiko():
    assert ui.risk_marker_radius(high_risk_parts=0) > 0


def test_warna_adalah_rgba_valid():
    for color in (ui.MAP_HIGH_COLOR, ui.MAP_MEDIUM_COLOR, ui.MAP_LOW_COLOR):
        assert len(color) == 4
        assert all(0 <= channel <= 255 for channel in color)


PAGES = [
    DASHBOARD_DIR / "app.py",
    DASHBOARD_DIR / "pages" / "1_Parts.py",
    DASHBOARD_DIR / "pages" / "2_Part_Detail.py",
    DASHBOARD_DIR / "pages" / "3_Inspeksi.py",
    DASHBOARD_DIR / "pages" / "5_Terminal.py",
    DASHBOARD_DIR / "pages" / "6_Sistem.py",
]


@pytest.fixture(scope="module")
def api_url(client) -> str:
    return "testclient"


@pytest.fixture
def route_dashboard_to_testclient(monkeypatch, client):
    import sys

    sys.path.insert(0, str(DASHBOARD_DIR))
    import api_client

    def fake_get(path, params=None):
        response = client.get(path, params=params)
        content_type = response.headers.get("content-type", "")
        try:
            body = response.json() if content_type.startswith("application/json") else None
        except ValueError:
            body = None
        if response.status_code == 404 and isinstance(body, dict) and body.get("status") == "NOT_FOUND":
            return body
        if response.status_code >= 400:
            message = (body.get("message") or body.get("detail")) if isinstance(body, dict) else None
            raise api_client.ApiError(message or f"API menjawab {response.status_code}.")
        if body is None:
            raise api_client.ApiError("API mengembalikan respons yang tidak bisa dibaca (bukan JSON).")
        return body

    monkeypatch.setattr(api_client, "_get", fake_get)
    api_client.health.clear()
    api_client.overview.clear()
    api_client.filters.clear()
    api_client.recommendations.clear()
    api_client.assessment.clear()
    api_client.history.clear()
    api_client.locations_map.clear()
    api_client.model_info.clear()
    api_client.monitoring_metrics.clear()
    api_client.terminals.clear()
    yield


@needs_database
@needs_models
@needs_internet
@pytest.mark.usefixtures("route_dashboard_to_testclient")
@pytest.mark.parametrize("page", PAGES, ids=lambda path: path.stem)
def test_halaman_bisa_dirender(page):
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(str(page), default_timeout=300)
    app.session_state["authenticated"] = True
    app = app.run()
    assert not app.exception, [error.value for error in app.exception]
    assert not app.error, [box.value for box in app.error]
    assert app.title, "halaman tidak menampilkan judul"


@needs_database
@needs_models
@needs_internet
@pytest.mark.usefixtures("route_dashboard_to_testclient")
def test_detail_part_menampilkan_angka(scorable_item):
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(
        str(DASHBOARD_DIR / "pages" / "2_Part_Detail.py"), default_timeout=300
    )
    app.session_state["authenticated"] = True
    app = app.run()
    app.text_input[0].set_value(scorable_item).run()

    assert not app.exception, [error.value for error in app.exception]
    labels = [metric.label for metric in app.metric]
    assert "Rusak dalam 30 hari" in labels
    assert "Rusak dalam 120 hari" in labels
    assert any("KENAPA DIPRIORITASKAN" in md.value for md in app.markdown)


@needs_database
@needs_models
@needs_internet
@pytest.mark.usefixtures("route_dashboard_to_testclient")
def test_filter_lokasi_terisi_dari_map_location_filter_dan_bertahan_setelah_rerun_lain():
    """`st.session_state["map_location_filter"]` (diisi oleh tab Peta di halaman
    Inspeksi) mengisi filter Lokasi di halaman Parts, dan filter itu harus
    tetap bertahan walau ada rerun lain (mis. ganti Horizon) - sebelumnya
    selectbox tanpa `key` reset ke 'Semua' pada rerun berikutnya karena
    `map_location_filter` sudah di-pop pada rerun pertama."""
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(str(DASHBOARD_DIR / "pages" / "1_Parts.py"), default_timeout=300)
    app.session_state["authenticated"] = True
    app = app.run()
    assert not app.exception

    location_box = next((box for box in app.selectbox if box.label == "Lokasi"), None)
    if location_box is None or len(location_box.options) <= 1:
        pytest.skip("tidak ada data lokasi untuk diuji")
    available_location = location_box.options[1]

    app.session_state["map_location_filter"] = available_location
    app = app.run()
    location_box = next(box for box in app.selectbox if box.label == "Lokasi")
    assert location_box.value == available_location
    assert "map_location_filter" not in app.session_state

    official_toggle = next(
        box for box in app.toggle if box.label == "Antrian dengan risiko tinggi"
    )
    app = official_toggle.set_value(not official_toggle.value).run()

    location_box = next(box for box in app.selectbox if box.label == "Lokasi")
    assert location_box.value == available_location, (
        "filter lokasi tidak seharusnya reset ke 'Semua' hanya karena widget lain berubah"
    )


@needs_database
@needs_models
@needs_internet
@pytest.mark.usefixtures("route_dashboard_to_testclient")
def test_detail_part_menjelaskan_yang_tidak_bisa_diskor(not_scorable_item):
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(
        str(DASHBOARD_DIR / "pages" / "2_Part_Detail.py"), default_timeout=300
    )
    app.session_state["authenticated"] = True
    app = app.run()
    app.text_input[0].set_value(not_scorable_item).run()

    assert not app.exception, [error.value for error in app.exception]
    assert app.warning, "tidak ada keterangan kenapa PART tidak bisa dinilai"
    assert not app.metric, "menampilkan angka untuk PART yang tidak bisa diskor"
