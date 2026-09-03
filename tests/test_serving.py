from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from partrisk.core import config
from partrisk.core import data_reader
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
from tests.conftest import needs_database, needs_models

DASHBOARD_DIR = Path(__file__).resolve().parent.parent / "dashboard"
if str(DASHBOARD_DIR) not in sys.path:
    sys.path.insert(0, str(DASHBOARD_DIR))


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


def test_terminal_part_summary_mengelompokkan_per_model_dalam_satu_terminal():
    frame = _scored_frame([
        {
            "item_id": "A", "terminal_id": "1", "item_model_code": "0521201",
            "failure_risk_level": "HIGH", "alert_status": "OPEN",
        },
        {
            "item_id": "B", "terminal_id": "1", "item_model_code": "0521201",
            "failure_risk_level": "LOW", "alert_status": pd.NA,
        },
        {
            "item_id": "C", "terminal_id": "1", "item_model_code": "0720301",
            "failure_risk_level": "MEDIUM", "alert_status": pd.NA,
        },
        {"item_id": "D", "terminal_id": "2", "item_model_code": "0521201"},
    ])
    summary = data_state.terminal_part_summary(frame, "1")
    assert len(summary) == 2
    row = summary.loc["0521201"]
    assert row["installed_count"] == 2
    assert row["high_risk_parts"] == 1
    assert row["open_alert_count"] == 1


def test_terminal_part_summary_kosong_kalau_terminal_tidak_ditemukan():
    frame = _scored_frame([{"item_id": "A", "terminal_id": "1", "item_model_code": "0521201"}])
    summary = data_state.terminal_part_summary(frame, "TIDAK-ADA")
    assert summary.empty


def test_filter_scores_part_type_menyaring_model_tertentu():
    frame = _scored_frame([
        {"item_id": "A", "item_model_code": "0521201", "in_official_queue": False},
        {"item_id": "B", "item_model_code": "0720301", "in_official_queue": False},
    ])
    result = data_state.filter_scores(frame, part_type="0521201", official_queue_only=False)
    assert result["item_id"].tolist() == ["A"]


PAGES = [
    DASHBOARD_DIR / "app.py",
    DASHBOARD_DIR / "pages" / "1_Parts.py",
    DASHBOARD_DIR / "pages" / "2_Part_Detail.py",
    DASHBOARD_DIR / "pages" / "3_Inspeksi.py",
    DASHBOARD_DIR / "pages" / "5_Terminal.py",
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
    api_client.terminals.clear()
    api_client.terminal_parts.clear()
    api_client.terminal_part_items.clear()
    yield


@needs_database
@needs_models
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
